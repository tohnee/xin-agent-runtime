<p align="center">
  <h1 align="center">Xin Agent Runtime</h1>
  <p align="center">企业级 Agent 开发运行时底座</p>
</p>

<p align="center">
    <a href="https://github.com/tohnee/xin-agent-runtime">
        <img
            src="https://img.shields.io/badge/python-3.11+-blue?logo=python"
            alt="python"
        />
    </a>
    <a href="https://github.com/tohnee/xin-agent-runtime/actions">
        <img
            src="https://img.shields.io/badge/CI-passing-brightgreen?logo=github"
            alt="ci"
        />
    </a>
    <a href="./LICENSE">
        <img
            src="https://img.shields.io/badge/license-Apache--2.0-black"
            alt="license"
        />
    </a>
    <a href="https://github.com/tohnee/xin-agent-runtime">
        <img
            src="https://img.shields.io/badge/tests-446%20passed-brightgreen"
            alt="tests"
        />
    </a>
    <a href="https://github.com/tohnee/xin-agent-runtime">
        <img
            src="https://img.shields.io/badge/coverage-86%25-green"
            alt="coverage"
        />
    </a>
</p>

<span align="center">

[**中文文档**](./README_zh.md) | [**操作手册**](./docs/xruntime/OPS-GUIDE.md) | [**SDK 指南**](./docs/xruntime/SDK-GUIDE.md) | [**安全架构**](./docs/xruntime/FINAL-SECURITY-ARCHITECTURE.md) | [**开发路线**](./docs/xruntime/ENTERPRISE-RUNTIME-ROADMAP.md)

</span>

---

## 什么是 Xin Agent Runtime？

Xin Agent Runtime 是一个**企业级 Agent 开发运行时底座**，基于 AgentScope 执行内核与 XRuntime 企业扩展层联合开发，提供从协议接入到生产部署的完整能力链。

### 核心架构

```
Client SDK / Protocol (Anthropic / Claude Code / OpenCode)
    ↓
XRuntime Gateway (Auth + RateLimit + Protocol Adapter)
    ↓
RuntimeExecutionPlan (统一执行计划)
    ↓
Tenant / RBAC / Model / Workspace / Knowledge Policy
    ↓
AgentScope ChatService / Agent / Workspace / Model
    ↓
AgentEvent Stream
    ↓
XRuntime Protocol Adapter → Client Response
```

### 核心能力

| 能力 | 说明 |
|------|------|
| **多协议接入** | Anthropic Messages API、Claude Code SDK、OpenCode SDK 三种协议入口 |
| **多租户隔离** | Redis key-prefix 隔离 + per-request tenant resolution + anti-spoofing |
| **RBAC 权限** | Owner/Admin/Contributor/Viewer 四级角色 + 16 个细粒度 Action |
| **知识库治理** | LLM-Wiki AOT 编译 + BM25 检索 + per-KB ACL + audit log |
| **企业中间件** | Audit、Quota、RBAC、SecretRedaction、KnowledgeMiddleware |
| **Workspace 沙箱** | Local/Docker/E2B 后端 + 生产拒绝 local + path traversal guard |
| **模型治理** | ModelCapabilityRegistry + ModelRouter + tenant allowlist + fallback |
| **可观测性** | OTel tracing + Prometheus metrics + Langfuse + audit log |
| **统一执行计划** | RuntimeExecutionPlan 统一三协议 + permissions 只能收紧 |

---

## 快速开始

### 安装

> Xin Agent Runtime 需要 **Python 3.11** 或更高版本。

```bash
# 克隆仓库
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime

# 安装（含开发依赖）
uv pip install -e ".[dev]"
# 或
pip install -e ".[dev]"
```

### 启动服务

```bash
# 设置环境变量
export XRUNTIME_API_KEYS="sk-your-key-1,sk-your-key-2"
export XRUNTIME_API_KEY_RECORDS='[{"key":"sk-admin","tenant_id":"acme","user_id":"alice","role":"admin","kb_ids":["kb1"]}]'
export XRUNTIME_WORKSPACE_BACKEND=docker
export XRUNTIME_PRODUCTION=1

# 启动
python -m xruntime._server
```

### 使用 SDK

```python
import asyncio
from xruntime_sdk import create_client

async def main():
    client = create_client(
        "http://localhost:8900",
        tenant_id="acme",
        api_key="sk-admin",
    )

    # Anthropic 协议
    result = await client.query(
        protocol="anthropic",
        prompt="List all files in the project",
        model="claude-sonnet-4-20250514",
    )

    # Claude Code SDK 协议
    result = await client.query(
        protocol="claude_code",
        prompt="Fix the bug in auth.py",
        options={
            "allowed_tools": ["Read", "Edit", "Bash"],
            "permission_mode": "acceptEdits",
            "max_turns": 10,
            "max_budget_usd": 5.0,
        },
    )

asyncio.run(main())
```

