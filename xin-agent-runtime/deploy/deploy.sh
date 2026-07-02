#!/bin/bash
# ============================================
# XRuntime 一键部署脚本
# --------------------------------------------
# 用法:
#   ./deploy.sh              # 交互式部署
#   ./deploy.sh --swarm      # Docker Swarm 部署
#   ./deploy.sh --compose    # Docker Compose 部署 (开发/测试)
#   ./deploy.sh --check      # 仅运行预检查
#   ./deploy.sh --down       # 停止并清理
#   ./deploy.sh --logs       # 查看日志
#   ./deploy.sh --status     # 查看服务状态
# ============================================

set -euo pipefail

# --------------------------------------------
# 颜色定义
# --------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --------------------------------------------
# 路径定义
# --------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_DIR="$SCRIPT_DIR"
ENV_FILE="$DEPLOY_DIR/.env"
ENV_EXAMPLE="$DEPLOY_DIR/.env.example"
ENV_PROD_EXAMPLE="$DEPLOY_DIR/.env.production-example"
STACK_FILE="$DEPLOY_DIR/xruntime-swarm-stack.yml"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
CHECK_SCRIPT="$DEPLOY_DIR/pre-deploy-check.sh"

STACK_NAME="xruntime"

# --------------------------------------------
# 辅助函数
# --------------------------------------------
info()    { echo -e "  ${BLUE}[INFO]${NC} $1"; }
success() { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "  ${RED}[FAIL]${NC} $1"; exit 1; }

banner() {
    echo ""
    echo "┌─────────────────────────────────────────────────────────┐"
    printf "│  %-53s │\n" "$1"
    echo "└─────────────────────────────────────────────────────────┘"
}

# --------------------------------------------
# 前置检查
# --------------------------------------------
check_prerequisites() {
    banner "前置检查"

    # Docker
    if ! command -v docker &> /dev/null; then
        fail "Docker 未安装，请先安装 Docker"
    fi
    success "Docker 已安装: $(docker --version | awk '{print $3}')"

    # Docker 运行中
    if ! docker info &> /dev/null; then
        fail "Docker 服务未运行，请先启动: sudo systemctl start docker"
    fi
    success "Docker 服务运行中"
}

# --------------------------------------------
# 步骤 1: 准备 .env 文件
# --------------------------------------------
prepare_env() {
    banner "步骤 1/5: 准备配置文件"

    if [ -f "$ENV_FILE" ]; then
        info ".env 文件已存在"
        read -p "  是否覆盖? (y/N): " -r overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            success "保留现有 .env 文件"
            return
        fi
    fi

    # 选择模板
    echo ""
    echo "  选择配置模板:"
    echo "    1) 生产环境模板 (推荐)"
    echo "    2) 开发环境模板"
    echo "    3) 自定义"
    read -p "  选择 [1-3] (默认 1): " -r template_choice

    case "${template_choice:-1}" in
        1)
            cp "$ENV_PROD_EXAMPLE" "$ENV_FILE"
            success "已复制生产环境模板"
            ;;
        2)
            cp "$ENV_EXAMPLE" "$ENV_FILE"
            success "已复制开发环境模板"
            ;;
        3)
            info "请手动编辑 $ENV_FILE"
            touch "$ENV_FILE"
            ;;
    esac

    # 生成随机密钥
    if [ -f "$ENV_FILE" ]; then
        info "生成随机密钥..."

        API_KEY="xrk-prod-$(openssl rand -hex 16)"
        JWT_SECRET="jsk-prod-$(openssl rand -hex 24)"
        REDIS_PASS="redis-prod-$(openssl rand -hex 16)"

        # 替换占位符
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "s|^XRUNTIME_API_KEYS=.*|XRUNTIME_API_KEYS=${API_KEY}|" "$ENV_FILE"
            sed -i '' "s|^XRUNTIME_JWT_SECRET=.*|XRUNTIME_JWT_SECRET=${JWT_SECRET}|" "$ENV_FILE"
            sed -i '' "s|^XRUNTIME_REDIS_PASSWORD=.*|XRUNTIME_REDIS_PASSWORD=${REDIS_PASS}|" "$ENV_FILE"
        else
            sed -i "s|^XRUNTIME_API_KEYS=.*|XRUNTIME_API_KEYS=${API_KEY}|" "$ENV_FILE"
            sed -i "s|^XRUNTIME_JWT_SECRET=.*|XRUNTIME_JWT_SECRET=${JWT_SECRET}|" "$ENV_FILE"
            sed -i "s|^XRUNTIME_REDIS_PASSWORD=.*|XRUNTIME_REDIS_PASSWORD=${REDIS_PASS}|" "$ENV_FILE"
        fi

        success "API Key: ${API_KEY:0:20}..."
        success "JWT Secret: ${JWT_SECRET:0:20}..."
        success "Redis Password: ${REDIS_PASS:0:20}..."

        echo ""
        warn "请记下以上密钥，它们不会再次显示"
        echo "  完整配置请编辑: $ENV_FILE"
    fi
}

