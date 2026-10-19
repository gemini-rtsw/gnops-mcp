"""Read-only Channel Access client (caget / camonitor) built on pyepics.

There is deliberately no put: nothing in this module or the server writes to
a PV. CA settings come from the usual environment variables
(EPICS_CA_ADDR_LIST, EPICS_CA_AUTO_ADDR_LIST, EPICS_CA_MAX_ARRAY_BYTES).

These functions block, so the server runs them in worker threads; each one
attaches to pyepics' shared CA context first.
"""

import threading
import time
from datetime import datetime, timezone

import epics
from epics import ca

MAX_PVS = 50
MAX_ELEMENTS = 100  # arrays are truncated to this many elements

SEVERITY = {0: "NO_ALARM", 1: "MINOR", 2: "MAJOR", 3: "INVALID"}


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _plain(value):
    """Make a CA value JSON-friendly: numpy scalars/arrays to Python, long arrays truncated."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if isinstance(value, list) and len(value) > MAX_ELEMENTS:
        return {"length": len(value), "first": value[:MAX_ELEMENTS]}
    return value


def _label(pv_type: str | None, char_value):
    """String form only where it adds information: enum labels, strings, char arrays.

    For numbers pyepics rounds to the PV's PREC, which can hide the real value.
    """
    if pv_type and any(t in pv_type for t in ("enum", "string", "char")):
        return char_value
    return None


def _connect(names: list[str], timeout: float, **kw) -> list[epics.PV]:
    ca.use_initial_context()
    pvs = [epics.PV(n, form="time", **kw) for n in names]
    deadline = time.monotonic() + timeout
    for pv in pvs:
        pv.wait_for_connection(timeout=max(0.0, deadline - time.monotonic()))
    return pvs


def get(names: list[str], timeout: float) -> list[dict]:
    names = list(dict.fromkeys(names))
    pvs = _connect(names, timeout, auto_monitor=False)
    out = []
    try:
        for pv in pvs:
            if not pv.connected:
                out.append({"pv": pv.pvname, "error": f"not connected after {timeout:g}s"})
                continue
            value = pv.get(timeout=timeout, use_monitor=False)
            if value is None:
                out.append({"pv": pv.pvname, "error": "connected but get timed out"})
                continue
            ctrl = pv.get_ctrlvars(timeout=timeout) or {}
            out.append({
                "pv": pv.pvname,
                "value": _plain(value),
                "string": _label(pv.type, pv.char_value),
                "units": ctrl.get("units") or None,
                "severity": SEVERITY.get(pv.severity, pv.severity),
                "status": pv.status,
                "timestamp": _iso(pv.timestamp),
                "type": pv.type,
                "count": pv.count,
                "host": pv.host,
            })
    finally:
        for pv in pvs:
            pv.disconnect()
    return out


def monitor(names: list[str], seconds: float, max_updates: int) -> list[dict]:
    names = list(dict.fromkeys(names))
    lock = threading.Lock()
    updates: dict[str, list] = {n: [] for n in names}
    dropped: dict[str, int] = {n: 0 for n in names}

    def on_change(pvname=None, value=None, char_value=None, timestamp=None, severity=None, type=None, **_):
        with lock:
            if len(updates[pvname]) < max_updates:
                updates[pvname].append([_iso(timestamp), _plain(value), _label(type, char_value), SEVERITY.get(severity, severity)])
            else:
                dropped[pvname] += 1

    start = time.monotonic()
    pvs = _connect(names, min(seconds, 5.0), auto_monitor=True, callback=on_change)
    try:
        time.sleep(max(0.0, seconds - (time.monotonic() - start)))
        connected = {pv.pvname: pv.connected for pv in pvs}
    finally:
        for pv in pvs:
            pv.clear_callbacks()
            pv.disconnect()

    with lock:
        return [
            {
                "pv": pv.pvname,
                "error": None if connected[pv.pvname] or updates[pv.pvname] else "never connected",
                "count": len(updates[pv.pvname]),
                "truncated": dropped[pv.pvname] > 0,
                "dropped": dropped[pv.pvname],
                "updates": updates[pv.pvname],
            }
            for pv in pvs
        ]
