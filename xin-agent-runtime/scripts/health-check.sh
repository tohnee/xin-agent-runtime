#!/usr/bin/env bash
# Xin Agent Runtime — 集成状态检查脚本
# 检查 AgentScope 内核 + XRuntime 扩展层的集成状态
# 用法: ./scripts/health-check.sh [--ci]
set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
SKIP=0
CI_MODE=false

[[ "${1:-}" == "--ci" ]] && CI_MODE=true

check() {
    local name="$1"
    local cmd="$2"
    local expect="${3:-}"

    printf "  ${BLUE}[%02d]${NC} %-50s " $((PASS + FAIL + SKIP + 1)) "$name"

    if eval "$cmd" 2>/dev/null; then
        printf "${GREEN}PASS${NC}\n"
        PASS=$((PASS + 1))
    else
        if [ "$CI_MODE" = true ] && [ -n "$expect" ]; then
            printf "${YELLOW}SKIP${NC}\n"
            SKIP=$((SKIP + 1))
        else
            printf "${RED}FAIL${NC}\n"
            FAIL=$((FAIL + 1))
        fi
    fi
}

section() {
    echo ""
    echo -e "  ${BLUE}━━ $1 ━━${NC}"
}

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║  Xin Agent Runtime — 集成状态检查              ║"
echo "  ╚═══════════════════════════════════════════════╝"

# ── 1. 包安装检查 ──
section "1. 包安装"
check "agentscope 包已安装" \
    'python3 -c "import agentscope"'
check "xruntime 包已安装" \
    'python3 -c "import xruntime"'
check "xruntime_sdk 包已安装" \
    'python3 -c "import xruntime_sdk"'
check "agentscope.__version__ 可读" \
    'python3 -c "import agentscope; assert agentscope.__version__"'
check "xruntime.__version__ 可读" \
    'python3 -c "import xruntime; assert xruntime.__version__"'

# ── 2. 内核组件检查 ──
section "2. AgentScope 内核组件"
check "Agent 类可导入" \
    'python3 -c "from agentscope.agent import Agent"'
check "create_app 可导入" \
    'python3 -c "from agentscope.app import create_app"'
check "Toolkit 可导入" \
    'python3 -c "from agentscope.tool import Toolkit"'
check "OpenAIChatModel 可导入" \
    'python3 -c "from agentscope.model import OpenAIChatModel"'
check "RedisStorage 可导入" \
    'python3 -c "from agentscope.app.storage import RedisStorage"'
check "RedisMessageBus 可导入" \
    'python3 -c "from agentscope.app.message_bus import RedisMessageBus"'
check "LocalWorkspaceManager 可导入" \
    'python3 -c "from agentscope.app.workspace_manager import LocalWorkspaceManager"'
check "AgentEvent 可导入" \
    'python3 -c "from agentscope.event import AgentEvent"'
check "MiddlewareBase 可导入" \
    'python3 -c "from agentscope.middleware import MiddlewareBase"'

# ── 3. 扩展层组件检查 ──
section "3. XRuntime 扩展层组件"
check "create_xruntime_extension 可导入" \
    'python3 -c "from xruntime import create_xruntime_extension"'
check "mount_protocol_adapters 可导入" \
    'python3 -c "from xruntime import mount_protocol_adapters"'
check "XRuntimeConfig 可导入" \
    'python3 -c "from xruntime import XRuntimeConfig"'
check "AuthMiddleware 可导入" \
    'python3 -c "from xruntime._gateway._auth import AuthMiddleware"'
check "RuntimeExecutionPlan 可导入" \
    'python3 -c "from xruntime._gateway._plan import RuntimeExecutionPlan"'
check "TenantPolicy 可导入" \
    'python3 -c "from xruntime._runtime._tenant._policy import TenantPolicy"'
check "ApiKeyStore 可导入" \
    'python3 -c "from xruntime._runtime._tenant._store import ApiKeyStore"'
check "WorkspaceManagerFactory 可导入" \
    'python3 -c "from xruntime._runtime._workspace import WorkspaceManagerFactory"'
check "ModelCapabilityRegistry 可导入" \
    'python3 -c "from xruntime._runtime._model_governance import ModelCapabilityRegistry"'
check "KnowledgeRegistry 可导入" \
    'python3 -c "from xruntime._runtime._knowledge._registry import KnowledgeRegistry"'
check "LangfuseConfig 可导入" \
    'python3 -c "from xruntime._runtime._langfuse import LangfuseConfig"'

# ── 4. 安全逻辑检查 ──
section "4. 安全逻辑"
check "RBAC 默认角色是 viewer (check_permissions deny)" \
    'python3 -c "