# --------------------------------------------
# 步骤 2: 运行预检查
# --------------------------------------------
run_preflight() {
    banner "步骤 2/5: 部署预检查"

    if [ ! -x "$CHECK_SCRIPT" ]; then
        chmod +x "$CHECK_SCRIPT"
    fi

    info "运行预检查脚本..."
    bash "$CHECK_SCRIPT" || true

    echo ""
    read -p "  预检查完成，是否继续部署? (Y/n): " -r continue
    if [[ "$continue" =~ ^[Nn]$ ]]; then
        info "部署已取消"
        exit 0
    fi
}

# --------------------------------------------
# 步骤 3: 创建必要目录
# --------------------------------------------
create_directories() {
    banner "步骤 3/5: 创建数据目录"

    directories=(
        "/var/lib/xruntime/workspaces"
        "/var/lib/xruntime/knowledge"
        "/var/log/xruntime"
        "/etc/xruntime/skills"
    )

    for dir in "${directories[@]}"; do
        if [ -d "$dir" ]; then
            success "目录已存在: $dir"
        else
            sudo mkdir -p "$dir"
            sudo chown "$(id -u):$(id -g)" "$dir"
            success "已创建: $dir"
        fi
    done
}

# --------------------------------------------
# 步骤 4: 部署
# --------------------------------------------
deploy_swarm() {
    banner "步骤 4/5: Docker Swarm 部署"

    # 检查 Swarm 模式
    if ! docker info 2>/dev/null | grep -q "Swarm: active"; then
        warn "Docker Swarm 未初始化"
        read -p "  是否初始化 Swarm? (Y/n): " -r init_swarm
        if [[ ! "$init_swarm" =~ ^[Nn]$ ]]; then
            docker swarm init
            success "Swarm 已初始化"
        else
            fail "Swarm 部署需要先初始化: docker swarm init"
        fi
    else
        success "Docker Swarm 已激活"
    fi

    # 加载 .env
    if [ -f "$ENV_FILE" ]; then
        info "加载配置..."
        set -a
        source "$ENV_FILE"
        set +a
    fi

    # 部署 Stack
    info "部署 Docker Stack..."
    docker stack deploy -c "$STACK_FILE" "$STACK_NAME"
    success "Stack 已部署"

    # 等待服务启动
    info "等待服务启动..."
    sleep 5

    # 显示服务状态
    banner "步骤 5/5: 验证部署"
    docker stack services "$STACK_NAME"

    echo ""
    info "服务正在启动中，可能需要 30-60 秒"
    info "查看实时状态: docker stack services $STACK_NAME"
    info "查看日志: docker service logs ${STACK_NAME}_gateway -f"

    # 健康检查
    echo ""
    read -p "  是否等待并执行健康检查? (Y/n): " -r health_check
    if [[ ! "$health_check" =~ ^[Nn]$ ]]; then
        info "等待服务就绪..."
        for i in $(seq 1 30); do
            if curl -s http://localhost:8900/health > /dev/null 2>&1; then
                success "服务已就绪! (尝试 $i/30)"
                echo ""
                echo "  ┌───────────────────────────────────────────────┐"
                echo "  │  ✅ XRuntime 部署成功!                          │"
                echo "  │                                               │"
                echo "  │  网关地址: http://localhost:8900              │"
                echo "  │  健康检查: http://localhost:8900/health        │"
                echo "  │  API 文档: http://localhost:8900/docs         │"
                echo "  │  指标:  http://localhost:8900/metrics         │"
                echo "  └───────────────────────────────────────────────┘"
                return
            fi
            sleep 2
            printf "  等待中... (%d/30)\r" "$i"
        done
        warn "服务未在 60 秒内就绪，请检查日志"
        info "docker service logs ${STACK_NAME}_gateway"
    fi
}

