"""Thin client for the TED Grafana front end.

All data access goes through Grafana (the same APIs the dashboards use), so the
MCP server never talks to the archiver or Elasticsearch directly.

Config (environment variables):
  GRAFANA_URL        default http://mkoteddev-lv1:3000
  GRAFANA_PUBLIC_URL Grafana as a browser reaches it, for grafana_link (default GRAFANA_URL)
  TED_SITE           MK or CP, the Telemetry dashboard's site (default MK)
  TELEMETRY_DASH_UID, LOGS_DASH_UID  dashboards grafana_link points at
  GRAFANA_TOKEN      optional service-account token; anonymous access if unset
  ARCHIVER_DS_UID    Grafana UID of the ArchiverAppliance datasource
  LOGS_DS_UID        Grafana UID of the Elasticsearch (logs-*) datasource
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from limits import slot

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://mkoteddev-lv1:3000").rstrip("/")
GRAFANA_PUBLIC_URL = (os.environ.get("GRAFANA_PUBLIC_URL") or GRAFANA_URL).rstrip("/")
GRAFANA_TOKEN = os.environ.get("GRAFANA_TOKEN")
TED_SITE = os.environ.get("TED_SITE") or "MK"
ARCHIVER_DS = {
    "type": "sasaki77-archiverappliance-datasource",
    "uid": os.environ.get("ARCHIVER_DS_UID", "cesb7dv10h3i8b"),
}
LOGS_DS = {
    "type": "elasticsearch",
    "uid": os.environ.get("LOGS_DS_UID", "F1zgMdwVz"),
}


def _client() -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {GRAFANA_TOKEN}"} if GRAFANA_TOKEN else {}
    return httpx.AsyncClient(base_url=GRAFANA_URL, headers=headers, timeout=60)


_UNIT_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def to_epoch_ms(t: str) -> int:
    """Parse "now", "now-6h" (units s/m/h/d/w) or ISO 8601 (UTC if no zone) to epoch ms."""
    t = t.strip()
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    if t == "now":
        return now
    if t.startswith("now-") and t[-1] in _UNIT_MS and t[4:-1].isdigit():
        return now - int(t[4:-1]) * _UNIT_MS[t[-1]]
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"bad time {t!r}: use now, now-<N><s|m|h|d|w>, or ISO 8601") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _check(r: httpx.Response) -> None:
    """Turn Grafana HTTP errors into messages the model can read and act on."""
    if r.is_error:
        raise ToolError(f"Grafana {r.request.method} {r.request.url.path} -> HTTP {r.status_code}: {r.text[:300]}")


async def ds_query(queries: list[dict], start_ms: int, end_ms: int) -> dict:
    """POST /api/ds/query, the endpoint Grafana panels use. Returns results keyed by refId."""
    body = {"from": str(start_ms), "to": str(end_ms), "queries": queries}
    async with slot(), _client() as c:
        r = await c.post("/api/ds/query", json=body)
    # On a per-query failure Grafana answers 400 but still returns every query's
    # result, so keep the good ones and let the caller report the bad ones.
    if r.status_code == 400 and "results" in r.text:
        return r.json()["results"]
    _check(r)
    return r.json()["results"]


async def archiver_get(path: str, params: dict) -> object:
    """GET an Archiver Appliance retrieval endpoint through Grafana's datasource proxy."""
    async with slot(), _client() as c:
        r = await c.get(f"/api/datasources/proxy/uid/{ARCHIVER_DS['uid']}/{path}", params=params)
    _check(r)
    return r.json()


# Dashboards a Viewer (anonymous) can open; Explore is editor-only on TED.
TELEMETRY_DASH = os.environ.get("TELEMETRY_DASH_UID", "642d6862-e9a1-461d-9a06-85209807bc47")
LOGS_DASH = os.environ.get("LOGS_DASH_UID", "e5760f92-16d7-4175-8251-4b65f2413639")


def dashboard_url(uid: str, variables: list[tuple[str, str]], start_ms: int, end_ms: int) -> str:
    """Link to a dashboard with its variables set and an absolute time range."""
    params = [("orgId", 1)] + [(f"var-{k}", v) for k, v in variables] + [("from", start_ms), ("to", end_ms)]
    return f"{GRAFANA_PUBLIC_URL}/d/{uid}?{urlencode(params)}"
