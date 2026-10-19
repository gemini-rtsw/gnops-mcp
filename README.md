# gnops-mcp

Read-only [MCP](https://modelcontextprotocol.io) server for Gemini North
operations data, shipped as a container and installed by RPM
(gemini-rtsw-ci type C).

| Tool | What it does |
|---|---|
| `search_pvs` | find archived PV names by glob |
| `get_pv_data` | archived telemetry (Archiver Appliance, via TED Grafana) |
| `list_log_systems`, `search_logs` | TED logs (Elasticsearch, via TED Grafana) |
| `grafana_link` | browser link to the TED Telemetry or Logs dashboard showing the same data |
| `caget` | current value of live PVs (Channel Access) |
| `camonitor` | value changes of live PVs over up to 60 s |
| `server_info` | host health check |

There is no put: nothing in the server writes to a PV.

## Install

```
sudo dnf install gnops-mcp
sudo vi /etc/sysconfig/gnops-mcp      # Grafana URL, EPICS_CA_ADDR_LIST, port
sudo systemctl enable --now gnops-mcp
```

The unit runs `ghcr.io/gemini-rtsw/gnops-mcp:<version>-git<hash>` with
`--network host` (needed for Channel Access) and `--read-only`.

Connect a client:

```
claude mcp add --transport http gnops-mcp http://<host>:8001/mcp
```

## Develop

```
docker build -t gnops-mcp:dev .
docker run --rm --network host --env-file deploy/gnops-mcp.sysconfig gnops-mcp:dev
python test_client.py http://localhost:8001/mcp [PV ...]
./gemini-rtsw-ci/build_rpm.sh --profile lightweight --el 9
```
