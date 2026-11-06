# gnops-mcp

Read-only [MCP](https://modelcontextprotocol.io) server for Gemini North
operations data, shipped as a container (gemini-rtsw-ci type C). CI also
builds an RPM with a systemd unit; hosts without sudo run the image with
docker compose instead (Install).

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

At most `MAX_CONCURRENT` (default 5) Grafana requests and CA calls run at
once, across all clients; a call that waits more than `QUEUE_WAIT_S` (30 s)
for a slot fails with "server busy". `caget` and `camonitor` together touch
at most `CA_MAX_PVS_PER_MIN` (default 200) PVs in any minute, across all
clients: each PV costs a CA search broadcast and an IOC connection. A call over
the limit fails at once with the reason and the seconds to wait, and the
refusal is logged (`journalctl -u gnops-mcp`).

## Install

CI builds two things from each commit on `main`: the image
`ghcr.io/gemini-rtsw/gnops-mcp:<version>-git<hash>` and an RPM pinned to it.
There are two ways to run it; both use the same settings file and the same
container hardening (`--network host` for Channel Access, `--read-only`,
`--cap-drop ALL`, `no-new-privileges`).

### Container, no sudo (mkoteddev-lv1)

For a host where you have docker but no sudo, e.g. mkoteddev-lv1 as `software`.
The host can't reach GHCR, so the image comes as a `docker save` tarball. On an
open host, from the RPM and the image of one build:

```
rpm2cpio gnops-mcp-<NVR>.rpm | cpio -idm './usr/share/gnops-mcp/*'
docker save ghcr.io/gemini-rtsw/gnops-mcp:<version>-git<hash> | gzip > gnops-mcp-<version>-git<hash>.tar.gz
```

Copy `gnops-mcp.yaml`, `gnops-mcp.env` and the tarball to the host's home
directory, then on the host:

```
docker load -i gnops-mcp-<version>-git<hash>.tar.gz
docker compose --env-file gnops-mcp.env -f gnops-mcp.yaml up -d
docker logs gnops-mcp        # ends with "Uvicorn running on http://0.0.0.0:8001"
```

`gnops-mcp.env` holds the settings and `IMAGE`, the pinned build. To change a
setting, edit it and run the `up -d` line again. To upgrade, load the new
tarball, set `IMAGE` to the new tag (or take the new release's
`gnops-mcp.env`, carrying over local edits) and run `up -d`. Logs, including
rate-limit refusals: `docker logs gnops-mcp`. `restart: unless-stopped` brings
it back after a crash or a reboot.

### RPM and systemd (hosts with sudo)

```
sudo dnf install gnops-mcp-<NVR>
sudo vi /etc/sysconfig/gnops-mcp      # same settings as gnops-mcp.env
sudo systemctl enable --now gnops-mcp
```

The unit pins its own image tag and pulls it as `software` when it is missing.
Logs: `journalctl -u gnops-mcp`. Don't run both ways on one host: they use the
same container name and port.

### Settings

The defaults are for Mauna Kea: TED Grafana on mkoteddev-lv1 and the Channel
Access address list TED's archiver uses (container `appliance-0`; `caget` is
served through the CA gateway mkoepicsgw-lv1). The server accepts requests
addressed to the host's FQDN or short name; add aliases in
`MCP_ALLOWED_HOSTS`.

### Check it

From any client host:

```
claude mcp add -s user --transport http gnops-mcp http://<host>:8001/mcp
```

Then in a session: `server_info`, `search_logs` for one system over the last
hour, and `caget ws:wsTemp` (a value in degrees C, not "not connected").

## Develop

```
docker build -t gnops-mcp:dev .
docker run --rm --network host --env-file deploy/gnops-mcp.sysconfig gnops-mcp:dev
python test_client.py http://localhost:8001/mcp [PV ...]
./gemini-rtsw-ci/build_rpm.sh --profile lightweight --el 9
```
