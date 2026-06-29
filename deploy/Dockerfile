FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --user -e ".[xruntime]" pyyaml

FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r xruntime && useradd -r -g xruntime xruntime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /home/xruntime/.local
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

ENV PATH=/home/xruntime/.local/bin:$PATH
ENV PYTHONPATH=/app/src
ENV XRUNTIME_SERVER_PORT=8900
ENV HOME=/home/xruntime

RUN mkdir -p /var/lib/xruntime/workspaces /var/lib/xruntime/knowledge /var/log/xruntime /home/xruntime/.cache
RUN chown -R xruntime:xruntime /app /var/lib/xruntime /var/log/xruntime /home/xruntime

USER xruntime

EXPOSE 8900

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8900/health || exit 1

CMD ["python", "-m", "xruntime._server"]
