FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl \
    && rm -rf /var/lib/apt/lists/*

# 安装项目（agentscope 内核 + xruntime 扩展）
COPY . /app
RUN pip install --no-cache-dir -e ".[xruntime-dev]"

# Workspace 目录
RUN mkdir -p /app/xruntime-workspaces
ENV XRUNTIME_WORKSPACE_DIR=/app/xruntime-workspaces

EXPOSE 8900

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8900/health')" || exit 1

CMD ["python", "-m", "xruntime._server"]
