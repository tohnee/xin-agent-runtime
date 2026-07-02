# XRuntime P0 模块文档

> 版本: v0.1.0
> 阶段: P0 地基
> 日期: 2026-06-23

## 概述

P0 阶段完成了 XRuntime 运行时的地基骨架，包括配置体系、FastAPI 应用工厂、
统一请求模型、协议适配器抽象基座、网关路由框架和端到端冒烟测试。

所有模块均采用 TDD（测试先行）开发，42 项测试全部通过。

## 模块清单

### 1. `_config.py` — 配置体系

**文件**: `src/xruntime/_config.py`

**职责**: 定义 XRuntime 全量配置 schema 并提供 YAML + 环境变量加载。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `XRuntimeConfig` | 顶层配置，聚合所有子配置 |
| `ServerConfig` | HTTP 服务配置 (host/port/auth) |
| `StorageConfig` | 存储后端配置 (Redis/PostgreSQL + 多租户前缀) |
| `MessageBusConfig` | 消息总线配置 |
| `TenantConfig` | 租户定义 (id/name/credentials) |
| `AgentBlueprintConfig` | Agent 蓝图 (name/system_prompt/tools) |
| `McpServerConfig` | MCP 服务声明 (stdio/http) |
| `SkillConfig` | Skill 目录声明 |
| `PermissionConfig` | 权限配置 (mode/rules) |
| `PluginConfig` | 插件声明 |
| `ObservabilityConfig` | 可观测性配置 (OTel/audit) |

**函数**:

| 函数 | 说明 |
|---|---|
| `load_config(config_path=None)` | 从 YAML 加载配置 + 环境变量覆盖 |

**环境变量约定**: `XRUNTIME_<SECTION>_<FIELD>`，如
`XRUNTIME_SERVER_PORT=7777` 覆盖 `server.port`。

**测试**: 13 项 (test_config.py)

---

### 2. `_gateway/_app.py` — FastAPI 应用工厂

**文件**: `src/xruntime/_gateway/_app.py`

**职责**: 创建配置好的 XRuntime FastAPI 应用，包含健康检查、协议路由。

**核心函数**:

| 函数 | 说明 |
|---|---|
| `create_xruntime_app(config, config_path, adapter_registry, runtime_mode)` | 应用工厂 |

**端点**:

| 路径 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 存活探针 |
| `/ready` | GET | 就绪探针 |
| `/v1/messages` | POST | Anthropic Messages API |
| `/v1/claude-code/query` | POST | Claude Code SDK HTTP |
| `/v1/opencode` | POST | OpenCode SDK |

**运行模式**:
- `production`: 对接 AS ChatService (P1 实现)
- `mock`: 内存 mock 事件流 (测试/开发用)

**测试**: 7 项 (test_app.py) + 4 项 (test_gateway_smoke.py)

---

### 3. `_gateway/_request.py` — 统一请求模型

**文件**: `src/xruntime/_gateway/_request.py`

**职责**: 三协议入站请求归一化为 `XRuntimeRequest`。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `ProtocolType` | 协议枚举: ANTHROPIC / CLAUDE_CODE / OPENCODE |
| `ToolExecutionMode` | 工具执行模式: SERVER / EXTERNAL |
| `XRuntimeRequest` | 统一请求 (protocol/prompt/session/user/tenant/tools/permission/metadata) |

**测试**: 9 项 (test_request_adapter.py 前半)

---

### 4. `_gateway/_adapter.py` — 协议适配器抽象

**文件**: `src/xruntime/_gateway/_adapter.py`

**职责**: 定义协议适配器 ABC 和注册表。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `ProtocolAdapter` | ABC: `parse_request` + `serialize_event_stream` + `protocol_type` |
| `AdapterRegistry` | 按协议类型注册/查询适配器 |

**测试**: 9 项 (test_request_adapter.py 后半)

---

### 5. 网关路由框架

**职责**: 根据 URL 路径选择协议适配器，驱动运行时，返回流式响应。

**路由 → 协议映射**:

| 路径 | 协议 |
|---|---|
| `/v1/messages` | ANTHROPIC |
| `/v1/claude-code/query` | CLAUDE_CODE |
| `/v1/opencode` | OPENCODE |

**流程**: 请求 → 适配器 `parse_request` → 运行时事件流 → 适配器 `serialize_event_stream` → NDJSON SSE

**冒烟测试**: DummyAdapter 通过 mock 运行时完成请求 → 事件流 → 响应全链路。

---

## 测试汇总

| 测试文件 | 测试数 | 覆盖模块 |
|---|---|---|
| `test_config.py` | 13 | `_config.py` |
| `test_app.py` | 7 | `_gateway/_app.py` |
| `test_request_adapter.py` | 18 | `_gateway/_request.py` + `_adapter.py` |
| `test_gateway_smoke.py` | 4 | 网关路由端到端 |
| **合计** | **42** | **全部通过** |

## 运行测试

```bash
cd agentscope
python3 -m pytest tests/xruntime/ -v -p no:cacheprovider
```

## 文件结构

```
src/xruntime/
├── __init__.py            # 包入口: XRuntimeConfig, load_config, __version__
├── _version.py            # 版本号
├── _config.py             # 配置 schema + YAML/env 加载
├── py.typed               # PEP 561 类型标记
├── _gateway/
│   ├── __init__.py
│   ├── _app.py            # create_xruntime_app
│   ├── _request.py        # XRuntimeRequest + ProtocolType
│   └── _adapter.py        # ProtocolAdapter ABC + AdapterRegistry
├── _runtime/
│   ├── __init__.py
│   └── _middleware/
│       └── __init__.py
├── _infra/
│   └── __init__.py
src/xruntime_sdk/
├── __init__.py
└── py.typed
tests/xruntime/
├── test_config.py
├── test_app.py
├── test_request_adapter.py
└── test_gateway_smoke.py
```
