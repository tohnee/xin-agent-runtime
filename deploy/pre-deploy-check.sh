#!/bin/bash
# ============================================
# XRuntime 部署预检查脚本
# 用于部署前验证所有环境依赖和配置
# ============================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

CHECK_COUNT=0
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║            XRuntime 部署预检查 v1.0                             ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# --------------------------------------------
# 辅助函数
# --------------------------------------------

check_pass() {
    echo -e "  [${GREEN}PASS${NC}] $1"
    ((PASS_COUNT++))
    ((CHECK_COUNT++))
}

check_warn() {
    echo -e "  [${YELLOW}WARN${NC}] $1"
    ((WARN_COUNT++))
    ((CHECK_COUNT++))
}

check_fail() {
    echo -e "  [${RED}FAIL${NC}] $1"
    ((FAIL_COUNT++))
    ((CHECK_COUNT++))
}

section_header() {
    echo ""
    echo "┌─────────────────────────────────────────────────────────┐"
    printf "│  %-53s │\n" "$1"
    echo "└─────────────────────────────────────────────────────────┘"
}

# --------------------------------------------
# 1. 基础环境检查
# --------------------------------------------
section_header "1. 操作系统与基础环境"

# 检查 Python 版本
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    if python3 -c "import sys; exit(sys.version_info < (3, 11))"; then
        check_pass "Python 版本: $PYTHON_VERSION (>= 3.11)"
    else
        check_fail "Python 版本过低: $PYTHON_VERSION (需要 >= 3.11)"
    fi
else
    check_fail "Python 3 未安装"
fi

# 检查 uv 包管理器
if command -v uv &> /dev/null; then
    UV_VERSION=$(uv --version | awk '{print $2}')
    check_pass "uv 包管理器: $UV_VERSION"
else
    check_warn "uv 包管理器未安装 (建议使用 uv 替代 pip)"
fi

# 检查 Docker
if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}')
    check_pass "Docker: $DOCKER_VERSION"
    
    # 检查 Docker 是否运行
    if docker info &> /dev/null; then
        check_pass "Docker 服务运行中"
    else
        check_fail "Docker 服务未启动"
    fi
else
    check_fail "Docker 未安装 (生产环境必需)"
fi

# 检查 Docker Swarm
if docker info 2>/dev/null | grep -q "Swarm: active"; then
    check_pass "Docker Swarm 已初始化"
else
    check_warn "Docker Swarm 未初始化 (集群部署需要)"
fi

