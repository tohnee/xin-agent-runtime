#!/usr/bin/env bash
# Xin Agent Runtime — 一键部署脚本
# 用法: ./deploy.sh [--docker|--bare-metal]
set -euo pipefail

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 配置 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DEPLOY_MODE="${1:---bare-metal}"
REDIS_CONTAINER="xruntime-redis"
RUNTIME_PORT="${XRUNTIME_PORT:-8900}"

# ── 前置检查 ──
check_prerequisites() {
    info "检查前置条件..."

    command -v python3 >/dev/null 2>&1 || error "Python 3 未安装"
    python3 --version | grep -q "3.1[1-9]" || error "需要 Python 3.11+，当前: $(python3 --version)"

    if [ "$DEPLOY_MODE" = "--docker" ]; then
        command -v docker >/dev/null 2>&1 || error "Docker 未安装"
    fi

    command -v pip3 >/dev/null 2>&1 || error "pip 未安装"

    info "Python: $(python3 --version)"
    info "部署模式: $DEPLOY_MODE"
}

# ── 安装依赖 ──
install_dependencies() {
    info "安装依赖（AgentScope 内核 + XRuntime 扩展层）..."

    cd "$PROJECT_DIR"
    pip3 install -e ".[xruntime-dev]" -q

    # 验证两个包都已安装
    python3 -c "import agentscope; print('  AgentScope:', agentscope.__version__)" || error "AgentScope 安装失败"
    python3 -c "import xruntime; print('  XRuntime:', xruntime.__version__)" || error "XRuntime 安装失败"

    info "依赖安装完成"
}

# ── 初始化 Redis ──
init_redis() {
    info "初始化 Redis..."

    if [ "$DEPLOY_MODE" = "--docker" ]; then
        # Docker 模式：用容器启动 Redis
        if docker ps -a --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
            info "Redis 容器已存在，正在重启..."
            docker start "$REDIS_CONTAINER" || true
        else
            REDIS_PASSWORD="${REDIS_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')}"
            info "Redis 密码: $REDIS_PASSWORD"
            docker run -d \
                --name "$REDIS_CONTAINER" \
                -p 6379:6379 \
                -v xruntime-redis-data:/data \
                redis:7-alpine \
                redis-server --requirepass "$REDIS_PASSWORD" \
                              --maxmemory 512mb \
                              --maxmemory-policy allkeys-lru \
                              --appendonly yes
            export REDIS_PASSWORD
        fi
    else
        # 裸金属模式：检查本地 Redis
        if command -v redis-cli >/dev/null 2>&1; then
            if redis-cli ping 2>/dev/null | grep -q PONG; then
                info "本地 Redis 已在运行"
            else
                warn "Redis 未运行，尝试启动..."
                if command -v redis-server >/dev/null 2>&1; then
                    redis-server --daemonize yes --port 6379
                    sleep 1
                    redis-cli ping | grep -q PONG || error "Redis 启动失败"
                    info "Redis 已启动"
                else
                    error "Redis 未安装，请安装 Redis 或使用 --docker 模式"
                fi
            fi
        else
            error "redis-cli 未找到，请安装 Redis 或使用 --docker 模式"
        fi
    fi

    # 验证 Redis 连接
    if [ -n "${REDIS_PASSWORD:-}" ]; then
        redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG || error "Redis 连接失败"
    else
        redis-cli ping 2>/dev/null | grep -q PONG || error "Redis 连接失败"
    fi
    info "Redis 就绪"
}

# ── 生成配置 ──
generate_config() {
    info "生成配置文件..."

    local config_file="$PROJECT_DIR/xruntime.yaml"

    if [ -f "$config_file" ]; then
        warn "配置文件已存在: $config_file（跳过生成）"
        return
    fi

    cat > "$config_file" << 'EOF'
server:
  auth_enabled: true
  host: 0.0.0.0
  port: 8900

tenants:
  - id: acme
    name: "ACME Corp"

storage:
  redis_host: localhost
  redis_port: 6379
  redis_password: "${REDIS_PASSWORD}"
  tenant_prefix: "xrt:{tid}:"

message_bus:
  redis_host: localhost
  redis_port: 6379

knowledge:
  enabled: true
  backend: llm_wiki
  mode: both
  retrieval_top_k: 5

permission:
  default_role: viewer

enable_enterprise_middlewares: true
EOF
    info "配置文件已生成: $config_file"
}

