FROM python:3.12-slim

# tzdata: zonas horarias completas para SESSION_LOG_TZ.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data

WORKDIR /app
COPY scripts/ scripts/

ENV PYTHONUNBUFFERED=1 \
    SESSION_LOG_DB=/data/session-log.db \
    SESSION_LOG_TZ=America/Panama \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["python3", "scripts/mcp_server.py", "--http"]
