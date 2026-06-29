#!/bin/bash
# ============================================
# XRuntime Docker 镜像导入脚本
# ============================================
# 用于将导出的 xruntime-image.tar.gz 导入到本地 Docker
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_FILE="${1:-$SCRIPT_DIR/xruntime-image.tar.gz}"
IMAGE_NAME="xruntime:latest"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        XRuntime Docker 镜像导入工具                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ 错误: 未找到 Docker，请先安装 Docker${NC}"
    exit 1
fi

# 检查镜像文件
if [ ! -f "$IMAGE_FILE" ]; then
    echo -e "${RED}❌ 错误: 镜像文件不存在: $IMAGE_FILE${NC}"
    echo ""
    echo "  请确保镜像文件在正确的位置，或指定文件路径:"
    echo "    ./load-image.sh /path/to/xruntime-image.tar.gz"
    exit 1
fi

echo "镜像文件: $IMAGE_FILE"
echo "目标镜像: $IMAGE_NAME"
echo ""

# 检查是否已有同名镜像
if docker image inspect "$IMAGE_NAME" &> /dev/null; then
    echo -e "${YELLOW}⚠️  已存在同名镜像: $IMAGE_NAME${NC}"
    read -p "是否覆盖? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "已取消导入"
        exit 0
    fi
    echo "正在删除旧镜像..."
    docker rmi "$IMAGE_NAME" 2>/dev/null || true
fi

# 导入镜像
echo -e "${BLUE}正在导入镜像，请稍候...${NC}"
echo ""

if gunzip -c "$IMAGE_FILE" | docker load; then
    echo ""
    echo -e "${GREEN}✅ 镜像导入成功！${NC}"
    echo ""
    echo "  镜像信息:"
    docker images --format "    仓库: {{.Repository}}:{{.Tag}}" "$IMAGE_NAME"
    docker images --format "    大小: {{.Size}}" "$IMAGE_NAME"
    echo ""
    echo "  快速启动:"
    echo "    cd deploy && docker compose up -d"
    echo ""
    echo "  查看镜像:"
    echo "    docker images xruntime"
else
    echo ""
    echo -e "${RED}❌ 镜像导入失败${NC}"
    exit 1
fi
