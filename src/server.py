"""gnops-mcp: read-only MCP server for Gemini North operations data.

Tools:
  TED telemetry   search_pvs, get_pv_data      (Archiver Appliance, via Grafana)
  TED logs        list_log_systems, search_logs (Elasticsearch, via Grafana)
  Browser links   grafana_link                  (TED dashboard URL for the same data)
  Live EPICS      caget, camonitor              (Channel Access; no put)

At most MAX_CONCURRENT (default 5) Grafana requests and CA calls run at once,
across all clients (limits.py).

Two ways to run it:
  python server.py          stdio: the client launches this process and talks
                            JSON-RPC on stdin/stdout (no network port)
  python server.py --http   Streamable HTTP on $MCP_HOST:$MCP_PORT (default
                            0.0.0.0:8001), endpoint /mcp; this is how the
                            container runs
"""

import argparse
import asyncio
import os
import platform
import socket
import time
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

import grafana
import ca
from limits import slot

mcp = MCPServer("gnops-mcp")


@mcp.tool()
def server_info() -> dict:
    """Report basic facts about the host the MCP server is running on."""
    with open("/proc/uptime") as f:
        uptime_s = float(f.read().split()[0])
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "user": os.environ.get("USER", "?"),
        "uptime_hours": round(uptime_s / 3600, 1),
        "load_avg": os.getloadavg(),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


@mcp.tool()
async def search_pvs(pattern: str, limit: int = 200) -> list[str]:
    """Find archived EPICS PV names matching a glob pattern, e.g. "ws:*" or "*Temp*".

    Use this to discover exact PV names before calling get_pv_data.
    """
    return await grafana.archiver_get("bpl/getMatchingPVs", {"pv": pattern, "limit": min(limit, 2500)})


ArchiverOperator = Literal["mean", "min", "max", "median", "std", "count", "first", "last", "firstFill", "lastFill", "raw"]


def _archiver_error(err: str | None) -> str | None:
    if err and "received=404" in err:
        return "PV not found in the archive (check the name with search_pvs)"
    return err


