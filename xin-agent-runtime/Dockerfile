FROM python:3.11-slim

WORKDIR /app

# ── 创建非 root 用户 ──────────────────────────────────────────
# 使用固定 UID:GID 1000:1000 与 DockerWorkspace 沙箱默认一致，
# 避免容器以 root 运行导致的提权风险。
RUN groupadd -r -g 1000 xruntime && \
    useradd -r -u 1000 -g 1000 -m -d /home/xruntime -s /bin/bash xruntime

# ── 系统依赖（最小化安装） ────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── 安装项目（agentscope 内核 + xruntime 扩展） ──────────────
# Production image installs only the runtime extra (no dev tools
# such as pytest/mypy/ruff — they expand the attack surface).
COPY --chown=xruntime:xruntime . /app
RUN pip install --no-cache-dir -e ".[xruntime]"

# ── Workspace 目录（由非 root 用户拥有） ─────────────────────
RUN mkdir -p /app/xruntime-workspaces /home/xruntime/.cache && \
    chown -R xruntime:xruntime /app /home/xruntime

ENV XRUNTIME_WORKSPACE_DIR=/app/xruntime-workspaces
ENV HOME=/home/xruntime
ENV PYTHONUNBUFFERED=1

# ── 切换到非 root 用户 ────────────────────────────────────────
USER 1000:1000

EXPOSE 8900

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8900/health')" || exit 1

CMD ["python", "-m", "xruntime._server"]