deploy_compose() {
    banner "步骤 4/5: Docker Compose 部署"

    # 加载 .env
    if [ -f "$ENV_FILE" ]; then
        info "加载配置..."
    fi

    info "启动 Docker Compose..."
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build
    success "Compose 服务已启动"

    banner "步骤 5/5: 验证部署"
    docker compose -f "$COMPOSE_FILE" ps

    echo ""
    info "服务启动中..."
    sleep 3

    if curl -s http://localhost:8900/health > /dev/null 2>&1; then
        success "服务已就绪!"
    else
        warn "服务可能需要更多时间启动"
        info "查看日志: docker compose -f $COMPOSE_FILE logs -f"
    fi

    echo ""
    echo "  ┌───────────────────────────────────────────────┐"
    echo "  │  ✅ XRuntime Compose 部署完成!                  │"
    echo "  │                                               │"
    echo "  │  网关地址: http://localhost:8900              │"
    echo "  │  健康检查: http://localhost:8900/health        │"
    echo "  └───────────────────────────────────────────────┘"
}

# --------------------------------------------
# 停止服务
# --------------------------------------------
stop_services() {
    banner "停止 XRuntime 服务"

    if docker info 2>/dev/null | grep -q "Swarm: active"; then
        info "移除 Docker Stack..."
        docker stack rm "$STACK_NAME"
        success "Stack 已移除"
    else
        info "停止 Docker Compose..."
        docker compose -f "$COMPOSE_FILE" down
        success "Compose 已停止"
    fi

    echo ""
    info "数据卷保留，如需清理:"
    echo "  docker volume rm xruntime_redis_data xruntime_workspace_data"
}

# --------------------------------------------
# 查看日志
# --------------------------------------------
show_logs() {
    if docker info 2>/dev/null | grep -q "Swarm: active"; then
        docker service logs "${STACK_NAME}_gateway" -f --tail 100
    else
        docker compose -f "$COMPOSE_FILE" logs -f --tail 100
    fi
}

# --------------------------------------------
# 查看状态
# --------------------------------------------
show_status() {
    banner "XRuntime 服务状态"

    if docker info 2>/dev/null | grep -q "Swarm: active"; then
        echo ""
        docker stack services "$STACK_NAME"
        echo ""
        info "节点状态:"
        docker node ls
    else
        docker compose -f "$COMPOSE_FILE" ps
    fi

    echo ""
    info "健康检查:"
    if curl -s http://localhost:8900/health 2>/dev/null; then
        echo ""
        success "服务正常运行"
    else
        warn "服务未响应"
    fi
}

# --------------------------------------------
# 主菜单
# --------------------------------------------
show_help() {
    cat << 'EOF'

  XRuntime 一键部署脚本
  =====================

  用法:
    ./deploy.sh              交互式部署 (默认 Swarm)
    ./deploy.sh --swarm      Docker Swarm 部署
    ./deploy.sh --compose    Docker Compose 部署 (开发/测试)
    ./deploy.sh --check      仅运行预检查
    ./deploy.sh --down       停止并清理
    ./deploy.sh --logs       查看日志
    ./deploy.sh --status     查看服务状态
    ./deploy.sh --help       显示帮助

  首次部署流程:
    1. ./deploy.sh --check        # 验证环境
    2. ./deploy.sh --swarm        # 部署到 Swarm
    3. ./deploy.sh --status       # 确认服务状态
    4. ./deploy.sh --logs         # 查看启动日志

EOF
}

# --------------------------------------------
# 入口
# --------------------------------------------
main() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║           XRuntime 一键部署脚本 v1.0                            ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"

    local mode="${1:-interactive}"

    case "$mode" in
        --swarm|interactive)
            check_prerequisites
            prepare_env
            run_preflight
            create_directories
            deploy_swarm
            ;;
        --compose)
            check_prerequisites
            prepare_env
            create_directories
            deploy_compose
            ;;
        --check)
            check_prerequisites
            bash "$CHECK_SCRIPT"
            ;;
        --down)
            stop_services
            ;;
        --logs)
            show_logs
            ;;
        --status)
            show_status
            ;;
        --help|-h)
            show_help
            ;;
        *)
            echo "  未知参数: $mode"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
