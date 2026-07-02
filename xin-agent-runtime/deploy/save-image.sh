#!/bin/bash
# ============================================
# XRuntime Docker 镜像导出脚本
# ============================================
# 用于将本地 xruntime:latest 镜像导出为 tar.gz
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="${1:-xruntime:latest}"
OUTPUT_FILE="${2:-$SCRIPT_DIR/xruntime-image.tar.gz}"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        XRuntime Docker 镜像导出工具                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ 错误: 未找到 Docker，请先安装 Docker${NC}"
    exit 1
fi

# 检查镜像是否存在
if ! docker image inspect "$IMAGE_NAME" &> /dev/null; then
    echo -e "${RED}❌ 错误: 镜像不存在: $IMAGE_NAME${NC}"
    echo ""
    echo "  请先构建镜像:"
    echo "    cd deploy && docker compose build"
    exit 1
fi

echo "源镜像: $IMAGE_NAME"
echo "输出文件: $OUTPUT_FILE"
echo ""

# 获取镜像大小
IMAGE_SIZE=$(docker images --format "{{.Size}}" "$IMAGE_NAME")
echo "镜像大小: $IMAGE_SIZE"
echo ""

# 如果输出文件已存在，询问是否覆盖
if [ -f "$OUTPUT_FILE" ]; then
    echo -e "${YELLOW}⚠️  输出文件已存在: $OUTPUT_FILE${NC}"
    read -p "是否覆盖? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "已取消导出"
        exit 0
    fi
    rm -f "$OUTPUT_FILE"
fi

# 导出镜像
echo -e "${BLUE}正在导出镜像，请稍候...${NC}"
echo ""

START_TIME=$(date +%s)

if docker save "$IMAGE_NAME" | gzip > "$OUTPUT_FILE"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    # 获取文件大小
    FILE_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)

    echo ""
    echo -e "${GREEN}✅ 镜像导出成功！${NC}"
    echo ""
    echo "  输出文件: $OUTPUT_FILE"
    echo "  压缩后大小: $FILE_SIZE"
    echo "  耗时: ${DURATION}秒"
    echo ""
    echo "  导入命令:"
    echo "    ./load-image.sh"
    echo ""
    echo "  上传到 GitHub Release 建议:"
    echo "    - 文件名添加版本号: xruntime-image-v1.0.0.tar.gz"
    echo "    - 计算 SHA256: shasum -a 256 $OUTPUT_FILE"
else
    echo ""
    echo -e "${RED}❌ 镜像导出失败${NC}"
    rm -f "$OUTPUT_FILE"
    exit 1
fi
