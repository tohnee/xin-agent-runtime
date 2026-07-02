#!/bin/bash
# ============================================
# XRuntime Docker 部署快速测试脚本
# ============================================
# 用于验证 Docker 部署的 XRuntime 服务是否正常工作
# ============================================

set -e

BASE_URL="${1:-http://localhost:8900}"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

test_pass() {
    echo -e "${GREEN}  ✓ $1${NC}"
    PASS_COUNT=$((PASS_COUNT + 1))
}

test_fail() {
    echo -e "${RED}  ✗ $1${NC}"
    if [ -n "$2" ]; then
        echo -e "${RED}    错误: $2${NC}"
    fi
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

test_skip() {
    echo -e "${YELLOW}  ⊘ $1${NC}"
    if [ -n "$2" ]; then
        echo -e "${YELLOW}    原因: $2${NC}"
    fi
    SKIP_COUNT=$((SKIP_COUNT + 1))
}

is_no_model_error() {
    echo "$1" | grep -q "No model provider configured"
}

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           XRuntime Docker 部署测试                           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo "测试目标: $BASE_URL"
echo ""

# ============================================
# 测试1: 健康检查
# ============================================
echo -e "${BLUE}[1/5]${NC} 健康检查..."
if curl -s "$BASE_URL/health" | grep -q "ok"; then
    test_pass "健康检查正常"
else
    test_fail "健康检查失败" "$(curl -s "$BASE_URL/health")"
fi

# ============================================
# 测试2: 就绪检查
# ============================================
echo -e "${BLUE}[2/5]${NC} 就绪检查..."
if curl -s "$BASE_URL/ready" | grep -q "ready"; then
    test_pass "就绪检查正常"
else
    test_fail "就绪检查失败" "$(curl -s "$BASE_URL/ready")"
fi

# ============================================
# 测试3: Anthropic Messages API
# ============================================
echo -e "${BLUE}[3/5]${NC} Anthropic Messages API..."
RESPONSE=$(curl -s -X POST "$BASE_URL/v1/messages" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-3-sonnet","max_tokens":100,"messages":[{"role":"user","content":"Hello"}]}' 2>&1)

if echo "$RESPONSE" | grep -q "message_start"; then
    test_pass "Messages API 流式响应正常"
elif is_no_model_error "$RESPONSE"; then
    test_skip "Messages API 已跳过" "未配置模型，配置后可用"
else
    test_fail "Messages API 响应异常" "$(echo "$RESPONSE" | head -5)"
fi

# ============================================
# 测试4: OpenCode Protocol
# ============================================
echo -e "${BLUE}[4/5]${NC} OpenCode Protocol..."
RESPONSE=$(curl -s -X POST "$BASE_URL/v1/opencode" \
    -H "Content-Type: application/json" \
    -d '{"agent":"coder","inputs":{"task":"test"}}' 2>&1)

if echo "$RESPONSE" | grep -q "session_start"; then
    test_pass "OpenCode Protocol 响应正常"
elif is_no_model_error "$RESPONSE"; then
    test_skip "OpenCode Protocol 已跳过" "未配置模型，配置后可用"
else
    test_fail "OpenCode Protocol 响应异常" "$(echo "$RESPONSE" | head -5)"
fi

# ============================================
# 测试5: Claude Code SDK
# ============================================
echo -e "${BLUE}[5/5]${NC} Claude Code SDK..."
RESPONSE=$(curl -s -X POST "$BASE_URL/v1/claude-code/query" \
    -H "Content-Type: application/json" \
    -d '{"query":"test"}' 2>&1)

if echo "$RESPONSE" | grep -q "session_start\|content_block"; then
    test_pass "Claude Code SDK 响应正常"
elif is_no_model_error "$RESPONSE"; then
    test_skip "Claude Code SDK 已跳过" "未配置模型，配置后可用"
elif [ -z "$RESPONSE" ]; then
    test_fail "Claude Code SDK 无响应"
else
    test_fail "Claude Code SDK 响应异常" "$(echo "$RESPONSE" | head -5)"
fi

# ============================================
# 测试结果汇总
# ============================================
echo ""
echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
echo -e "  测试结果: ${GREEN}$PASS_COUNT 通过${NC}, ${YELLOW}$SKIP_COUNT 跳过${NC}, ${RED}$FAIL_COUNT 失败${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"

if [ $FAIL_COUNT -eq 0 ]; then
    echo ""
    if [ $SKIP_COUNT -gt 0 ]; then
        echo -e "${YELLOW}⚠️  部分测试因未配置模型而跳过${NC}"
        echo ""
        echo "  配置模型后可运行完整测试:"
        echo "    1. 编辑 deploy/.env 设置模型 API Key"
        echo "    2. 重启服务: cd deploy && ./start.sh restart"
    else
        echo -e "${GREEN}🎉 所有测试通过！XRuntime Docker 部署运行正常！${NC}"
    fi
    echo ""
    echo "  常用命令:"
    echo "    • 查看状态: cd deploy && ./start.sh status"
    echo "    • 查看日志: cd deploy && ./start.sh logs"
    echo "    • 停止服务: cd deploy && ./start.sh stop"
    exit 0
else
    echo ""
    echo -e "${RED}❌ 部分测试失败，请检查服务状态和日志${NC}"
    echo ""
    echo "  排查步骤:"
    echo "    1. 查看服务状态: cd deploy && ./start.sh status"
    echo "    2. 查看服务日志: cd deploy && ./start.sh logs"
    echo "    3. 检查配置: cat deploy/.env"
    exit 1
fi