### 开发 Agent

```python
from agentscope.agent import Agent
from agentscope.tool import Toolkit, Bash, Read, Write, Edit
from agentscope.model import OpenAIChatModel
from agentscope.credential import ApiKeyCredential
from agentscope.message import UserMsg
import asyncio, os

async def main():
    agent = Agent(
        name="Friday",
        system_prompt="You're a helpful assistant.",
        model=OpenAIChatModel(
            credential=ApiKeyCredential(
                api_key=os.environ["OPENAI_API_KEY"]
            ),
            model="gpt-4o",
        ),
        toolkit=Toolkit(tools=[Bash(), Read(), Write(), Edit()]),
    )
    async for evt in agent.reply_stream(UserMsg("user", "Hi!")):
        print(evt)

asyncio.run(main())
```

---

## 安全架构

Xin Agent Runtime 实现了 10 层防御体系：

| 层 | 防御机制 | 覆盖率 |
|----|----------|--------|
| L1 网关 | AuthMiddleware (API Key + JWT) | 100% |
| L2 反欺骗 | Principal tenant_id 覆盖客户端值 | Tested |
| L3 RBAC | 四级角色权限矩阵 (默认 deny) | 95.7% |
| L4 KB ACL | per-KB ownership + grant | 93.6% |
| L5 工具权限 | check_permissions 强制 kb:query/doc:ingest | 73% |
| L6 配额 | token/cost 超限阻断 | 100% |
| L7 脱敏 | secret redaction (audit + ingest + langfuse) | 82-94% |
| L8 沙箱 | WorkspaceManagerFactory (生产拒绝 local) | 100% |
| L9 审计 | knowledge-audit.jsonl + AuditMiddleware | 83.5% |
| L10 限流 | RateLimitMiddleware (429) | 90.2% |

详见 [安全架构文档](./docs/xruntime/FINAL-SECURITY-ARCHITECTURE.md)。

---

## 项目结构

```
xin-agent-runtime/
├── src/
│   ├── agentscope/          # 执行内核（Agent/Model/Tool/Workspace/Storage）
│   ├── xruntime/            # 企业扩展层
│   │   ├── _gateway/        # 协议适配 + 认证 + 限流 + 执行计划
│   │   ├── _runtime/        # 中间件 + 知识库 + 租户 + Workspace + 模型治理
│   │   ├── _infra/          # 租户隔离 + 指标
│   │   └── _config.py       # YAML 配置
│   └── xruntime_sdk/        # 客户端 SDK
├── tests/
│   └── xruntime/            # 446 个测试 (unit/contract/integration/e2e)
├── docs/
│   ├── xruntime/            # 操作手册 + 安全文档 + 路线图
│   └── adr/                 # 架构决策记录
└── .github/workflows/       # CI/CD 流水线
```

---

## CI/CD

| 工作流 | 说明 |
|--------|------|
| `xruntime-ci.yml` | Lint (flake8+black) + Test (coverage≥80%) + Security Gate |
| `unittest.yml` | 全项目测试 |
| `pre-commit.yml` | 代码格式检查 |

---

## 开发路线

| Milestone | 状态 | 说明 |
|-----------|------|------|
| M0 测试护栏 | ✅ | 文档 + 测试分类 + contract 固化 |
| M1 RBAC 租户 | ✅ | 四级角色 + 默认 deny + auth 绑定 |
| M2 Knowledge RBAC | ✅ | KB ACL + 工具权限 + 租户隔离 |
| M3 LLM-Wiki | ✅ | BM25 + audit + redaction + scoped layout |
| M4 ExecutionPlan | ✅ | 三协议统一 + permissions 收紧 |
| M5 Workspace | ✅ | 生产 docker + path guard |
| M6 Model Governance | ✅ | Capability + Router + allowlist |
| M7 Langfuse | ✅ | NoopExporter + trace + redaction |

详见 [开发路线图](./docs/xruntime/ENTERPRISE-RUNTIME-ROADMAP.md)。

---

## 贡献

欢迎提交 Issue 和 Pull Request。请先阅读 [贡献指南](./CONTRIBUTING.md)。

开发前请安装 pre-commit：
```bash
pre-commit install
```

## 许可证

Apache License 2.0
