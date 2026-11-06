"""Limits on calls to TED Grafana and Channel Access.

Every Grafana request and every caget/camonitor takes a slot, so however many
clients are connected, at most MAX_CONCURRENT of these run at once. A call that
can't get a slot within QUEUE_WAIT_S seconds fails with "server busy" rather
than piling up.

Channel Access also has a rate limit: caget and camonitor together may touch at
most CA_MAX_PVS_PER_MIN PVs in any 60 s, across all clients. Each PV in a call
costs a CA search broadcast and a connection to its IOC, so a session calling
caget in a loop would otherwise load the network and the IOCs. A call over the
limit fails at once (no queueing) with a message saying why and when to retry,
and the refusal is logged.

Config (environment variables):
  MAX_CONCURRENT      default 5
  QUEUE_WAIT_S        default 30
  CA_MAX_PVS_PER_MIN  default 200
"""

import asyncio
import os
import sys
import time
from collections import deque
from contextlib import asynccontextmanager

from mcp.server.mcpserver.exceptions import ToolError

MAX_CONCURRENT = max(1, int(os.environ.get("MAX_CONCURRENT") or 5))
QUEUE_WAIT_S = float(os.environ.get("QUEUE_WAIT_S") or 30)
CA_MAX_PVS_PER_MIN = max(1, int(os.environ.get("CA_MAX_PVS_PER_MIN") or 200))

_slots = asyncio.Semaphore(MAX_CONCURRENT)


@asynccontextmanager
async def slot():
    try:
        await asyncio.wait_for(_slots.acquire(), QUEUE_WAIT_S)
    except asyncio.TimeoutError:
        raise ToolError(f"server busy: {MAX_CONCURRENT} calls already running; try again shortly") from None
    try:
        yield
    finally:
        _slots.release()


_ca_calls: deque[tuple[float, int]] = deque()   # (time, PVs) of CA calls in the last 60 s


def ca_budget(tool: str, n: int) -> None:
    """Charge n PVs to the CA rate limit, or refuse the call and say why."""
    now = time.monotonic()
    while _ca_calls and now - _ca_calls[0][0] >= 60:
        _ca_calls.popleft()
    used = sum(k for _, k in _ca_calls)
    if used + n <= CA_MAX_PVS_PER_MIN:
        _ca_calls.append((now, n))
        return
    if n > CA_MAX_PVS_PER_MIN:
        msg = (f"CA rate limit: this call asks for {n} PVs, but this server allows at most "
               f"{CA_MAX_PVS_PER_MIN} PVs per minute; ask for at most {CA_MAX_PVS_PER_MIN}.")
    else:
        freed, wait = CA_MAX_PVS_PER_MIN - used, 0.0
        for t, k in _ca_calls:          # oldest first: when has enough expired?
            freed += k
            if freed >= n:
                wait = 60 - (now - t)
                break
        msg = (f"CA rate limit: this server allows {CA_MAX_PVS_PER_MIN} PVs per minute for caget and "
               f"camonitor together, shared by everyone using it; {used} were used in the last minute "
               f"and this call needs {n}. Retry in {int(wait) + 1} s, or ask for fewer PVs. Tell the "
               "user the call was refused by the rate limit, not because the PVs are unreachable.")
    print(f"refused {tool}: {n} PVs, {used}/{CA_MAX_PVS_PER_MIN} used in the last minute",
          file=sys.stderr, flush=True)
    raise ToolError(msg)
