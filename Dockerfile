# gnops-mcp: read-only MCP server (TED telemetry and logs via Grafana, live EPICS
# caget/camonitor). Runs with --network host so Channel Access broadcasts and
# the beacon port work like on the host itself.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .

# The unit runs the container --read-only; nothing here needs to write.
USER nobody
EXPOSE 8001
CMD ["python", "server.py", "--http"]
