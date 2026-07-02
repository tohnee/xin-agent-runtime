FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r xruntime && useradd -r -g xruntime xruntime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e ".[xruntime-minimal]" pyyaml httpx

RUN apt-get remove -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/app/src
ENV XRUNTIME_SERVER_PORT=8900
ENV HOME=/home/xruntime
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /var/lib/xruntime/workspaces /var/lib/xruntime/knowledge /var/log/xruntime /home/xruntime/.cache
RUN chown -R xruntime:xruntime /app /var/lib/xruntime /var/log/xruntime /home/xruntime

USER xruntime

EXPOSE 8900

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8900/health || exit 1

CMD ["python", "-m", "xruntime._server"]