# ── 生成 API Key ──
generate_api_keys() {
    info "生成 API Key..."

    local admin_key="sk-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    local viewer_key="sk-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

    export XRUNTIME_API_KEY_RECORDS="[
  {\"key\":\"${admin_key}\",\"tenant_id\":\"acme\",\"user_id\":\"admin\",\"role\":\"admin\",\"kb_ids\":[\"kb1\"],\"active\":true},
  {\"key\":\"${viewer_key}\",\"tenant_id\":\"acme\",\"user_id\":\"viewer\",\"role\":\"viewer\",\"kb_ids\":[\"kb1\"],\"active\":true}
]"

    export XRUNTIME_JWT_SECRET="${XRUNTIME_JWT_SECRET:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')}"
    export XRUNTIME_WORKSPACE_BACKEND="${XRUNTIME_WORKSPACE_BACKEND:-docker}"
    export XRUNTIME_PRODUCTION="${XRUNTIME_PRODUCTION:-1}"
    export XRUNTIME_RATE_LIMIT="${XRUNTIME_RATE_LIMIT:-100/60}"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  API Keys（请妥善保存）:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Admin  : $admin_key"
    echo "  Viewer : $viewer_key"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # 保存到 .env 文件
    cat > "$PROJECT_DIR/.env" << EOF
REDIS_PASSWORD=${REDIS_PASSWORD:-}
XRUNTIME_API_KEY_RECORDS=${XRUNTIME_API_KEY_RECORDS}
XRUNTIME_JWT_SECRET=${XRUNTIME_JWT_SECRET}
XRUNTIME_WORKSPACE_BACKEND=${XRUNTIME_WORKSPACE_BACKEND}
XRUNTIME_PRODUCTION=${XRUNTIME_PRODUCTION}
XRUNTIME_RATE_LIMIT=${XRUNTIME_RATE_LIMIT}
EOF
    info "环境变量已保存到 .env"
}

# ── 启动服务 ──
start_service() {
    info "启动 Xin Agent Runtime..."

    cd "$PROJECT_DIR"

    # 加载 .env
    if [ -f .env ]; then
        export $(grep -v '^#' .env | xargs 2>/dev/null || true)
    fi

    # 后台启动
    nohup python3 -m xruntime._server > /tmp/xin-agent-runtime.log 2>&1 &
    local pid=$!
    info "服务 PID: $pid"

    # 等待启动
    info "等待服务就绪..."
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:${RUNTIME_PORT}/health" >/dev/null 2>&1; then
            info "服务已就绪 (耗时 ${i}s)"
            echo ""
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "  Xin Agent Runtime 已启动"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "  URL     : http://localhost:${RUNTIME_PORT}"
            echo "  Health  : http://localhost:${RUNTIME_PORT}/health"
            echo "  Ready   : http://localhost:${RUNTIME_PORT}/ready"
            echo "  PID     : $pid"
            echo "  日志    : /tmp/xin-agent-runtime.log"
            echo "  配置    : $PROJECT_DIR/xruntime.yaml"
            echo "  环境    : $PROJECT_DIR/.env"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            return 0
        fi
        sleep 1
    done

    error "服务启动超时，请检查日志: /tmp/xin-agent-runtime.log"
}

# ── 主流程 ──
main() {
    echo ""
    echo "╔═══════════════════════════════════════════════╗"
    echo "║     Xin Agent Runtime — 一键部署             ║"
    echo "╚═══════════════════════════════════════════════╝"
    echo ""

    check_prerequisites
    install_dependencies
    init_redis
    generate_config
    generate_api_keys
    start_service

    echo ""
    info "部署完成！"
    info "运行健康检查: ./scripts/health-check.sh"
}

main "$@"
