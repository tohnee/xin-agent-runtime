#!/usr/bin/env bash
# Xin Agent Runtime — CI/CD 全量测试脚本
# 运行所有 583+ 测试，确保 push 前代码质量
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║     Xin Agent Runtime — CI/CD 全量测试       ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

cd "$PROJECT_DIR"

# ── Step 1: Lint (black + flake8) ──
echo "━━ Step 1: Code Formatting (black) ━━"
if python3 -m black --line-length=79 --check src/xruntime tests/xruntime 2>&1; then
    echo -e "  ${GREEN}black: PASS${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}black: FAIL (run: black --line-length=79 src/xruntime tests/xruntime)${NC}"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "━━ Step 2: Lint (flake8) ━━"
FLAKE8_ERRORS=$(python3 -m flake8 --extend-ignore=E203,W503,E704 src/xruntime 2>&1 | wc -l)
if [ "$FLAKE8_ERRORS" -eq 0 ]; then
    echo -e "  ${GREEN}flake8: PASS${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}flake8: FAIL ($FLAKE8_ERRORS issues)${NC}"
    python3 -m flake8 --extend-ignore=E203,W503,E704 src/xruntime 2>&1 | head -10
    FAIL=$((FAIL + 1))
fi

# ── Step 2: XRuntime 企业测试 (must pass) ──
echo ""
echo "━━ Step 3: XRuntime Enterprise Tests ━━"
if python3 -m pytest tests/xruntime -q --tb=short 2>&1 | tail -5; then
    COUNT=$(python3 -m pytest tests/xruntime -q 2>&1 | grep -oP '\d+(?= passed)' | tail -1)
    echo -e "  ${GREEN}xruntime: PASS ($COUNT tests)${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}xruntime: FAIL${NC}"
    FAIL=$((FAIL + 1))
fi

# ── Step 3: 集成测试 ──
echo ""
echo "━━ Step 4: Integration Tests ━━"
if python3 -m pytest tests/xruntime/integration -q --tb=short 2>&1 | tail -5; then
    echo -e "  ${GREEN}integration: PASS${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}integration: FAIL${NC}"
    FAIL=$((FAIL + 1))
fi

# ── Step 4: AgentScope 原始测试 (best-effort) ──
echo ""
echo "━━ Step 5: AgentScope Original Tests (best-effort) ━━"
if python3 -m pytest tests/ -q --tb=short \
    --ignore=tests/xruntime \
    --ignore=tests/service_team_tools_test.py \
    --ignore=tests/mcp_streamable_http_client_test.py \
    --ignore=tests/mcp_sse_client_test.py \
    2>&1 | tail -5; then
    echo -e "  ${GREEN}agentscope: PASS${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${YELLOW}agentscope: some failures (best-effort, non-blocking)${NC}"
    # Don't count as hard failure
fi

# ── Step 5: 模块导入检查 ──
echo ""
echo "━━ Step 6: Module Import Check ━━"
MODULES=(
    "xruntime"
    "xruntime._gateway._extension"
    "xruntime._runtime._skills"
    "xruntime._runtime._memory._store"
    "xruntime._runtime._memory._hybrid_retriever"
    "xruntime._runtime._subagents"
    "xruntime._runtime._middleware._loop_detection"
    "xruntime._runtime._middleware._llm_error_handling"
    "xruntime._runtime._middleware._langfuse_tracer"
    "xruntime._infra._metrics"
)
IMPORT_FAIL=0
for mod in "${MODULES[@]}"; do
    if python3 -c "import $mod" 2>/dev/null; then
        echo -e "  ${GREEN}✓ $mod${NC}"
    else
        echo -e "  ${RED}✗ $mod${NC}"
        IMPORT_FAIL=$((IMPORT_FAIL + 1))
    fi
done
if [ "$IMPORT_FAIL" -eq 0 ]; then
    echo -e "  ${GREEN}imports: PASS${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}imports: FAIL ($IMPORT_FAIL modules)${NC}"
    FAIL=$((FAIL + 1))
fi

# ── Step 6: Extension 完整性检查 ──
echo ""
echo "━━ Step 7: Extension Integrity Check ━━"
if python3 -c "
import asyncio
from xruntime._gateway._extension import create_xruntime_extension
ext = create_xruntime_extension()
assert 'skill_registry' in ext
assert 'memory_store' in ext
assert 'subagent_executor' in ext
assert 'extra_agent_middlewares' in ext
async def check():
    mws = await ext['extra_agent_middlewares']('u','a','s')
    assert len(mws) == 7
asyncio.run(check())
print('  Extension OK: 3 modules + 7 middlewares')
" 2>&1; then
    echo -e "  ${GREEN}extension: PASS${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}extension: FAIL${NC}"
    FAIL=$((FAIL + 1))
fi

# ── 汇总 ──
echo ""
echo "┌─────────────────────────────────────────────┐"
printf "│  ${GREEN}PASS${NC}: %d  ${RED}FAIL${NC}: %d  Total: %d  │\n" "$PASS" "$FAIL" "$((PASS + FAIL))"
echo "└─────────────────────────────────────────────┘"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo -e "${RED}存在失败项，请修复后再 push。${NC}"
    exit 1
else
    echo ""
    echo -e "${GREEN}所有检查通过！可以安全 push。${NC}"
    exit 0
fi