def _archiver_queries(pvs: list[str], operator: str, max_points: int, start_ms: int, end_ms: int):
    """Grafana queries for archived PVs; returns (bin seconds, queries)."""
    bin_s = max(1, (end_ms - start_ms) // 1000 // max_points)
    functions = []
    if operator != "raw":
        functions.append({
            "def": {"name": "binInterval", "category": "Options", "defaultParams": ["900"],
                    "params": [{"name": "interval", "type": "int"}]},
            "params": [str(bin_s)],
        })
    queries = [
        {"refId": pv, "datasource": grafana.ARCHIVER_DS, "target": pv, "regex": False,
         "operator": operator, "functions": functions, "maxDataPoints": max_points}
        for pv in pvs
    ]
    return bin_s, queries


@mcp.tool()
async def get_pv_data(
    pvs: list[str],
    start: str = "now-1h",
    end: str = "now",
    operator: ArchiverOperator = "mean",
    max_points: int = 200,
) -> list[dict]:
    """Fetch archived time series for up to 10 PVs from the Archiver Appliance (via Grafana).

    start/end: "now", "now-30m", "now-6h", "now-7d", or ISO 8601 (UTC unless a zone is given).
    operator: how samples are combined per time bin: mean (default), min, max, median,
      std, count, first, last, firstFill, lastFill, or raw (no binning; truncated to max_points).
      A raw query may include the last sample before start (the value in effect at start).
    max_points: bins per PV (max 2000). Wider time range = coarser bins.

    Returns per PV: summary stats over the returned points, the bin size, and
    points as [ISO time UTC, value]. For a browser plot of the same data, call
    grafana_link with the same arguments.
    """
    if not pvs or len(pvs) > 10:
        raise ToolError("pass 1-10 PV names (use search_pvs to find them)")
    max_points = max(1, min(max_points, 2000))
    start_ms, end_ms = grafana.to_epoch_ms(start), grafana.to_epoch_ms(end)
    if end_ms <= start_ms:
        raise ToolError("end must be after start")
    bin_s, queries = _archiver_queries(pvs, operator, max_points, start_ms, end_ms)
    results = await grafana.ds_query(queries, start_ms, end_ms)

    out = []
    for pv in pvs:
        res = results.get(pv, {})
        frames = res.get("frames") or []
        values = frames[0]["data"]["values"] if frames and frames[0]["data"]["values"] else [[], []]
        points = [[grafana.iso(t), v] for t, v in zip(values[0], values[1])]
        # Binned queries can return one extra edge bin; only raw needs a hard cap.
        truncated = operator == "raw" and len(points) > max_points
        if truncated:
            points = points[:max_points]
        nums = [v for _, v in points if isinstance(v, (int, float))]
        out.append({
            "pv": pv,
            "error": _archiver_error(res.get("error")),
            "operator": operator,
            "bin_seconds": None if operator == "raw" else bin_s,
            "count": len(points),
            "truncated": truncated,
            "min": min(nums) if nums else None,
            "max": max(nums) if nums else None,
            "mean": round(sum(nums) / len(nums), 6) if nums else None,
            "first": points[0] if points else None,
            "last": points[-1] if points else None,
            "points": points,
        })
    return out


def _logs_query(lucene: str, metrics: list[dict], bucket_aggs: list[dict], start_ms: int, end_ms: int) -> dict:
    # Grafana also builds a log-volume histogram from intervalMs; without it
    # Elasticsearch rejects the query for creating too many buckets.
    interval_ms = max(1000, (end_ms - start_ms) // 100)
    return {"refId": "A", "datasource": grafana.LOGS_DS, "query": lucene, "metrics": metrics,
            "bucketAggs": bucket_aggs, "timeField": "@timestamp",
            "intervalMs": interval_ms, "maxDataPoints": 100}


def _frame_columns(result: dict) -> dict[str, list]:
    if result.get("error"):
        raise ToolError(f"log query failed: {result['error'][:300]}")
    frames = result.get("frames") or []
    if not frames:
        return {}
    f = frames[0]
    return {fld["name"]: col for fld, col in zip(f["schema"]["fields"], f["data"]["values"])}


def _ci_regex(text: str) -> str:
    """Case-insensitive 'contains' as a Lucene regex on the msg keyword field."""
    out = []
    for ch in text:
        if ch.isalpha():
            out.append(f"[{ch.lower()}{ch.upper()}]")
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append("\\" + ch)
    return f"msg:/.*{''.join(out)}.*/"


def _log_lucene(text: str | None, systems: list[str] | None, lucene: str | None) -> str:
    clauses = []
    if systems:
        bad = [s for s in systems if not s.replace("-", "").replace("_", "").isalnum()]
        if bad:
            raise ToolError(f"invalid system name(s): {bad}")
        clauses.append("system:(" + " OR ".join(systems) + ")")
    if text:
        clauses.append(_ci_regex(text))
    if lucene:
        clauses.append(f"({lucene})")
    return " AND ".join(clauses)


@mcp.tool()
async def list_log_systems(start: str = "now-7d", end: str = "now") -> list[dict]:
    """List the systems that have logs in TED (e.g. mcs, ecs, ag, seqexec, pwfs1) with line counts.

    Use this to find valid system names for search_logs.
    """
    start_ms, end_ms = grafana.to_epoch_ms(start), grafana.to_epoch_ms(end)
    terms = {"type": "terms", "id": "2", "field": "system",
             "settings": {"size": "100", "order": "desc", "orderBy": "_count", "min_doc_count": "1"}}
    q = _logs_query("", [{"type": "count", "id": "1"}], [terms], start_ms, end_ms)
    cols = _frame_columns((await grafana.ds_query([q], start_ms, end_ms))["A"])
    return [{"system": s, "lines": n} for s, n in zip(cols.get("system", []), cols.get("Count", []))]


@mcp.tool()
async def search_logs(
    text: str | None = None,
    systems: list[str] | None = None,
    start: str = "now-1h",
    end: str = "now",
    limit: int = 100,
    lucene: str | None = None,
) -> dict:
    """Search TED system logs (Elasticsearch, via Grafana). Newest lines first.

    text: case-insensitive substring to find in the message, e.g. "tracking off".
    systems: restrict to these systems, e.g. ["mcs", "ecs"] (see list_log_systems).
    start/end: "now", "now-30m", "now-6h", "now-7d", or ISO 8601 (UTC unless a zone is given).
    limit: max lines returned (max 1000).
    lucene: optional extra Lucene query ANDed in, for advanced filters on fields
      msg, system, location, log.file.path.

    Returns lines as "<UTC time> [system] message"; truncated=true means more
    lines matched than limit, so narrow the time range or filter. For a browser
    view of the same lines, call grafana_link with the same arguments.
    """
    start_ms, end_ms = grafana.to_epoch_ms(start), grafana.to_epoch_ms(end)
    if end_ms <= start_ms:
        raise ToolError("end must be after start")
    limit = max(1, min(limit, 1000))
    query = _log_lucene(text, systems, lucene)
    q = _logs_query(query, [{"type": "logs", "id": "1", "settings": {"limit": str(limit + 1)}}],
                    [], start_ms, end_ms)
    cols = _frame_columns((await grafana.ds_query([q], start_ms, end_ms))["A"])
    times, systems_col, msgs = cols.get("@timestamp", []), cols.get("system", []), cols.get("msg", [])
    lines = [f"{grafana.iso(t)} [{s}] {m}" for t, s, m in zip(times, systems_col, msgs)]
    return {
        "query": query or "*",
        "from": grafana.iso(start_ms),
        "to": grafana.iso(end_ms),
        "count": min(len(lines), limit),
        "truncated": len(lines) > limit,
        "lines": lines[:limit],
    }


# The Telemetry dashboard only offers these; map the rest to the closest.
_DASH_OPERATOR = {"mean": "mean", "median": "median", "firstFill": "firstFill",
                  "min": "mean", "max": "mean", "std": "mean", "count": "mean",
                  "first": "firstFill", "last": "firstFill", "lastFill": "firstFill", "raw": "firstFill"}


@mcp.tool()
async def grafana_link(
    pvs: list[str] | None = None,
    operator: ArchiverOperator = "mean",
    text: str | None = None,
    systems: list[str] | None = None,
    lucene: str | None = None,
    start: str = "now-1h",
    end: str = "now",
) -> dict:
    """Browser link to the TED Grafana dashboard showing the same data as a get_pv_data or search_logs call.

    Pass the same arguments you used for that call:
      telemetry: pvs (+ operator), start, end -> Telemetry dashboard plot
      logs: systems (required, at least one) + text / lucene, start, end -> Logs dashboard
    The time range is pinned to absolute times, so the link keeps showing the
    same window later. Give the user the url to paste into a browser.

    The Telemetry dashboard bins with mean, median or firstFill only (no raw);
    other operators map to the closest (shown as `operator`). For single-count
    steps or short events, keep the window short so the bins stay small.
    """
    start_ms, end_ms = grafana.to_epoch_ms(start), grafana.to_epoch_ms(end)
    if end_ms <= start_ms:
        raise ToolError("end must be after start")
    if pvs and (text or systems or lucene):
        raise ToolError("pass either pvs (telemetry) or systems/text/lucene (logs), not both")
    if pvs:
        if len(pvs) > 10:
            raise ToolError("pass 1-10 PV names")
        shown = _DASH_OPERATOR[operator]
        variables = [("site", grafana.TED_SITE)] + [("pv", pv) for pv in pvs] + [("ppType", shown)]
        out = {"kind": "telemetry", "operator": shown, "pvs": pvs}
        uid = grafana.TELEMETRY_DASH
    else:
        if not systems:
            raise ToolError("the Logs dashboard needs at least one system (see list_log_systems)")
        query = _log_lucene(text, None, lucene) or "msg:**"
        _log_lucene(None, systems, None)  # validates the names
        variables = [("system", s) for s in systems] + [("lucene_query", query)]
        out = {"kind": "logs", "systems": systems, "query": query}
        uid = grafana.LOGS_DASH
    out.update({
        "from": grafana.iso(start_ms),
        "to": grafana.iso(end_ms),
        "url": grafana.dashboard_url(uid, variables, start_ms, end_ms),
    })
    return out


@mcp.tool()
async def caget(pvs: list[str], timeout: float = 3.0) -> list[dict]:
    """Read the current value of up to 50 live EPICS PVs over Channel Access.

    Read-only: this server has no way to write (caput) to a PV.
    Returns per PV: value, string form (enum labels, strings and char arrays only), units, alarm
    severity/status, IOC timestamp (ISO UTC), host, and an error if the PV did
    not connect within timeout seconds (max 10).
    """
    if not pvs or len(pvs) > ca.MAX_PVS:
        raise ToolError(f"pass 1-{ca.MAX_PVS} PV names")
    async with slot():
        return await asyncio.to_thread(ca.get, pvs, max(0.5, min(timeout, 10.0)))


@mcp.tool()
async def camonitor(pvs: list[str], seconds: float = 10.0, max_updates: int = 500) -> list[dict]:
    """Watch up to 50 live EPICS PVs for a while and return every value change.

    Read-only, like caget. Subscribes for `seconds` (max 60), then unsubscribes.
    The first update per PV is its value at subscribe time. Returns per PV the
    updates as [ISO time UTC, value, string form or null, severity], capped at
    max_updates (max 2000) per PV.
    """
    if not pvs or len(pvs) > ca.MAX_PVS:
        raise ToolError(f"pass 1-{ca.MAX_PVS} PV names")
    seconds = max(1.0, min(seconds, 60.0))
    max_updates = max(1, min(max_updates, 2000))
    async with slot():
        return await asyncio.to_thread(ca.monitor, pvs, seconds, max_updates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8001")))
    args = parser.parse_args()

    if not args.http:
        mcp.run()
    else:
        # Only accept requests addressed to this machine by name (DNS-rebinding guard).
        # MCP_ALLOWED_HOSTS adds names (e.g. a DNS alias) to the host's FQDN.
        names = [socket.getfqdn(), socket.gethostname(), "localhost", "127.0.0.1"]
        names += os.environ.get("MCP_ALLOWED_HOSTS", "").split()
        names = list(dict.fromkeys(n for n in names if n))
        security = TransportSecuritySettings(
            allowed_hosts=[f"{n}:*" for n in names],
            allowed_origins=[f"http://{n}:*" for n in names],
        )
        mcp.run("streamable-http", host=args.host, port=args.port, transport_security=security)