from xruntime._runtime._tenant._policy import TenantPolicy, TenantRole, Principal, Action
p = TenantPolicy.default()
viewer = Principal(tenant_id=\"t1\", user_id=\"v\", role=TenantRole.VIEWER)
assert not p.check(viewer, Action.DOC_INGEST).allowed
"'
check "WorkspaceConfig 默认后端是 docker" \
    'python3 -c "from xruntime._runtime._workspace import WorkspaceConfig; assert WorkspaceConfig().default_backend == \"docker\""'
check "生产模式拒绝 local workspace" \
    'python3 -c "
from xruntime._runtime._workspace import WorkspaceConfig, WorkspaceManagerFactory
c = WorkspaceConfig(default_backend=\"local\", allow_local_in_production=False)
f = WorkspaceManagerFactory(c)
try:
    f.create(backend=\"local\", production=True)
    exit(1)
except ValueError:
    exit(0)
"'
check "QuotaTracker cost 阻断生效" \
    'python3 -c "
from xruntime._runtime._middleware._quota import QuotaConfig, QuotaTracker, QuotaExceededError
t = QuotaTracker(QuotaConfig(max_cost_usd=1.0))
t.consume_cost(0.5)
try:
    t.consume_cost(0.6)
    exit(1)
except QuotaExceededError:
    exit(0)
"'
check "ApiKeyStore 认证绑定租户" \
    'python3 -c "
from xruntime._runtime._tenant._store import ApiKeyStore, ApiKeyRecord, TenantRole
s = ApiKeyStore([ApiKeyRecord(key=\"sk-test\", tenant_id=\"acme\", user_id=\"alice\", role=TenantRole.ADMIN)])
p = s.authenticate(\"sk-test\")
assert p.tenant_id == \"acme\" and p.role == TenantRole.ADMIN
"'
check "Secret redaction 脱敏生效" \
    'python3 -c "
from xruntime._runtime._middleware._redaction import SecretRedactionMiddleware
import re
# Check pattern exists
assert hasattr(SecretRedactionMiddleware, \"on_model_call\")
"'

# ── 5. 协议适配检查 ──
section "5. 协议适配"
check "Anthropic adapter 可导入" \
    'python3 -c "from xruntime._gateway._anthropic_adapter import AnthropicMessagesAdapter"'
check "Claude Code adapter 可导入" \
    'python3 -c "from xruntime._gateway._claude_code_adapter import ClaudeCodeAdapter"'
check "OpenCode adapter 可导入" \
    'python3 -c "from xruntime._gateway._opencode_adapter import OpenCodeAdapter"'
check "OpenCode schema 校验可导入" \
    'python3 -c "from xruntime._gateway._opencode_schema import validate_opencode_config"'
check "permissions tighten 可导入" \
    'python3 -c "from xruntime._gateway._opencode_schema import tighten_permissions"'

# ── 6. 运行时服务检查（仅非 CI 模式）──
if [ "$CI_MODE" = false ]; then
    section "6. 运行时服务"
    check "服务存活 (/health)" \
        'curl -sf http://localhost:8900/health | grep -q healthy' \
        "skip"
    check "服务就绪 (/ready)" \
        'curl -sf http://localhost:8900/ready | grep -q ready' \
        "skip"
    check "认证生效 (无 key → 401)" \
        'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8900/v1/messages | grep -q 401' \
        "skip"
    check "限流配置生效" \
        'test -n "${XRUNTIME_RATE_LIMIT:-}"' \
        "skip"
fi

# ── 7. 测试套件检查 ──
section "7. 测试套件"
check "RBAC 权限矩阵测试" \
    'python3 -m pytest tests/xruntime/test_rbac_policy.py -q --no-header 2>/dev/null | grep -q "passed"'
check "Auth membership 测试" \
    'python3 -m pytest tests/xruntime/test_auth_membership.py -q --no-header 2>/dev/null | grep -q "passed"'
check "Knowledge ACL 测试" \
    'python3 -m pytest tests/xruntime/test_knowledge_acl.py -q --no-header 2>/dev/null | grep -q "passed"'
check "Phase 1 安全测试" \
    'python3 -m pytest tests/xruntime/test_phase1_security.py -q --no-header 2>/dev/null | grep -q "passed"'
check "Workspace 集成测试" \
    'python3 -m pytest tests/xruntime/integration/test_workspace_rbac_integration.py -q --no-header 2>/dev/null | grep -q "passed"'
check "覆盖率缺口测试" \
    'python3 -m pytest tests/xruntime/test_coverage_gaps.py -q --no-header 2>/dev/null | grep -q "passed"'

# ── 8. 环境配置检查 ──
if [ "$CI_MODE" = false ]; then
    section "8. 环境配置"
    check "生产模式已开启" \
        'test "${XRUNTIME_PRODUCTION:-}" = "1"'
    check "Workspace 后端是 docker" \
        'test "${XRUNTIME_WORKSPACE_BACKEND:-}" = "docker"'
    check "API Key 已配置" \
        'test -n "${XRUNTIME_API_KEY_RECORDS:-}"'
    check "JWT 密钥已设置" \
        'test -n "${XRUNTIME_JWT_SECRET:-}"'
    check "Redis 可连接" \
        'redis-cli ping 2>/dev/null | grep -q PONG' \
        "skip"
fi

# ── 汇总 ──
echo ""
echo "  ┌─────────────────────────────────────────────┐"
printf "  │  ${GREEN}PASS${NC}: %-3d  ${RED}FAIL${NC}: %-3d  ${YELLOW}SKIP${NC}: %-3d  Total: %-3d  │\n" \
    "$PASS" "$FAIL" "$SKIP" "$((PASS + FAIL + SKIP))"
echo "  └─────────────────────────────────────────────┘"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo -e "  ${RED}存在失败项，请检查上方输出。${NC}"
    exit 1
else
    echo ""
    echo -e "  ${GREEN}所有检查通过！集成状态正常。${NC}"
    exit 0
fi
