"""Cap on concurrent calls to TED Grafana and Channel Access.

Every Grafana request and every caget/camonitor takes a slot, so however many
clients are connected, at most MAX_CONCURRENT of these run at once. A call that
can't get a slot within QUEUE_WAIT_S seconds fails with "server busy" rather
than piling up.

Config (environment variables):
  MAX_CONCURRENT  default 5
  QUEUE_WAIT_S    default 30
"""

import asyncio
import os
from contextlib import asynccontextmanager

from mcp.server.mcpserver.exceptions import ToolError

MAX_CONCURRENT = max(1, int(os.environ.get("MAX_CONCURRENT") or 5))
QUEUE_WAIT_S = float(os.environ.get("QUEUE_WAIT_S") or 30)

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
