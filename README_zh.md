<p align="center">
  <h1 align="center">Xin Agent Runtime</h1>
  <p align="center">企业级 Agent 开发运行时底座</p>
</p>

<p align="center">
    <a href="https://github.com/tohnee/xin-agent-runtime">
        <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python" alt="python" />
    </a>
    <a href="https://github.com/tohnee/xin-agent-runtime/actions">
        <img src="https://img.shields.io/badge/CI-passing-brightgreen?logo=github" alt="ci" />
    </a>
    <a href="./LICENSE">
        <img src="https://img.shields.io/badge/license-Apache--2.0-black" alt="license" />
    </a>
    <a href="https://github.com/tohnee/xin-agent-runtime">
        <img src="https://img.shields.io/badge/tests-446%20passed-brightgreen" alt="tests" />
    </a>
    <a href="https://github.com/tohnee/xin-agent-runtime">
        <img src="https://img.shields.io/badge/coverage-86%25-green" alt="coverage" />
    </a>
</p>

<span align="center">

[**English**](./README.md) | [**操作手册**](./docs/xruntime/OPS-GUIDE.md) | [**SDK 指南**](./docs/xruntime/SDK-GUIDE.md) | [**安全架构**](./docs/xruntime/FINAL-SECURITY-ARCHITECTURE.md) | [**开发路线**](./docs/xruntime/ENTERPRISE-RUNTIME-ROADMAP.md)

</span>

---

## 什么是 Xin Agent Runtime？

Xin Agent Runtime 是一个**企业级 Agent 开发运行时底座**，基于 AgentScope 执行内核与 XRuntime 企业扩展层联合开发，提供从协议接入到生产部署的完整能力链。

### 核心能力

| 能力 | 说明 |
|------|------|
| **多协议接入** | Anthropic Messages API、Claude Code SDK、OpenCode SDK |
| **多租户隔离** | Redis key-prefix + per-request tenant + anti-spoofing |
| **RBAC 权限** | Owner/Admin/Contributor/Viewer 四级角色 + 16 个 Action |
| **知识库治理** | LLM-Wiki AOT 编译 + BM25 + per-KB ACL + audit |
| **Workspace 沙箱** | Local/Docker/E2B + 生产拒绝 local + path guard |
| **模型治理** | CapabilityRegistry + Router + allowlist + fallback |
| **可观测性** | OTel + Prometheus + Langfuse + audit log |

### 架构定位

```
Client SDK → XRuntime Gateway (Auth+RateLimit+Adapter)
    → RuntimeExecutionPlan → Tenant/RBAC/Model/Workspace/Knowledge
    → AgentScope ChatService/Agent/Workspace/Model → AgentEvent Stream
    → Protocol Adapter → Client Response
```

**AgentScope** 提供运行内核（Agent + Model + Tool + Workspace + Storage）。
**XRuntime** 提供企业外壳（协议转换 + 安全 + 多租户 + 审计 + 知识库）。
二者联合构成 **Xin Agent Runtime**。

---

## 快速开始

### 安装

```bash
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime
uv pip install -e ".[dev]"
```

### 启动服务

```bash
export XRUNTIME_API_KEYS="sk-your-key"
export XRUNTIME_WORKSPACE_BACKEND=docker
export XRUNTIME_PRODUCTION=1
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
    result = await client.query(
        protocol="anthropic",
        prompt="Hello!",
        model="claude-sonnet-4-20250514",
    )

asyncio.run(main())
```

---

## 安全架构

10 层防御体系，核心安全文件覆盖率 92%。详见 [安全架构文档](./docs/xruntime/FINAL-SECURITY-ARCHITECTURE.md)。

## CI/CD

GitHub Actions 自动运行 Lint + Test (coverage≥80%) + Security Gate。详见 [CI 配置](./.github/workflows/xruntime-ci.yml)。

## 贡献

```bash
pre-commit install
```

## 许可证

Apache License 2.0