# --------------------------------------------
# 2. 配置文件检查
# --------------------------------------------
section_header "2. 配置文件检查"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# 检查 .env 文件
if [ -f "deploy/.env" ]; then
    check_pass "配置文件存在: deploy/.env"
    
    # 检查关键配置项
    source deploy/.env
    
    # 检查生产模式
    if [ "$XRUNTIME_PRODUCTION" = "1" ]; then
        check_pass "生产模式已启用 (XRUNTIME_PRODUCTION=1)"
    else
        check_warn "生产模式未启用 (XRUNTIME_PRODUCTION=0)"
    fi
    
    # 检查 API Keys
    if [ -n "$XRUNTIME_API_KEYS" ] && [ "$XRUNTIME_API_KEYS" != "your-api-key-1,your-api-key-2" ]; then
        check_pass "API Key 已配置"
    else
        check_fail "API Key 未配置或使用默认值"
    fi
    
    # 检查 JWT Secret
    if [ -n "$XRUNTIME_JWT_SECRET" ] && [ "$XRUNTIME_JWT_SECRET" != "your-jwt-secret-at-least-32-characters" ]; then
        JWT_LEN=${#XRUNTIME_JWT_SECRET}
        if [ $JWT_LEN -ge 32 ]; then
            check_pass "JWT Secret 已配置 (长度: $JWT_LEN)"
        else
            check_warn "JWT Secret 长度不足 (建议至少 32 字符)"
        fi
    else
        check_fail "JWT Secret 未配置"
    fi
    
    # 检查 Redis 密码
    if [ -n "$XRUNTIME_REDIS_PASSWORD" ] && [ "$XRUNTIME_REDIS_PASSWORD" != "your-secure-redis-password" ]; then
        check_pass "Redis 密码已配置"
    else
        check_fail "Redis 密码未配置"
    fi
    
    # 检查 Workspace 后端
    if [ "$XRUNTIME_WORKSPACE_BACKEND" = "docker" ]; then
        check_pass "Workspace 后端: Docker (安全隔离)"
    elif [ "$XRUNTIME_WORKSPACE_BACKEND" = "e2b" ]; then
        check_pass "Workspace 后端: E2B (云沙箱)"
    else
        check_warn "Workspace 后端: Local (仅开发环境)"
    fi
else
    check_warn "未找到 deploy/.env (从 .env.example 复制配置)"
fi

# 检查 Stack 配置
if [ -f "deploy/xruntime-swarm-stack.yml" ]; then
    check_pass "Stack 配置文件存在"
else
    check_fail "Stack 配置文件不存在: deploy/xruntime-swarm-stack.yml"
fi

# --------------------------------------------
# 3. 资源与网络检查
# --------------------------------------------
section_header "3. 资源与网络检查"

# 检查内存
if command -v free &> /dev/null; then
    TOTAL_MEM=$(free -g | awk '/^Mem:/{print $2}')
    if [ "$TOTAL_MEM" -ge 4 ]; then
        check_pass "系统内存: ${TOTAL_MEM}GB (建议 >= 4GB)"
    else
        check_warn "系统内存: ${TOTAL_MEM}GB (建议 >= 4GB)"
    fi
else
    check_warn "无法检查内存 (非 Linux 环境)"
fi

# 检查 CPU 核心数
if command -v nproc &> /dev/null; then
    CPU_CORES=$(nproc)
    if [ "$CPU_CORES" -ge 2 ]; then
        check_pass "CPU 核心: $CPU_CORES (建议 >= 2)"
    else
        check_warn "CPU 核心: $CPU_CORES (建议 >= 2)"
    fi
else
    check_warn "无法检查 CPU 核心数"
fi

# 检查磁盘空间
REQUIRED_DISK=10  # GB
if command -v df &> /dev/null; then
    AVAIL_DISK=$(df -P . | awk 'NR==2 {print int($4/1024/1024)}')
    if [ "$AVAIL_DISK" -ge "$REQUIRED_DISK" ]; then
        check_pass "可用磁盘: ${AVAIL_DISK}GB (建议 >= $REQUIRED_DISK GB)"
    else
        check_warn "可用磁盘: ${AVAIL_DISK}GB (建议 >= $REQUIRED_DISK GB)"
    fi
fi

# 检查端口占用
check_port() {
    local port=$1
    local name=$2
    if command -v netstat &> /dev/null; then
        if netstat -tuln 2>/dev/null | grep -q ":$port "; then
            check_warn "端口 $port ($name) 已被占用"
        else
            check_pass "端口 $port ($name) 可用"
        fi
    else
        check_warn "无法检查端口 $port (netstat 未安装)"
    fi
}

check_port 8900 "XRuntime Gateway"
check_port 6379 "Redis"
check_port 443 "HTTPS"
check_port 80 "HTTP"

# --------------------------------------------
# 4. 代码与依赖检查
# --------------------------------------------
section_header "4. 代码与依赖检查"

# 检查 Python 依赖
if [ -f "pyproject.toml" ]; then
    check_pass "pyproject.toml 存在"
    
    # 检查能否导入关键模块
    if python3 -c "import xruntime" 2>/dev/null; then
        check_pass "XRuntime 模块可导入"
    else
        check_warn "XRuntime 模块导入失败 (可能需要安装依赖)"
    fi
else
    check_fail "pyproject.toml 不存在"
fi

# 检查测试文件
TEST_COUNT=$(find tests/xruntime -name "test_*.py" 2>/dev/null | wc -l)
if [ "$TEST_COUNT" -gt 0 ]; then
    check_pass "测试文件: $TEST_COUNT 个"
else
    check_warn "未找到测试文件"
fi

# 运行快速测试
if python3 -m pytest tests/xruntime/test_config.py tests/xruntime/test_extension.py -q --tb=no 2>/dev/null; then
    check_pass "基础测试通过"
else
    check_warn "基础测试失败 (可能缺少依赖)"
fi

# --------------------------------------------
# 5. 安全检查
# --------------------------------------------
section_header "5. 安全检查"

# 检查文件权限
if [ -f "deploy/.env" ]; then
    ENV_PERMS=$(stat -c %a deploy/.env 2>/dev/null || stat -f %OLp deploy/.env 2>/dev/null)
    if [ "$ENV_PERMS" = "600" ] || [ "$ENV_PERMS" = "400" ]; then
        check_pass ".env 文件权限安全 ($ENV_PERMS)"
    else
        check_warn ".env 文件权限过宽 ($ENV_PERMS, 建议设置为 600)"
    fi
fi

# 检查是否有硬编码密钥
SECRET_COUNT=$(grep -r "sk-" src/xruntime --include="*.py" 2>/dev/null | wc -l)
if [ "$SECRET_COUNT" -eq 0 ]; then
    check_pass "源代码中无硬编码密钥"
else
    check_warn "源代码中发现 $SECRET_COUNT 处可能的硬编码密钥"
fi

# 检查 Docker socket 权限
if [ -S /var/run/docker.sock ]; then
    DOCKER_PERMS=$(stat -c %a /var/run/docker.sock 2>/dev/null || stat -f %OLp /var/run/docker.sock 2>/dev/null)
    if [ "$DOCKER_PERMS" = "660" ] || [ "$DOCKER_PERMS" = "666" ]; then
        check_pass "Docker socket 权限正常"
    else
        check_warn "Docker socket 权限: $DOCKER_PERMS"
    fi
fi

# --------------------------------------------
# 6. 目录结构检查
# --------------------------------------------
section_header "6. 必要目录检查"

REQUIRED_DIRS=(
    "/var/lib/xruntime/workspaces"
    "/var/lib/xruntime/knowledge"
    "/var/log/xruntime"
    "/etc/xruntime/skills"
)

for dir in "${REQUIRED_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        check_pass "目录存在: $dir"
    else
        check_warn "目录不存在: $dir (将自动创建)"
    fi
done

# --------------------------------------------
# 总结
# --------------------------------------------
section_header "检查总结"

echo ""
echo "  总计检查: $CHECK_COUNT 项"
echo -e "  ${GREEN}通过: $PASS_COUNT${NC}"
echo -e "  ${YELLOW}警告: $WARN_COUNT${NC}"
echo -e "  ${RED}失败: $FAIL_COUNT${NC}"
echo ""

if [ "$FAIL_COUNT" -eq 0 ]; then
    echo -e "  ${GREEN}✓ 所有关键检查通过，可以部署！${NC}"
    
    if [ "$WARN_COUNT" -gt 0 ]; then
        echo -e "  ${YELLOW}⚠ 请注意上述警告项${NC}"
    fi
    
    echo ""
    echo "  部署命令："
    echo "    cd deploy/"
    echo "    docker stack deploy -c xruntime-swarm-stack.yml xruntime"
    echo ""
    echo "  查看服务："
    echo "    docker stack services xruntime"
    echo ""
    echo "  查看日志："
    echo "    docker service logs xruntime_gateway -f"
    echo ""
    exit 0
else
    echo -e "  ${RED}✗ 存在失败项，请修复后重试部署${NC}"
    echo ""
    exit 1
fi
