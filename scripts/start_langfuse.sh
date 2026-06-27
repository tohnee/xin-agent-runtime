#!/usr/bin/env bash
# Xin Agent Runtime — Langfuse 一键启动脚本
#
# 两种模式:
#   ./scripts/start_langfuse.sh cloud    — 配置 Langfuse Cloud (推荐，零运维)
#   ./scripts/start_langfuse.sh local    — 启动本地 Langfuse Docker 环境
#
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

MODE="${1:-cloud}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║     Langfuse 可观测性一键配置                ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ── Cloud 模式: 交互式配置 Langfuse Cloud ──
setup_cloud() {
    info "配置 Langfuse Cloud (推荐，零运维)"
    echo ""
    echo "步骤:"
    echo "  1. 访问 https://cloud.langfuse.com 注册 (免费层 50k events/月)"
    echo "  2. 创建 Organization → Project"
    echo "  3. Settings → API Keys → 获取 Public Key 和 Secret Key"
    echo ""

    # 交互式输入
    read -rp "请输入 Langfuse Public Key (pk-lf-...): " PUBLIC_KEY
    read -rp "请输入 Langfuse Secret Key (sk-lf-...): " SECRET_KEY

    if [ -z "$PUBLIC_KEY" ] || [ -z "$SECRET_KEY" ]; then
        error "Public Key 和 Secret Key 不能为空"
    fi

    # 写入 .env
    ENV_FILE="$PROJECT_DIR/.env"
    touch "$ENV_FILE"

    # 移除旧的 Langfuse 配置
    sed -i '' '/XRUNTIME_LANGFUSE/d' "$ENV_FILE" 2>/dev/null || true

    cat >> "$ENV_FILE" << EOF
XRUNTIME_LANGFUSE_ENABLED=true
XRUNTIME_LANGFUSE_HOST=https://cloud.langfuse.com
XRUNTIME_LANGFUSE_PUBLIC_KEY=${PUBLIC_KEY}
XRUNTIME_LANGFUSE_SECRET_KEY=${SECRET_KEY}
EOF

    info "配置已写入 .env"
    info "安装 Langfuse SDK..."
    pip install langfuse -q 2>/dev/null || warn "pip install langfuse 失败，请手动安装"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Langfuse Cloud 配置完成"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  面板: https://cloud.langfuse.com"
    echo "  配置: $ENV_FILE"
    echo ""
    echo "  验证:"
    echo "    PYTHONPATH=src python3 scripts/verify_langfuse_trace.py"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ── Local 模式: 启动本地 Docker 环境 ──
setup_local() {
    info "启动本地 Langfuse Docker 环境"
    echo ""
    warn "Langfuse v3 需要 PostgreSQL + ClickHouse + Redis + MinIO"
    warn "首次拉取镜像约 2GB，需要 10-15 分钟"
    echo ""

    command -v docker >/dev/null 2>&1 || error "Docker 未安装"
    docker info >/dev/null 2>&1 || error "Docker daemon 未运行"

    LANGFUSE_DIR="/tmp/xin-langfuse"
    mkdir -p "$LANGFUSE_DIR"

    # 写入 docker-compose.yml
    cat > "$LANGFUSE_DIR/docker-compose.yml" << 'YAMLEOF'
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    volumes:
      - pg-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 10

  clickhouse:
    image: clickhouse/clickhouse-server:latest
    user: "101:101"
    environment:
      CLICKHOUSE_DB: default
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: clickhouse
    volumes:
      - ch-data:/var/lib/clickhouse
      - ch-logs:/var/log/clickhouse-server
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:8123/ping"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    command: --requirepass myredissecret --maxmemory-policy noeviction
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 3s
      timeout: 5s
      retries: 10

  minio:
    image: minio/minio:latest
    entrypoint: sh
    command: -c 'mkdir -p /data/langfuse && minio server --address ":9000" --console-address ":9001" /data'
    environment:
      MINIO_ROOT_USER: minio
      MINIO_ROOT_PASSWORD: miniosecret
    ports:
      - "9090:9000"
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 3s
      timeout: 5s
      retries: 5

  langfuse-web:
    image: langfuse/langfuse:3
    ports:
      - "3000:3000"
    environment:
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: local-dev-secret-32chars-minimum!!
      SALT: local-dev-salt-32chars-minimum!!!!!!
      ENCRYPTION_KEY: 0000000000000000000000000000000000000000000000000000000000000000
      DATABASE_URL: postgresql://postgres:postgres@postgres:5432/postgres
      CLICKHOUSE_URL: http://clickhouse:8123
      CLICKHOUSE_MIGRATION_URL: clickhouse://clickhouse:9000
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: clickhouse
      CLICKHOUSE_CLUSTER_ENABLED: "false"
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_AUTH: myredissecret
      LANGFUSE_S3_EVENT_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_EVENT_UPLOAD_REGION: auto
      LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: minio
      LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: miniosecret
      LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: http://minio:9000
      LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_EVENT_UPLOAD_PREFIX: events/
      LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_MEDIA_UPLOAD_REGION: auto
      LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: minio
      LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: miniosecret
      LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: http://localhost:9090
      LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_MEDIA_UPLOAD_PREFIX: media/
      TELEMETRY_ENABLED: "false"
    depends_on:
      postgres:
        condition: service_healthy
      clickhouse:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:3000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 15

volumes:
  pg-data:
  ch-data:
  ch-logs:
  redis-data:
  minio-data:
YAMLEOF

    info "docker-compose.yml 已生成: $LANGFUSE_DIR/docker-compose.yml"
    info "启动容器 (首次需拉取镜像，约 10-15 分钟)..."
    echo ""

    cd "$LANGFUSE_DIR"
    docker compose up -d 2>&1 | tail -10

    # 等待 Langfuse 就绪
    info "等待 Langfuse 就绪..."
    for i in $(seq 1 60); do
        if curl -sf http://localhost:3000/api/health 2>/dev/null | grep -qi "ok\|health"; then
            info "Langfuse 就绪! (耗时 $((i*5))s)"
            break
        fi
        if [ $((i % 6)) -eq 0 ]; then
            warn "仍在等待... (${i}x5s) 容器状态:"
            docker ps --filter "name=langfuse" --format "  {{.Names}}: {{.Status}}" 2>/dev/null || true
        fi
        sleep 5
    done

    if ! curl -sf http://localhost:3000/api/health 2>/dev/null | grep -qi "ok\|health"; then
        error "Langfuse 启动超时。检查日志: docker logs langfuse-langfuse-web-1"
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Langfuse 本地环境已启动"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  面板: http://localhost:3000"
    echo "  MinIO: http://localhost:9090"
    echo ""
    echo "  首次访问需要注册管理员账号"
    echo "  然后创建 Project → 获取 API Keys"
    echo ""
    echo "  配置 XRuntime:"
    echo "    export XRUNTIME_LANGFUSE_ENABLED=true"
    echo "    export XRUNTIME_LANGFUSE_HOST=http://localhost:3000"
    echo "    export XRUNTIME_LANGFUSE_PUBLIC_KEY=pk-lf-xxx"
    echo "    export XRUNTIME_LANGFUSE_SECRET_KEY=sk-lf-xxx"
    echo ""
    echo "  停止: cd $LANGFUSE_DIR && docker compose down"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ── 主逻辑 ──
case "$MODE" in
    cloud)
        setup_cloud
        ;;
    local)
        setup_local
        ;;
    *)
        echo "用法: $0 [cloud|local]"
        echo "  cloud — 配置 Langfuse Cloud (推荐)"
        echo "  local — 启动本地 Docker 环境"
        exit 1
        ;;
esac
