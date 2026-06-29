#!/bin/bash
# ============================================
# XRuntime Docker 一键启动脚本
# ============================================
# 使用方法:
#   1. 进入 deploy 目录: cd deploy
#   2. 启动服务: ./start.sh
#   3. 停止服务: ./start.sh stop
#   4. 查看日志: ./start.sh logs
#   5. 查看状态: ./start.sh status
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_banner() {
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                    XRuntime - 企业级 Agent 运行时              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}错误: 未找到 Docker，请先安装 Docker${NC}"
        echo "安装指南: https://docs.docker.com/get-docker/"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo -e "${RED}错误: Docker 服务未运行，请启动 Docker${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Docker 环境正常${NC}"
}

check_env() {
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}⚠ 未找到 .env 文件，使用默认配置${NC}"
        echo "如需自定义配置，请复制 .env.example 为 .env 并修改"
    else
        echo -e "${GREEN}✓ 配置文件已加载${NC}"
    fi
}

start_service() {
    print_banner
    echo -e "${BLUE}[1/4]${NC} 检查环境..."
    check_docker
    check_env

    echo ""
    echo -e "${BLUE}[2/4]${NC} 构建镜像（首次启动较慢，请耐心等待）..."
    docker compose build

    echo ""
    echo -e "${BLUE}[3/4]${NC} 启动服务..."
    docker compose up -d

    echo ""
    echo -e "${BLUE}[4/4]${NC} 等待服务启动..."
    echo "  正在等待 Redis 就绪..."
    for i in {1..30}; do
        if docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD:-xruntime_redis_pwd}" ping 2>/dev/null | grep -q PONG; then
            echo -e "${GREEN}  ✓ Redis 已就绪${NC}"
            break
        fi
        sleep 2
        if [ $i -eq 30 ]; then
            echo -e "${YELLOW}  ⚠ Redis 启动超时，请检查日志${NC}"
        fi
    done

    echo "  正在等待 XRuntime 启动..."
    for i in {1..60}; do
        if curl -s http://localhost:${XRUNTIME_PORT:-8900}/health 2>/dev/null | grep -q ok; then
            echo -e "${GREEN}  ✓ XRuntime 已就绪${NC}"
            break
        fi
        sleep 2
        if [ $i -eq 60 ]; then
            echo -e "${YELLOW}  ⚠ XRuntime 启动超时，请检查日志${NC}"
        fi
    done

    echo ""
    echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  🎉 XRuntime 启动成功！${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  🌐 服务地址: http://localhost:${XRUNTIME_PORT:-8900}"
    echo "  ❤️  健康检查: http://localhost:${XRUNTIME_PORT:-8900}/health"
    echo ""
    echo "  📡 可用端点:"
    echo "    POST /v1/messages         - Anthropic Messages API"
    echo "    POST /v1/opencode         - OpenCode Protocol"
    echo "    POST /v1/claude-code/query - Claude Code SDK"
    echo ""
    echo "  📋 常用命令:"
    echo "    查看日志: ./start.sh logs"
    echo "    查看状态: ./start.sh status"
    echo "    停止服务: ./start.sh stop"
    echo "    重启服务: ./start.sh restart"
    echo ""

    if [ -z "${XRUNTIME_MODEL_API_KEY:-}" ]; then
        echo -e "${YELLOW}  ⚠ 提示: 当前使用 Mock 模型，仅用于测试连通性${NC}"
        echo -e "${YELLOW}    配置真实模型请编辑 .env 文件设置 XRUNTIME_MODEL_PROVIDER 和 XRUNTIME_MODEL_API_KEY${NC}"
        echo ""
    fi
}

stop_service() {
    echo -e "${BLUE}停止 XRuntime 服务...${NC}"
    docker compose down
    echo -e "${GREEN}✓ 服务已停止${NC}"
}

restart_service() {
    stop_service
    start_service
}

show_logs() {
    echo -e "${BLUE}查看 XRuntime 日志 (Ctrl+C 退出)...${NC}"
    docker compose logs -f xruntime
}

show_status() {
    print_banner
    echo -e "${BLUE}服务状态:${NC}"
    echo ""
    docker compose ps
    echo ""

    if curl -s http://localhost:${XRUNTIME_PORT:-8900}/health 2>/dev/null | grep -q ok; then
        echo -e "${GREEN}✓ XRuntime 服务运行正常${NC}"
    else
        echo -e "${RED}✗ XRuntime 服务未响应${NC}"
    fi
}

show_help() {
    print_banner
    echo "使用方法: ./start.sh [命令]"
    echo ""
    echo "可用命令:"
    echo "  start     启动服务（默认）"
    echo "  stop      停止服务"
    echo "  restart   重启服务"
    echo "  logs      查看日志"
    echo "  status    查看状态"
    echo "  build     重新构建镜像"
    echo "  help      显示帮助信息"
    echo ""
    echo "示例:"
    echo "  ./start.sh           # 启动服务"
    echo "  ./start.sh stop      # 停止服务"
    echo "  ./start.sh logs      # 查看日志"
}

case "${1:-start}" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    logs)
        show_logs
        ;;
    status)
        show_status
        ;;
    build)
        docker compose build
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}未知命令: $1${NC}"
        echo ""
        show_help
        exit 1
        ;;
esac
