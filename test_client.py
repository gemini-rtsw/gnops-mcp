"""Smoke-test a running gnops-mcp server: list its tools and call each one.

  python test_client.py http://HOST:8001/mcp [PV ...]

PVs default to the first few archived PVs matching "ws:*" (weather station).
Needs the mcp package (pip install -r requirements.txt).
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call(s: ClientSession, tool: str, args: dict):
    res = await s.call_tool(tool, args)
    sc = res.structured_content
    if sc is not None:
        data = sc.get("result", sc)  # non-dict returns are wrapped as {"result": ...}
    else:
        data = res.content[0].text if res.content else ""
    print(f"{tool}({json.dumps(args)}) -> {'ERROR ' if res.is_error else ''}{json.dumps(data)[:400]}")
    return data


async def main(url: str, pvs: list[str]):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print("server:", init.server_info.name)
            print("tools: ", [t.name for t in (await s.list_tools()).tools])
            await call(s, "server_info", {})
            if not pvs:
                pvs = (await call(s, "search_pvs", {"pattern": "ws:*", "limit": 3})) or []
            await call(s, "get_pv_data", {"pvs": pvs[:1], "start": "now-10m", "max_points": 5})
            await call(s, "list_log_systems", {"start": "now-1h"})
            await call(s, "caget", {"pvs": pvs})
            await call(s, "camonitor", {"pvs": pvs, "seconds": 5})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
