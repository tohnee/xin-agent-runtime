# Xin Agent Runtime 使用 SOP 手册

> 面向小白的完全操作手册 · 架构介绍 · 模块详情 · 安装部署 · 自定义开发教程
>
> 版本：v1.0 · 适用于代码主分支（main）

---

## 目录

- [第一部分：架构介绍](#第一部分架构介绍)
  - [1.1 项目定位](#11-项目定位)
  - [1.2 三层九模块总览](#12-三层九模块总览)
  - [1.3 与 AgentScope 的关系](#13-与-agentscope-的关系)
  - [1.4 请求生命周期](#14-请求生命周期)
- [第二部分：模块详情](#第二部分模块详情)
  - [2.1 网关层 Gateway](#21-网关层-gateway)
  - [2.2 运行时层 Runtime](#22-运行时层-runtime)
  - [2.3 基础设施层 Infra](#23-基础设施层-infra)
  - [2.4 租户与权限模块](#24-租户与权限模块)
  - [2.5 知识库模块](#25-知识库模块)
  - [2.6 记忆模块](#26-记忆模块)
  - [2.7 模型路由与治理](#27-模型路由与治理)
  - [2.8 Admin API](#28-admin-api)
  - [2.9 前端 Web UI](#29-前端-web-ui)
- [第三部分：安装部署流程](#第三部分安装部署流程)
  - [3.1 环境准备](#31-环境准备)
  - [3.2 Docker 一键部署（推荐生产）](#32-docker-一键部署推荐生产)
  - [3.3 本地开发部署](#33-本地开发部署)
  - [3.4 前端 Web UI 部署](#34-前端-web-ui-部署)
  - [3.5 配置文件详解](#35-配置文件详解)
  - [3.6 环境变量速查表](#36-环境变量速查表)
  - [3.7 健康检查与故障排查](#37-健康检查与故障排查)
- [第四部分：自定义开发教程](#第四部分自定义开发教程)
  - [4.1 新增中间件](#41-新增中间件)
  - [4.2 新增协议适配器](#42-新增协议适配器)
  - [4.3 新增模型 Provider](#43-新增模型-provider)
  - [4.4 新增知识库后端](#44-新增知识库后端)
  - [4.5 新增记忆存储后端](#45-新增记忆存储后端)
  - [4.6 新增 Skill 技能](#46-新增-skill-技能)
- [第五部分：面向小白的完全 SOP 操作手册](#第五部分面向小白的完全-sop-操作手册)
  - [5.1 SOP-1：从零搭建并启动服务](#51-sop-1从零搭建并启动服务)
  - [5.2 SOP-2：创建租户与 API Key](#52-sop-2创建租户与-api-key)
  - [5.3 SOP-3：使用 SDK 发起一次对话](#53-sop-3使用-sdk-发起一次对话)
  - [5.4 SOP-4：接入 Anthropic 协议客户端](#54-sop-4接入-anthropic-协议客户端)
  - [5.5 SOP-5：接入 Claude Code SDK](#55-sop-5接入-claude-code-sdk)
  - [5.6 SOP-6：管理知识库与上传文档](#56-sop-6管理知识库与上传文档)
  - [5.7 SOP-7：通过 Admin API 查看系统状态](#57-sop-7通过-admin-api-查看系统状态)
  - [5.8 SOP-8：开启 Langfuse 可观测性](#58-sop-8开启-langfuse-可观测性)
  - [5.9 SOP-9：配置 Human-in-the-Loop 审批](#59-sop-9配置-human-in-the-loop-审批)
  - [5.10 SOP-10：生产环境上线 Checklist](#510-sop-10生产环境上线-checklist)
- [附录](#附录)

---

# 第一部分：架构介绍

## 1.1 项目定位

Xin Agent Runtime 是一个**企业级 Agent 开发运行时底座**，由 AgentScope 执行内核与 XRuntime 企业扩展层联合组成。它把"协议接入、多租户隔离、权限管控、知识库、记忆系统、模型治理、可观测性、安全审计"等企业级能力打包成一套开箱即用的运行时，让你只需关心 Agent 本身的业务逻辑。

**典型使用场景：**

- 企业内部把多个 LLM Agent 能力对外开放，需要租户隔离、配额、审计。
- 把 Anthropic Messages API / Claude Code SDK / OpenCode 三种协议统一接入同一个后端。
- 需要为 Agent 提供知识库 RAG、长期记忆、技能注入等企业能力。
- 需要对接 Langfuse / OpenTelemetry / Prometheus 实现完整可观测性。

## 1.2 三层九模块总览

整体架构按"自上而下"分为三层，每层又包含若干子模块：

```
┌─────────────────────────────────────────────────────────────┐
│              客户端 / SDK / 协议入口                          │
│   (Anthropic / Claude Code / OpenCode / xruntime_sdk)        │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼─────────────────────────────────┐
│  第一层 · 网关层 Gateway  (src/xruntime/_gateway/)            │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐   │
│  │  Auth    │ RateLimit│ Anthropic│ClaudeCode│ OpenCode │   │
│  │ Middleware│Middleware│ Adapter  │ Adapter  │ Adapter  │   │
│  └──────────┴──────────┴──────────┴──────────┴──────────┘   │
└───────────────────────────┬─────────────────────────────────┘
                            │ RuntimeExecutionPlan (统一执行计划)
┌───────────────────────────▼─────────────────────────────────┐
│  第二层 · 运行时层 Runtime  (src/xruntime/_runtime/)          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  中间件链 (10 个，按固定顺序)                          │   │
│  │  LangfuseTracer → LoopDetection → LLMErrorHandling   │   │
│  │  → Audit → Quota → Rbac → SecretRedaction            │   │
│  │  → Knowledge → SkillInjection → Memory              │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌────────┬────────┬────────┬────────┬────────┬─────────┐  │
│  │Knowledge│Memory │ Skills │Subagents│Workflow│Approval │  │
│  │ Module  │Module │ Module │ Module │ Module │ Module  │  │
│  └────────┴────────┴────────┴────────┴────────┴─────────┘  │
│  ┌─────────────────┬─────────────────┬──────────────────┐  │
│  │ ModelResolver   │ ModelRouter     │ ModelGovernance  │  │
│  └─────────────────┴─────────────────┴──────────────────┘  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  第三层 · 基础设施层 Infra  (src/xruntime/_infra/)           │
│  ┌──────────────┬──────────────┬────────────────────────┐  │
│  │ TenantCtx    │ Metrics      │ TenantStorage /         │  │
│  │ (contextvars)│ (Prometheus) │ TenantMessageBus       │  │
│  └──────────────┴──────────────┴────────────────────────┘  │
│  ┌────────────────────────────────────────────────────┐    │
│  │  AgentScope 内核  (src/agentscope/)                 │    │
│  │  Agent · Model · Tool · Formatter · Storage · Bus   │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**九个核心模块速览：**

| 模块 | 路径 | 职责 |
|------|------|------|
| Gateway | `src/xruntime/_gateway/` | 协议适配 + 认证 + 限流 |
| Runtime Middleware | `src/xruntime/_runtime/_middleware/` | 10 个企业中间件链 |
| Knowledge | `src/xruntime/_runtime/_knowledge/` | 知识库 RAG + BM25 + ACL |
| Memory | `src/xruntime/_runtime/_memory/` | 长期记忆存储与检索 |
| Skills | `src/xruntime/_runtime/_skills/` | 技能注入与加载 |
| Subagents | `src/xruntime/_runtime/_subagents/` | 子 Agent 任务委派 |
| Tenant | `src/xruntime/_runtime/_tenant/` + `_infra/_tenant.py` | 多租户隔离 + RBAC |
| ModelRouter | `src/xruntime/_runtime/_model_router.py` | 三级模型解析 + 8 个 provider |
| Workflow | `src/xruntime/_runtime/_workflow/` | 工作流编排（canary / checkpoint / 分布式锁）|

## 1.3 与 AgentScope 的关系

XRuntime **不是** AgentScope 的替代品，而是它的企业级扩展层：

- **AgentScope 内核** (`src/agentscope/`) 提供 Agent 执行能力：`Agent` 类、Model Provider（OpenAI/Anthropic/Gemini/Ollama 等）、Toolkit、Formatter、Storage、MessageBus、FastAPI 服务层（`agentscope.app`）。
- **XRuntime 扩展** (`src/xruntime/`) 在 AgentScope 之上加了网关、中间件链、租户隔离、知识库、记忆、模型治理等企业能力，通过 `create_xruntime_extension()` 装配到 AS 的 FastAPI app 上。

```
+---------------------------------------------------+
|              FastAPI App (uvicorn)                |
|  ┌─────────────────────────────────────────────┐  |
|  │  AgentScope create_app()                    │  |
|  │  ┌───────────────────────────────────────┐  │  |
|  │  │  XRuntime Extension                   │  │  |
|  │  │  (middleware chain + admin router)    │  │  |
|  │  └───────────────────────────────────────┘  │  |
|  │  + Protocol Adapters (mounted routes)       │  |
|  └─────────────────────────────────────────────┘  |
+---------------------------------------------------+
```

**关键装配点：** `src/xruntime/_server.py::build_xruntime_app()` 负责把 AS app、XRuntime extension、协议适配器三者按顺序装配起来。

## 1.4 请求生命周期

一次完整的客户端请求（以 Anthropic Messages API 为例）会经过以下阶段：

```
1. HTTP POST /v1/messages
        │
2. AuthMiddleware         ← 验证 API Key / JWT，解析出 AuthPrincipal
        │                    （request.state.principal 优先于 header）
3. RateLimitMiddleware    ← 令牌桶限流，超限返回 429
        │
4. AnthropicMessagesAdapter.parse_request()
        │                 ← 把 Anthropic 请求体转换为 XRuntimeRequest
        │
5. RuntimeExecutionPlan   ← 统一执行计划，三协议汇合点
        │
6. 中间件链 (10 个，按顺序)
   ├─ LangfuseTracer       ← 开始 trace span
   ├─ LoopDetection        ← 检测死循环（同工具连续调用阈值）
   ├─ LLMErrorHandling     ← LLM 调用失败兜底
   ├─ Audit                ← 记录 audit log（who/what/when/decision）
   ├─ Quota                ← 检查 token / cost 配额
   ├─ Rbac                 ← 校验 tool / kb / action 权限
   ├─ SecretRedaction      ← 脱敏工具入参 / LLM prompt
   ├─ Knowledge            ← 注入知识库 RAG 上下文
   ├─ SkillInjection       ← 加载 SKILL.yaml 注入 system prompt
   └─ Memory               ← 检索长期记忆注入上下文
        │
7. AgentScope Agent.reply_stream()
        │                 ← 真正执行 ReAct 循环
        │
8. AgentEvent Stream      ← REPLY_START / TEXT_BLOCK_* / TOOL_CALL_* / REPLY_END
        │
9. AnthropicMessagesAdapter.serialize_event_stream()
        │                 ← 把事件流序列化为 Anthropic SSE 字节流
        │
10. HTTP Response (SSE chunked)
```

**关键设计：**

- **协议适配器只做格式转换**，不参与业务逻辑。三协议在执行计划层汇合，复用同一套中间件链。
- **中间件链顺序固定**，依赖关系（如 SecretRedaction 必须在 Audit 之前脱敏，Audit 必须在 Quota 之前记录）由代码硬编码保证。
- **租户隔离贯穿全链路**：从 AuthMiddleware 解析出 tenant_id 后，通过 `contextvars.ContextVar` 向下传递，Redis key 自动加 `tenant:{tid}:` 前缀。

---

# 第二部分：模块详情

## 2.1 网关层 Gateway

**路径：** `src/xruntime/_gateway/`

网关层是所有外部请求的入口，负责"认证、限流、协议适配"三件事。

### 文件结构

```
_gateway/
├── _adapter.py              # ProtocolAdapter 抽象基类
├── _anthropic_adapter.py    # Anthropic Messages API 适配器
├── _claude_code_adapter.py  # Claude Code SDK 适配器
├── _opencode_adapter.py     # OpenCode 适配器
├── _openai_adapter.py        # OpenAI 兼容适配器
├── _auth.py                  # AuthMiddleware (API Key + JWT)
├── _ratelimit.py             # RateLimitMiddleware (令牌桶)
├── _extension.py             # create_xruntime_extension + mount_protocol_adapters
├── _plan.py                  # RuntimeExecutionPlan
├── _request.py               # XRuntimeRequest 统一请求模型
└── _mw_state.py              # 中间件状态管理
```

### 三个协议适配器对比

| 适配器 | 路由 | 协议类型 | 状态管理 |
|--------|------|----------|----------|
| `AnthropicMessagesAdapter` | `POST /v1/messages` | `ProtocolType.ANTHROPIC` | 通过 `x-session-id` header 可选有状态 |
| `ClaudeCodeAdapter` | `POST /v1/claude-code/query` | `ProtocolType.CLAUDE_CODE` | 强制 session 模式 |
| `OpenCodeAdapter` | `POST /v1/opencode` | `ProtocolType.OPENCODE` | session 模式 |

**核心抽象：** `ProtocolAdapter`（在 `_adapter.py`）定义两个必须实现的方法：

- `parse_request(raw, headers)` → 把原始请求转成统一的 `XRuntimeRequest`
- `serialize_event_stream(events)` → 把 `AgentEvent` 流转回该协议的字节流

### 认证中间件 AuthMiddleware

`_auth.py` 实现双因子认证：

1. **API Key 模式**：从 `Authorization: Bearer sk-xxx` 解析 key，在 `ApiKeyStore` 中查到对应的 `tenant_id` / `user_id` / `role` / `kb_ids`，构造 `AuthPrincipal` 放入 `request.state.principal`。
2. **JWT 模式**：解析 JWT，校验 `nbf / iat / aud / iss` claim（带 clock skew leeway），同样构造 `AuthPrincipal`。

**反身份欺骗：** `request.state.principal` 一旦被 AuthMiddleware 设置，下游所有模块必须以它为准，**忽略**客户端发送的 `x-user-id` / `x-tenant-id` header。这是防止用户伪造他人身份的核心防线。

### 限流中间件 RateLimitMiddleware

`_ratelimit.py` 实现基于令牌桶的限流，配置格式 `"100/60"` 表示"每 60 秒最多 100 次请求"。超限返回 HTTP 429。

## 2.2 运行时层 Runtime

**路径：** `src/xruntime/_runtime/`

运行时层是 XRuntime 的核心，包含 10 个中间件、知识库、记忆、技能、子 Agent、工作流等子系统。

### 中间件链（固定顺序，不可调换）

| 序号 | 中间件 | 文件 | 职责 |
|------|--------|------|------|
| 1 | LangfuseTracer | `_middleware/_langfuse_tracer.py` | 启动 Langfuse trace span |
| 2 | LoopDetection | `_middleware/_loop_detection.py` | 检测同工具连续调用死循环 |
| 3 | LLMErrorHandling | `_middleware/_llm_error_handling.py` | LLM 调用失败兜底 + 重试 |
| 4 | Audit | `_middleware/_audit.py` | 记录 audit log |
| 5 | Quota | `_middleware/_quota.py` | token / cost 配额检查 |
| 6 | Rbac | `_middleware/_rbac.py` | 工具 / KB / action 权限校验 |
| 7 | SecretRedaction | `_middleware/_redaction.py` | 脱敏工具入参 + LLM prompt |
| 8 | Knowledge | `_knowledge/_middleware.py` | 注入知识库 RAG 上下文 |
| 9 | SkillInjection | `_middleware/_skill_injection.py` | 加载 SKILL.yaml 注入 |
| 10 | Memory | `_memory/_middleware.py` | 检索长期记忆注入 |

**额外中间件（可选）：**

- `ApprovalMiddleware` (`_middleware/_approval.py`)：Human-in-the-Loop 工具调用审批，支持 `always / once / never / predicate` 四种策略。

**中间件链构建位置：** `src/xruntime/_gateway/_extension.py::create_xruntime_extension()` 按上述顺序硬编码组装。

### 知识库子系统

见 [2.5 知识库模块](#25-知识库模块)。

### 记忆子系统

见 [2.6 记忆模块](#26-记忆模块)。

### 模型路由与治理

见 [2.7 模型路由与治理](#27-模型路由与治理)。

## 2.3 基础设施层 Infra

**路径：** `src/xruntime/_infra/`

基础设施层提供跨模块共享的底层能力。

### 文件结构

```
_infra/
├── _tenant.py               # contextvars 租户上下文
├── _tenant_storage.py        # TenantKeyPrefixer (Redis key 自动加前缀)
├── _tenant_message_bus.py   # 租户隔离的消息总线
└── _metrics.py              # Prometheus 指标收集
```

### 多租户隔离三层机制

| 层 | 实现 | 作用 |
|----|------|------|
| 1. 上下文传递 | `_tenant.py` 用 `contextvars.ContextVar` 保存当前请求的 `tenant_id` | 全链路自动传递，无需手动透传 |
| 2. Redis key 前缀 | `TenantKeyPrefixer(prefix="tenant:{tid}:")` | 所有 Redis key 自动加租户前缀，物理隔离 |
| 3. RBAC 矩阵 | `_tenant/_policy.py::TenantPolicy` | Owner/Admin/Contributor/Viewer × 15 个 Action 的权限矩阵 |

**关键设计：** `TenantKeyPrefixer` 用 `str.removeprefix()` 而非 `str.replace()` 剥离前缀，避免 key 中间出现前缀子串时被错误替换。

## 2.4 租户与权限模块

**路径：** `src/xruntime/_runtime/_tenant/`

### 文件结构

```
_tenant/
├── _store.py                # ApiKeyStore + JwtClaimsParser + TenantMembershipStore + AuthPrincipal
├── _policy.py               # TenantPolicy RBAC 矩阵 + TenantRole 枚举
└── __init__.py              # 导出 TenantRole
```

### 角色与权限

四级角色，权限逐级递减：

| 角色 | 权限范围 |
|------|----------|
| `Owner` | 全部权限，包括删除租户、转让所有权 |
| `Admin` | 管理用户、KB、凭证；不能删除租户 |
| `Contributor` | 创建/修改自己的 KB、文档；不能管理用户 |
| `Viewer` | 只读访问 |

**15 个细粒度 Action：** `kb:create` / `kb:query` / `kb:delete` / `doc:ingest` / `doc:read` / `doc:delete` / `agent:invoke` / `tool:call` / `memory:write` / `memory:read` / `memory:delete` / `cred:manage` / `tenant:user.manage` / `tenant:config` / `tenant:delete`。

### AuthPrincipal 数据结构

```python
@dataclass
class AuthPrincipal:
    user_id: str
    tenant_id: str
    role: TenantRole
    kb_ids: list[str]           # 该 principal 可访问的 KB 列表
    auth_method: str            # "api_key" | "jwt"
    raw_token: str              # 原始 token（仅用于审计日志）
```

## 2.5 知识库模块

**路径：** `src/xruntime/_runtime/_knowledge/`

### 文件结构

```
_knowledge/
├── _base.py                  # KnowledgeBase 抽象基类 + KnowledgeBaseConfig
├── _adapter.py              # KnowledgeBackendFactory + backend 注册机制
├── _llm_wiki_adapter.py     # 默认实现：LLM-Wiki AOT 编译后端
├── _registry.py             # KnowledgeRegistry (多 KB 管理)
├── _middleware.py           # KnowledgeMiddleware (RAG 注入)
├── _tools.py                # SearchKnowledgeTool (供 Agent 调用)
└── _acl.py                  # KnowledgeAclEntry + per-KB ACL
```

### 工作流程

1. **文档摄入**：通过 `KnowledgeBase.ingest(source_id, content)` 把文档喂给 KB。`LlmWikiAdapter` 会调用 LLM 做 AOT 编译，生成可检索的 wiki 结构。
2. **检索**：`KnowledgeMiddleware` 在每轮对话前，用当前 prompt 去 KB 做 BM25 检索，把命中的片段注入到 system prompt。
3. **ACL**：每个 KB 有 owner 和 granted_users，`KnowledgeAclEntry` 控制谁能 query / ingest / delete。

**路径穿越防护：** `LlmWikiAdapter.delete_source(source_id)` 会拒绝 `../` / `/` / 绝对路径，防止目录逃逸。

### 默认后端注册

模块底部调用 `_register_default_adapter()`，使 `llm_wiki` 后端在 import 时自动注册到 `KnowledgeBackendFactory`，无需手动调用。

## 2.6 记忆模块

**路径：** `src/xruntime/_runtime/_memory/`

### 文件结构

```
_memory/
├── _models.py               # MemoryItem 数据模型
├── _store.py                # MemoryStore 抽象基类 + InMemoryStore
├── _redis_store.py          # RedisMemoryStore (生产推荐)
├── _extractor.py            # MemoryExtractor (从对话中抽取记忆)
├── _embedding_providers.py  # 嵌入 provider (OpenAI / DashScope / Ollama)
├── _hybrid_retriever.py     # 混合检索 (向量 + BM25)
└── _middleware.py           # MemoryMiddleware (自动注入记忆)
```

### 记忆生命周期

```
对话发生 → MemoryExtractor 抽取关键信息 → MemoryStore.save()
                                                    │
对话检索 ← MemoryMiddleware 注入 ← MemoryStore.search() ←
```

**存储后端：**

- `InMemoryStore`：进程内存，适合开发测试。
- `RedisMemoryStore`：生产推荐，key 自动加 `tenant:{tid}:mem:` 前缀。

## 2.7 模型路由与治理

**路径：** `src/xruntime/_runtime/_model_router.py` + `_model_resolver.py` + `_model_governance.py`

### 三级模型解析

模型解析按以下优先级查找，命中即返回：

```
1. Runtime registry   (代码注册的 ModelConfig)
2. 环境变量           (XRUNTIME_MODEL_<NAME>_<FIELD>)
3. 配置文件           (xruntime.yaml 里的 model_providers)
```

### 支持的 Provider

| Provider | 环境变量 | 说明 |
|----------|----------|------|
| OpenAI | `OPENAI_API_KEY` | GPT-4o / GPT-4-Turbo |
| Anthropic | `ANTHROPIC_API_KEY` | Claude Sonnet / Opus |
| DashScope | `DASHSCOPE_API_KEY` | 通义千问 |
| DeepSeek | `DEEPSEEK_API_KEY` | DeepSeek-Chat / Coder |
| Gemini | `GEMINI_API_KEY` | Gemini Pro / Flash |
| Ollama | `OLLAMA_BASE_URL` | 本地模型 |
| Moonshot | `MOONSHOT_API_KEY` | Kimi |
| xAI | `XAI_API_KEY` | Grok |

### 模型治理

`_model_governance.py` 提供：

- **Tenant allowlist**：每个租户可配置允许使用的模型列表。
- **Capability registry**：模型能力声明（支持工具调用 / 视觉 / 流式）。
- **Fallback 策略**：主模型不可用时自动降级到备用模型。

## 2.8 Admin API

**路径：** `src/xruntime/_admin_api.py`

挂载在 `/admin/*`，提供只读管理端点。所有端点都需要 `admin` 或 `owner` 角色，且只能查询自己租户的数据（跨租户查询返回 403）。

### 可用端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/status` | 系统状态（skills / memories / sessions 数量）|
| GET | `/admin/skills` | 列出所有技能 |
| GET | `/admin/skills/{name}` | 查看技能详情 |
| POST | `/admin/memories/search` | 搜索记忆 |
| GET | `/admin/memories` | 列出记忆 |
| GET | `/admin/models` | 列出可用模型 |
| GET | `/admin/metrics/summary` | 性能指标摘要 |

## 2.9 前端 Web UI

**路径：** `examples/web_ui/`

前端是 pnpm monorepo，包含 `backend/`（Node 代理）和 `frontend/`（React + Vite + TDesign）。

### 关键页面

| 页面 | 路径 | 功能 |
|------|------|------|
| Setup | `pages/setup/` | 配置后端地址 + 用户名 + JWT Token |
| Chat | `pages/chat/` | 与 Agent 对话 |
| Credential | `pages/credential/` | 管理 LLM 凭证 |
| Schedule | `pages/schedule/` | 管理定时任务 |

### 前后端通信

前端通过 `api/client.ts` 封装 HTTP 客户端，所有请求带 `Authorization: Bearer <token>` header。Setup 页面允许用户填入 JWT Token（密码框，避免明文显示）。

---

# 第三部分：安装部署流程

## 3.1 环境准备

### 必备依赖

| 软件 | 版本要求 | 用途 |
|------|----------|------|
| Python | >= 3.11 | 运行时 |
| Redis | >= 7.0 | 存储 + 消息总线 |
| Docker | >= 24.0 | 容器化部署 + 沙箱 |
| docker-compose | v2 | 多容器编排 |
| Node.js | >= 18 | 前端构建（可选）|
| pnpm | >= 8 | 前端包管理（可选）|
| uv | 最新 | Python 包管理（推荐）|

### 系统资源建议

| 部署规模 | CPU | 内存 | 磁盘 |
|----------|-----|------|------|
| 开发测试 | 2 核 | 4 GB | 20 GB |
| 小规模生产（< 50 并发）| 4 核 | 8 GB | 50 GB |
| 中规模生产（< 500 并发）| 8 核 | 16 GB | 200 GB SSD |

## 3.2 Docker 一键部署（推荐生产）

### 步骤 1：克隆仓库

```bash
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime
```

### 步骤 2：准备配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，修改以下关键字段：
#   REDIS_PASSWORD      Redis 密码（生产务必修改）
#   JWT_SECRET          JWT 签名密钥（生产务必用强随机值，>= 32 字节）
#   API_KEY_RECORDS     API Key 列表（JSON 数组，见下方说明）
#   XRUNTIME_PORT       服务端口（默认 8900）
```

**`API_KEY_RECORDS` 格式说明：**

```json
[
  {
    "key": "sk-admin-your-strong-key",
    "tenant_id": "acme",
    "user_id": "alice",
    "role": "admin",
    "kb_ids": ["kb1", "kb2"],
    "key_id": "optional-key-id",
    "active": true
  },
  {
    "key": "sk-user-another-key",
    "tenant_id": "acme",
    "user_id": "bob",
    "role": "contributor",
    "kb_ids": ["kb1"]
  }
]
```

**字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | str | 是 | API Key 字符串，建议 `sk-` 前缀 + 32 位随机字符 |
| `tenant_id` | str | 是 | 该 key 归属的租户 ID |
| `user_id` | str | 是 | 该 key 归属的用户 ID |
| `role` | str | 是 | `owner` / `admin` / `contributor` / `viewer` |
| `kb_ids` | list[str] | 否 | 该 key 可访问的知识库 ID 列表 |
| `key_id` | str | 否 | 可读的 key 标识（用于审计日志）|
| `active` | bool | 否 | 是否启用，默认 `true` |

### 步骤 3：准备 xruntime.yaml 配置文件

在仓库根目录创建 `xruntime.yaml`（Docker 会挂载到容器内 `/app/xruntime.yaml`）：

```yaml
# xruntime.yaml — XRuntime 主配置文件

server:
  host: "0.0.0.0"
  port: 8900
  auth_enabled: true

storage:
  backend: "redis"
  redis_host: "redis"        # Docker 内部服务名
  redis_port: 6379
  redis_password: "${REDIS_PASSWORD}"  # 从环境变量读取
  tenant_prefix: "tenant:{tid}:"

message_bus:
  backend: "redis"
  redis_host: "redis"
  redis_port: 6379
  redis_password: "${REDIS_PASSWORD}"
  tenant_prefix: "tenant:{tid}:"

tenants:
  - id: "acme"
    name: "ACME Corp"
    credentials: []
    tool_allowlist: null
    model_allowlist: null

agents:
  - name: "default-assistant"
    system_prompt: "You are a helpful assistant."
    model_config_name: "gpt-4o"
    allowed_tools: []

model_providers:
  - name: "gpt-4o"
    provider: "openai"
    model: "gpt-4o"
    credential_env: "OPENAI_API_KEY"

approval:
  enabled: false

observability:
  otel_enabled: false
  langfuse_enabled: false
```

### 步骤 4：启动服务

```bash
docker compose up -d
```

这会启动两个容器：

- `xin-redis`：Redis 7 Alpine，持久化 + 限流 + 健康检查
- `xin-runtime`：XRuntime 服务，监听 `8900`

### 步骤 5：验证启动

```bash
# 健康检查
curl http://localhost:8900/health
# 预期返回: {"status":"ok"}

# 查看日志
docker compose logs -f runtime

# 查看容器状态
docker compose ps
```

### 步骤 6（可选）：开启可观测性栈

```bash
# 启动 Prometheus + Grafana + Alertmanager + OTel Collector
docker compose -f deploy/docker-compose.observability.yml up -d
```

## 3.3 本地开发部署

适合二次开发或调试。

### 步骤 1：克隆 + 安装依赖

```bash
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime

# 推荐用 uv（更快）
uv pip install -e ".[dev]"

# 或用 pip
pip install -e ".[dev]"
```

### 步骤 2：启动 Redis

```bash
# 方式 A：用 Docker 单独启动 Redis
docker run -d --name xin-redis \
  -p 6379:6379 \
  redis:7-alpine \
  redis-server --requirepass xruntime-redis-dev

# 方式 B：只启动 docker-compose 里的 Redis
docker compose up -d redis
```

### 步骤 3：设置环境变量

```bash
export XRUNTIME_API_KEYS="sk-dev-key-1"
export XRUNTIME_API_KEY_RECORDS='[{"key":"sk-admin-dev","tenant_id":"acme","user_id":"alice","role":"admin","kb_ids":["kb1"]}]'
export XRUNTIME_WORKSPACE_BACKEND=local          # 开发用 local，生产必须 docker
export XRUNTIME_STORAGE_REDIS_HOST=localhost
export XRUNTIME_STORAGE_REDIS_PORT=6379
export XRUNTIME_STORAGE_REDIS_PASSWORD=xruntime-redis-dev
export XRUNTIME_MESSAGE_BUS_REDIS_HOST=localhost
export XRUNTIME_MESSAGE_BUS_REDIS_PORT=6379
export XRUNTIME_MESSAGE_BUS_REDIS_PASSWORD=xruntime-redis-dev
export OPENAI_API_KEY="sk-your-openai-key"        # 至少配一个 LLM key
```

### 步骤 4：启动服务

```bash
# 方式 A：直接运行
python -m xruntime._server

# 方式 B：用 uvicorn 热重载（开发推荐）
uvicorn xruntime._server:app --host 0.0.0.0 --port 8900 --reload
```

### 步骤 5：验证

```bash
curl http://localhost:8900/health
```

## 3.4 前端 Web UI 部署

### 步骤 1：安装依赖

```bash
cd examples/web_ui
pnpm install
```

### 步骤 2：启动前端开发服务器

```bash
pnpm --filter frontend dev
# 默认在 http://localhost:5173
```

### 步骤 3：启动后端代理（可选）

前端默认直连 XRuntime 后端。如需 Node 代理：

```bash
pnpm --filter backend dev
```

### 步骤 4：访问

打开浏览器访问 `http://localhost:5173`，在 Setup 页面填入：

- **Server URL**: `http://localhost:8900`
- **Username**: 你的用户名
- **Auth Token**（可选）: 你的 JWT 或 API Key

## 3.5 配置文件详解

`XRuntimeConfig` 是所有配置的根模型（在 `src/xruntime/_config.py`）。它支持 **YAML 文件 + 环境变量覆盖**双通道配置。

### 顶层配置节

| 节 | 类型 | 说明 |
|----|------|------|
| `server` | `ServerConfig` | HTTP 服务器配置 |
| `storage` | `StorageConfig` | 存储后端（Redis/Postgres）|
| `message_bus` | `MessageBusConfig` | 消息总线 |
| `tenants` | `list[TenantConfig]` | 租户定义 |
| `agents` | `list[AgentBlueprintConfig]` | Agent 蓝图 |
| `mcps` | `list` | MCP 服务器配置 |
| `skills` | `SkillsConfig` | 技能目录配置 |
| `permission` | `PermissionConfig` | 权限策略 |
| `plugins` | `PluginsConfig` | 插件配置 |
| `observability` | `ObservabilityConfig` | 可观测性配置 |
| `knowledge` | `KnowledgeConfig` | 知识库配置 |
| `approval` | `ApprovalConfig` | HITL 审批配置 |
| `credential_broker` | `CredentialBrokerConfig` | 凭证代理 |
| `workflow` | `WorkflowConfig` | 工作流配置 |
| `model_providers` | `list` | 模型 Provider 列表 |

### 配置加载优先级

```
1. 代码内默认值                      (最低)
2. YAML 配置文件 (xruntime.yaml)
3. 环境变量 (XRUNTIME_<SECTION>_<FIELD>)  (最高)
```

**环境变量命名规则：** `XRUNTIME_` 前缀 + 大写的 `节名_字段名`，下划线分隔。例如：

- `XRUNTIME_SERVER_PORT=7777` → `server.port = 7777`
- `XRUNTIME_STORAGE_REDIS_HOST=redis` → `storage.redis_host = "redis"`
- `XRUNTIME_APPROVAL_ENABLED=true` → `approval.enabled = True`

## 3.6 环境变量速查表

### 必须配置（生产）

| 变量 | 说明 | 示例 |
|------|------|------|
| `XRUNTIME_JWT_SECRET` | JWT 签名密钥 | `openssl rand -hex 32` |
| `XRUNTIME_API_KEY_RECORDS` | API Key JSON 数组 | 见 [3.2 步骤 2](#步骤-2准备配置) |
| `OPENAI_API_KEY` | 至少一个 LLM provider key | `sk-...` |

### 服务相关

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `XRUNTIME_PORT` | `8900` | 服务端口 |
| `XRUNTIME_CONFIG` | - | YAML 配置文件路径 |
| `XRUNTIME_CONFIG_PATH` | - | 配置文件路径（备选）|
| `XRUNTIME_PRODUCTION` | `0` | 生产模式标志（启用严格校验）|
| `XRUNTIME_WORKSPACE_BACKEND` | `local` | 沙箱后端（生产必须 `docker` 或 `e2b`）|
| `XRUNTIME_RATE_LIMIT` | `100/60` | 限流配置 |

### Redis 相关

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `XRUNTIME_STORAGE_REDIS_HOST` | `localhost` | 存储 Redis host |
| `XRUNTIME_STORAGE_REDIS_PORT` | `6379` | 存储 Redis port |
| `XRUNTIME_STORAGE_REDIS_PASSWORD` | - | 存储 Redis 密码 |
| `XRUNTIME_MESSAGE_BUS_REDIS_HOST` | `localhost` | 消息总线 Redis host |
| `XRUNTIME_MESSAGE_BUS_REDIS_PORT` | `6379` | 消息总线 Redis port |
| `XRUNTIME_MESSAGE_BUS_REDIS_PASSWORD` | - | 消息总线 Redis 密码 |

### 可观测性

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `XRUNTIME_OTEL_ENABLED` | `false` | 启用 OpenTelemetry |
| `XRUNTIME_OTEL_ENDPOINT` | - | OTLP collector 地址 |
| `LANGFUSE_PUBLIC_KEY` | - | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | - | Langfuse secret key |
| `LANGFUSE_HOST` | - | Langfuse 服务地址 |

### LLM Provider Keys

| 变量 | Provider |
|------|----------|
| `OPENAI_API_KEY` | OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic |
| `DASHSCOPE_API_KEY` | 通义千问 |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `GEMINI_API_KEY` | Gemini |
| `OLLAMA_BASE_URL` | Ollama |
| `MOONSHOT_API_KEY` | Kimi |
| `XAI_API_KEY` | xAI Grok |

## 3.7 健康检查与故障排查

### 健康检查端点

```bash
curl http://localhost:8900/health
# 预期: {"status":"ok"}
```

### 常见问题

#### Q1: 启动报 `Redis connection refused`

**原因：** Redis 没启动或地址配错。

**排查：**

```bash
# 检查 Redis 是否运行
docker compose ps redis
redis-cli -h localhost -p 6379 -a $REDIS_PASSWORD ping
# 预期: PONG
```

#### Q2: 请求返回 401 Unauthorized

**原因：** API Key 未在 `XRUNTIME_API_KEY_RECORDS` 注册，或 JWT 密钥不匹配。

**排查：**

```bash
# 检查环境变量
echo $XRUNTIME_API_KEY_RECORDS | jq .

# 测试 API Key
curl -H "Authorization: Bearer sk-admin-dev" \
     http://localhost:8900/admin/status
```

#### Q3: 请求返回 403 Forbidden

**原因：** 角色不足，或跨租户查询。

**排查：** 检查 `API_KEY_RECORDS` 中该 key 的 `role` 字段和 `tenant_id`。

#### Q4: 启动报 `XRUNTIME_PRODUCTION=1 but WORKSPACE_BACKEND=local`

**原因：** 生产模式拒绝使用 `local` 沙箱后端。

**解决：**

```bash
export XRUNTIME_WORKSPACE_BACKEND=docker
# 或用 e2b 云沙箱
export XRUNTIME_WORKSPACE_BACKEND=e2b
```

#### Q5: 中间件数量为 0

**原因：** `SystemStatus.middleware_count` 默认 0，只有 XRuntime extension 正确装配后才会有值。

**排查：** 检查 `create_xruntime_extension()` 是否被正确调用，查看启动日志是否有 `XRuntime extension mounted` 字样。

---

# 第四部分：自定义开发教程

## 4.1 新增中间件

**场景：** 想在每轮对话前记录 token 使用量到自定义监控系统。

### 步骤 1：创建中间件文件

在 `src/xruntime/_runtime/_middleware/` 下新建 `_token_logger.py`：

```python
# -*- coding: utf-8 -*-
"""Custom middleware: log token usage to external monitor."""
from __future__ import annotations

import logging
from typing import Any

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger("xruntime.middleware.token_logger")


class TokenLoggerMiddleware(MiddlewareBase):
    """Log token usage after each LLM call.

    Args:
        endpoint (`str`):
            Webhook URL to receive token usage events.
    """

    def __init__(self, endpoint: str = "") -> None:
        self._endpoint = endpoint

    async def on_replying(
        self,
        agent: Any,
        prompt: Any,
        state: Any,
    ) -> Any:
        """Hook called before agent replies."""
        # 在 agent 执行前可以做准备工作
        return await self._next(agent, prompt, state)

    async def on_reply_end(
        self,
        agent: Any,
        reply: Any,
        state: Any,
    ) -> Any:
        """Hook called after agent replies — inspect token usage."""
        usage = getattr(reply, "usage", None)
        if usage:
            logger.info(
                "token_usage: input=%s output=%s",
                getattr(usage, "input_tokens", 0),
                getattr(usage, "output_tokens", 0),
            )
        return reply
```

### 步骤 2：注册到中间件链

编辑 `src/xruntime/_gateway/_extension.py`，在 `create_xruntime_extension()` 中找到中间件链组装部分，按正确位置插入：

```python
from xruntime._runtime._middleware._token_logger import (
    TokenLoggerMiddleware,
)

# 在 Audit 之后、Quota 之前插入
middleware_chain = [
    LangfuseTracer(config.langfuse),
    LoopDetectionMiddleware(...),
    LLMErrorHandlingMiddleware(...),
    AuditMiddleware(...),
    TokenLoggerMiddleware(endpoint=config.token_logger_endpoint),  # ← 新增
    QuotaMiddleware(...),
    # ...
]
```

### 步骤 3：添加配置项

在 `src/xruntime/_config.py` 的 `XRuntimeConfig` 中添加字段：

```python
class TokenLoggerConfig(BaseModel):
    """Token logger middleware configuration."""
    enabled: bool = False
    endpoint: str = ""

class XRuntimeConfig(BaseModel):
    # ... 既有字段 ...
    token_logger: TokenLoggerConfig = TokenLoggerConfig()
```

### 步骤 4：写测试

在 `tests/` 下新建 `test_token_logger.py`：

```python
import pytest
from xruntime._runtime._middleware._token_logger import (
    TokenLoggerMiddleware,
)


@pytest.mark.asyncio
async def test_token_logger_logs_usage(caplog):
    mw = TokenLoggerMiddleware(endpoint="http://example.com")
    # ... 构造 mock agent 和 reply ...
    with caplog.at_level("INFO"):
        await mw.on_reply_end(agent=mock_agent, reply=mock_reply, state={})
    assert "token_usage" in caplog.text
```

## 4.2 新增协议适配器

**场景：** 接入一个新的协议（如 Bedrock）。

### 步骤 1：实现 ProtocolAdapter

在 `src/xruntime/_gateway/` 下新建 `_bedrock_adapter.py`：

```python
# -*- coding: utf-8 -*-
"""Bedrock protocol adapter."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from ._adapter import ProtocolAdapter
from ._request import ProtocolType, XRuntimeRequest


class BedrockAdapter(ProtocolAdapter):
    """Protocol adapter for AWS Bedrock."""

    protocol_type = ProtocolType.BEDROCK  # 需先在 _request.py 中添加枚举值

    async def parse_request(
        self,
        raw: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> XRuntimeRequest:
        headers = headers or {}
        # 解析 Bedrock 请求体
        prompt = raw.get("prompt", "")
        return XRuntimeRequest(
            protocol=ProtocolType.BEDROCK,
            prompt=prompt,
            session_id=headers.get("x-session-id"),
            user_id=headers.get("x-user-id", "anonymous"),
            tenant_id=headers.get("x-tenant-id", "default"),
        )

    async def serialize_event_stream(
        self,
        events: AsyncGenerator[dict[str, Any], None],
    ) -> AsyncGenerator[bytes, None]:
        async for event in events:
            # 把 AgentEvent 转成 Bedrock 格式
            yield self._convert(event).encode()

    def _convert(self, event: dict) -> str:
        # 实现格式转换逻辑
        import json
        return json.dumps(event)
```

### 步骤 2：在 ProtocolType 中注册

编辑 `src/xruntime/_gateway/_request.py`：

```python
class ProtocolType(str, enum.Enum):
    ANTHROPIC = "anthropic"
    CLAUDE_CODE = "claude_code"
    OPENCODE = "opencode"
    BEDROCK = "bedrock"      # ← 新增
```

### 步骤 3：挂载路由

编辑 `src/xruntime/_gateway/_extension.py::mount_protocol_adapters()`，添加新路由：

```python
from ._bedrock_adapter import BedrockAdapter

def mount_protocol_adapters(app, config):
    # ... 既有路由 ...

    # Bedrock 路由
    bedrock_adapter = BedrockAdapter()
    runtime = app.state.xruntime_runtime

    @app.post("/v1/bedrock/invoke")
    async def bedrock_invoke(request: Request):
        raw = await request.json()
        headers = dict(request.headers)
        xruntime_req = await bedrock_adapter.parse_request(raw, headers=headers)
        events = runtime.execute(xruntime_req)
        return StreamingResponse(
            bedrock_adapter.serialize_event_stream(events),
            media_type="application/json",
        )
```

## 4.3 新增模型 Provider

**场景：** 接入一个新的 LLM 提供商（如 Yi / Moonshot 新模型）。

XRuntime 的模型 Provider 复用 AgentScope 的 model 层。参考 `AGENTS.md` 中 "Contributing a chat model" 章节，需要提供四个部分：

### 步骤 1：创建 Credential 类

在 `src/agentscope/credential/` 下新建 `_yi.py`：

```python
from ._base import CredentialBase


class YiCredential(CredentialBase):
    """Credential for Yi / 01.AI API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def get_api_key(self) -> str:
        return self._api_key
```

### 步骤 2：创建 Model 类

在 `src/agentscope/model/` 下新建 `_yi/` 目录，实现 `YiChatModel(ChatModelBase)`，覆盖流式 / 非流式 / 工具调用 / tool_choice / reasoning 模型。

### 步骤 3：创建 Model Card

在 `src/agentscope/model/_yi/_models/` 下创建 YAML 文件：

```yaml
# yi-large.yaml
name: yi-large
label: "Yi Large"
status: active
input_types:
  - text
output_types:
  - text
context_size: 32768
output_size: 4096
```

### 步骤 4：创建 Formatter

在 `src/agentscope/formatter/` 下创建 `YiChatFormatter` 和 `YiMultiAgentFormatter`。

### 步骤 5：在 XRuntime 配置中注册

在 `xruntime.yaml` 中添加：

```yaml
model_providers:
  - name: "yi-large"
    provider: "yi"
    model: "yi-large"
    credential_env: "YI_API_KEY"
```

设置环境变量 `YI_API_KEY`，重启服务即可。

## 4.4 新增知识库后端

**场景：** 想用 Milvus 替代默认的 LLM-Wiki 作为知识库后端。

### 步骤 1：实现 KnowledgeBase 接口

在 `src/xruntime/_runtime/_knowledge/` 下新建 `_milvus_adapter.py`：

```python
# -*- coding: utf-8 -*-
"""Milvus knowledge base backend."""
from __future__ import annotations

from typing import Any

from ._base import KnowledgeBase, KnowledgeBaseConfig


class MilvusConfig(KnowledgeBaseConfig):
    """Configuration for Milvus backend."""

    host: str = "localhost"
    port: int = 19530
    collection_name: str = "xruntime_kb"
    embedding_dim: int = 1536


class MilvusKnowledgeBase(KnowledgeBase):
    """Milvus-backed knowledge base."""

    def __init__(self, config: MilvusConfig) -> None:
        super().__init__(config)
        self._config = config
        self._client = None

    async def initialize(self) -> None:
        """Connect to Milvus and ensure collection exists."""
        from pymilvus import connections, Collection
        connections.connect(
            host=self._config.host,
            port=self._config.port,
        )
        # ... 创建 collection ...

    async def ingest(
        self,
        source_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Ingest a document into Milvus."""
        # 路径穿越校验（必须有）
        if ".." in source_id or "/" in source_id:
            raise ValueError("Invalid source_id")
        # ... 写入 Milvus ...

    async def query(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Query Milvus for relevant documents."""
        # ... 向量检索 ...

    async def delete_source(self, source_id: str) -> None:
        """Delete a document by source_id."""
        # 路径穿越校验（必须有）
        if not source_id or not source_id.strip():
            raise ValueError("source_id must not be empty")
        if (
            ".." in source_id
            or "/" in source_id
            or os.path.isabs(source_id)
        ):
            raise ValueError("Invalid source_id")
        # ... 从 Milvus 删除 ...
```

### 步骤 2：注册到 Factory

在文件底部添加：

```python
from ._adapter import KnowledgeBackendFactory

def _register_milvus_adapter():
    factory = KnowledgeBackendFactory()
    factory.register("milvus", MilvusKnowledgeBase, MilvusConfig)

_register_milvus_adapter()
```

### 步骤 3：在配置中指定后端

```yaml
knowledge:
  backend: "milvus"
  config:
    host: "milvus-host"
    port: 19530
    collection_name: "xruntime_kb"
```

## 4.5 新增记忆存储后端

**场景：** 用 Postgres 替代 Redis 存储长期记忆。

### 步骤 1：实现 MemoryStore 接口

在 `src/xruntime/_runtime/_memory/` 下新建 `_postgres_store.py`：

```python
# -*- coding: utf-8 -*-
"""PostgreSQL-backed memory store."""
from __future__ import annotations

from typing import Any

from ._models import MemoryItem
from ._store import MemoryStore


class PostgresMemoryStore(MemoryStore):
    """PostgreSQL-backed memory store."""

    def __init__(
        self,
        dsn: str = "",
        tenant_prefix: str = "tenant:{tid}:",
    ) -> None:
        self._dsn = dsn
        self._prefix = tenant_prefix
        self._pool = None

    async def initialize(self) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(self._dsn)
        # 创建表（如果不存在）
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    type TEXT NOT NULL,
                    confidence FLOAT DEFAULT 0.0,
                    embedding BYTEA,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

    def save(self, item: MemoryItem) -> None:
        # ... INSERT ...
        pass

    def search(
        self,
        query: str,
        user_id: str = "",
        tenant_id: str = "default",
        top_k: int = 10,
    ) -> list[MemoryItem]:
        # ... SELECT + 向量检索 ...
        pass

    def list_all(
        self,
        user_id: str = "",
        tenant_id: str = "default",
    ) -> list[MemoryItem]:
        # ... SELECT ...
        pass

    @property
    def count(self) -> int:
        # ... SELECT COUNT(*) ...
        return 0
```

### 步骤 2：在装配点切换

编辑 `src/xruntime/_gateway/_extension.py`，在创建 memory store 的地方根据 config 选择实现：

```python
if config.storage.backend == "postgres":
    from xruntime._runtime._memory._postgres_store import (
        PostgresMemoryStore,
    )
    memory_store = PostgresMemoryStore(dsn=config.storage.postgres_dsn)
else:
    from xruntime._runtime._memory._redis_store import (
        RedisMemoryStore,
    )
    memory_store = RedisMemoryStore(...)
```

## 4.6 新增 Skill 技能

**场景：** 给 Agent 注入一个自定义技能（如"代码审查"技能）。

### 步骤 1：创建 SKILL.yaml

在 `skills/public/` 下新建目录 `code-review/`，创建 `SKILL.yaml`：

```yaml
# skills/public/code-review/SKILL.yaml
name: code-review
description: >
  Code review skill. When activated, the agent reviews code
  changes following the project's style guide and reports
  findings in a structured format.
trigger:
  keywords:
    - "review"
    - "code review"
    - "审查"
  pattern: "review\\s+(my\\s+)?code"
instructions: |
  You are a code reviewer. Follow these steps:
  1. Read the changed files
  2. Check for style violations
  3. Look for security issues
  4. Report findings as a markdown table
tools:
  - Read
  - Glob
  - Grep
metadata:
  author: "your-team"
  version: "1.0"
```

### 步骤 2：重启服务

Skill 在启动时扫描 `skills/` 目录，重启服务即可生效。

### 步骤 3：使用技能

在对话中触发关键词（如 "review my code"），`SkillInjectionMiddleware` 会自动加载该技能的 `instructions` 注入到 system prompt。

### 步骤 4：验证

```bash
curl -X POST http://localhost:8900/admin/skills \
  -H "Authorization: Bearer sk-admin-dev"
# 预期返回中包含 "code-review"
```

---

# 第五部分：面向小白的完全 SOP 操作手册

> 本部分假设你是一个从未接触过本项目的开发者，从零开始逐步操作。

## 5.1 SOP-1：从零搭建并启动服务

**目标：** 在本地把服务跑起来，能访问 `/health` 返回 ok。

### 前置条件

- 已安装 Docker Desktop（或 Docker Engine + docker-compose）
- 终端能执行 `docker --version` 且版本 >= 24.0

### 操作步骤

**1. 克隆代码**

```bash
cd ~/your-workspace
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime
```

**2. 复制环境变量模板**

```bash
cp .env.example .env
```

**3. 生成强密钥并写入 .env**

```bash
# 生成 JWT 密钥
JWT_SECRET=$(openssl rand -hex 32)
echo "JWT_SECRET=$JWT_SECRET" >> .env

# 生成 Redis 密码
REDIS_PASSWORD=$(openssl rand -hex 16)
# 用 sed 替换 .env 中的默认值
sed -i.bak "s/REDIS_PASSWORD=.*/REDIS_PASSWORD=$REDIS_PASSWORD/" .env
rm .env.bak
```

**4. 生成 API Key 并写入 .env**

```bash
# 生成一个强 API Key
API_KEY="sk-admin-$(openssl rand -hex 16)"

# 构造 API_KEY_RECORDS
cat >> .env << EOF
API_KEY_RECORDS=[{"key":"$API_KEY","tenant_id":"acme","user_id":"admin","role":"admin","kb_ids":[]}]
EOF

# 记住这个 API_KEY，后面要用
echo "你的 API Key: $API_KEY"
```

**5. 启动服务**

```bash
docker compose up -d
```

等待 10-30 秒，期间会下载镜像和初始化。

**6. 验证启动**

```bash
# 等待健康检查通过
docker compose ps

# 健康检查
curl http://localhost:8900/health
```

预期输出：

```json
{"status":"ok"}
```

**7. 用 API Key 测试**

```bash
curl -H "Authorization: Bearer $API_KEY" \
     http://localhost:8900/admin/status
```

预期返回包含 `version`、`total_skills` 等字段的 JSON。

### 完成标志

- `docker compose ps` 显示 `xin-redis` 和 `xin-runtime` 都是 `running` 状态
- `/health` 返回 `{"status":"ok"}`
- `/admin/status` 能用 API Key 访问

### 常见卡点

- **端口被占用：** 修改 `.env` 里的 `XRUNTIME_PORT=8910`（或其他空闲端口）
- **镜像拉取慢：** 配置 Docker 镜像加速器
- **健康检查失败：** `docker compose logs runtime` 查看错误日志

## 5.2 SOP-2：创建租户与 API Key

**目标：** 为不同团队创建独立的租户和 API Key。

### 背景知识

- **租户（Tenant）**：逻辑隔离的单位，不同租户的数据互相不可见。
- **API Key**：绑定到一个租户和一个用户，带角色权限。
- **角色**：`owner` > `admin` > `contributor` > `viewer`。

### 操作步骤

**1. 编辑 .env 文件**

找到 `API_KEY_RECORDS=` 这一行，按以下格式添加多个 key：

```bash
API_KEY_RECORDS=[
  {"key":"sk-acme-admin-xxx","tenant_id":"acme","user_id":"alice","role":"admin","kb_ids":["kb-acme-1"]},
  {"key":"sk-acme-user-xxx","tenant_id":"acme","user_id":"bob","role":"contributor","kb_ids":["kb-acme-1"]},
  {"key":"sk-beta-admin-xxx","tenant_id":"beta","user_id":"carol","role":"admin","kb_ids":["kb-beta-1"]}
]
```

注意：JSON 必须是单行，或用 `>` 多行字符串。实际写法是**压缩成一行**：

```bash
API_KEY_RECORDS=[{"key":"sk-acme-admin-xxx","tenant_id":"acme","user_id":"alice","role":"admin","kb_ids":["kb-acme-1"]},{"key":"sk-acme-user-xxx","tenant_id":"acme","user_id":"bob","role":"contributor","kb_ids":["kb-acme-1"]},{"key":"sk-beta-admin-xxx","tenant_id":"beta","user_id":"carol","role":"admin","kb_ids":["kb-beta-1"]}]
```

**2. 重启服务**

```bash
docker compose restart runtime
```

**3. 验证**

```bash
# 用 acme 的 admin key 查询
curl -H "Authorization: Bearer sk-acme-admin-xxx" \
     http://localhost:8900/admin/status

# 用 beta 的 admin key 查询（应该看到不同的租户数据）
curl -H "Authorization: Bearer sk-beta-admin-xxx" \
     http://localhost:8900/admin/status
```

### 完成标志

- 不同租户的 API Key 都能正常访问
- 跨租户查询会被拒绝（返回 403）

## 5.3 SOP-3：使用 SDK 发起一次对话

**目标：** 用 `xruntime_sdk` 发起一次 Agent 对话。

### 前置条件

- 已完成 SOP-1，服务正常运行
- 已配置至少一个 LLM provider（如 `OPENAI_API_KEY`）

### 操作步骤

**1. 安装 SDK**

```bash
pip install xin-agent-runtime
# 或
uv pip install xin-agent-runtime
```

**2. 编写脚本**

创建 `test_chat.py`：

```python
import asyncio
from xruntime_sdk import create_client

async def main():
    client = create_client(
        base_url="http://localhost:8900",
        tenant_id="acme",
        api_key="sk-acme-admin-xxx",  # 替换为你的 key
    )

    # 简单对话
    result = await client.query(
        protocol="anthropic",
        prompt="Hello! What can you do?",
        model="claude-sonnet-4-20250514",  # 或你配置的模型
    )

    # 打印响应
    async for chunk in result:
        print(chunk, end="", flush=True)

asyncio.run(main())
```

**3. 运行**

```bash
# 确保环境变量有 LLM key
export OPENAI_API_KEY="sk-your-openai-key"

python test_chat.py
```

### 完成标志

- 看到 Agent 的回复输出

## 5.4 SOP-4：接入 Anthropic 协议客户端

**目标：** 用任何兼容 Anthropic Messages API 的客户端连接 XRuntime。

### 操作步骤

**1. 获取你的 API Key**

参考 SOP-1 步骤 4 生成的 key。

**2. 配置客户端**

以 Python `anthropic` SDK 为例：

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8900/v1",
    api_key="sk-acme-admin-xxx",  # 你的 XRuntime API Key
)

response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Hello, who are you?"}
    ],
)

print(response.content[0].text)
```

**3. 用 curl 测试**

```bash
curl -X POST http://localhost:8900/v1/messages \
  -H "Authorization: Bearer sk-acme-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

### 完成标志

- 客户端能正常发起对话
- 服务端返回符合 Anthropic Messages API 格式的响应

## 5.5 SOP-5：接入 Claude Code SDK

**目标：** 用 Claude Code SDK 通过 XRuntime 执行 Agent 任务。

### 操作步骤

**1. 确保模型支持**

`xruntime.yaml` 中至少配置一个支持工具调用的模型（如 `claude-sonnet-4-20250514`）。

**2. 发起 Claude Code 查询**

```python
import asyncio
from xruntime_sdk import create_client

async def main():
    client = create_client(
        base_url="http://localhost:8900",
        tenant_id="acme",
        api_key="sk-acme-admin-xxx",
    )

    result = await client.query(
        protocol="claude_code",
        prompt="Read the README.md and summarize it",
        options={
            "allowed_tools": ["Read", "Glob", "Grep"],
            "permission_mode": "acceptEdits",
            "max_turns": 10,
            "max_budget_usd": 5.0,
        },
    )

    async for event in result:
        print(event)

asyncio.run(main())
```

**3. 用 curl 测试**

```bash
curl -X POST http://localhost:8900/v1/claude-code/query \
  -H "Authorization: Bearer sk-acme-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "List all Python files in the current directory",
    "allowed_tools": ["Bash", "Glob"],
    "permission_mode": "acceptEdits",
    "max_turns": 5
  }'
```

### 完成标志

- Agent 能正确执行工具调用
- 返回工具调用结果

## 5.6 SOP-6：管理知识库与上传文档

**目标：** 创建知识库、上传文档、在对话中检索。

### 操作步骤

**1. 通过 API 创建知识库**

```bash
curl -X POST http://localhost:8900/v1/knowledge/bases \
  -H "Authorization: Bearer sk-acme-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "kb_id": "kb-acme-1",
    "name": "ACME Product Docs",
    "backend": "llm_wiki"
  }'
```

**2. 上传文档**

```bash
curl -X POST http://localhost:8900/v1/knowledge/bases/kb-acme-1/ingest \
  -H "Authorization: Bearer sk-acme-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "product-spec-v1",
    "content": "Our product is an enterprise agent runtime..."
  }'
```

**3. 检索测试**

```bash
curl -X POST http://localhost:8900/v1/knowledge/bases/kb-acme-1/query \
  -H "Authorization: Bearer sk-acme-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the product?",
    "top_k": 3
  }'
```

**4. 在对话中自动检索**

当 Agent 收到对话请求时，`KnowledgeMiddleware` 会自动用当前 prompt 去 KB 检索，把命中片段注入 system prompt。你不需要手动操作。

### 完成标志

- 文档上传成功
- 检索能返回相关片段
- 对话中 Agent 能引用知识库内容

## 5.7 SOP-7：通过 Admin API 查看系统状态

**目标：** 监控服务运行状态。

### 操作步骤

**1. 系统总览**

```bash
curl -H "Authorization: Bearer sk-acme-admin-xxx" \
     http://localhost:8900/admin/status
```

返回示例：

```json
{
  "total_skills": 3,
  "total_memories": 42,
  "active_sessions": 2,
  "middleware_count": 10,
  "langfuse_enabled": false,
  "redis_enabled": true,
  "version": "1.0.0"
}
```

**2. 列出技能**

```bash
curl -H "Authorization: Bearer sk-acme-admin-xxx" \
     http://localhost:8900/admin/skills
```

**3. 查看技能详情**

```bash
curl -H "Authorization: Bearer sk-acme-admin-xxx" \
     http://localhost:8900/admin/skills/coding
```

**4. 搜索记忆**

```bash
curl -X POST http://localhost:8900/admin/memories/search \
  -H "Authorization: Bearer sk-acme-admin-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "user preferences",
    "tenant_id": "acme",
    "limit": 5
  }'
```

**5. 查看模型列表**

```bash
curl -H "Authorization: Bearer sk-acme-admin-xxx" \
     http://localhost:8900/admin/models
```

**6. 查看性能指标**

```bash
curl -H "Authorization: Bearer sk-acme-admin-xxx" \
     http://localhost:8900/admin/metrics/summary
```

### 完成标志

- 所有 admin 端点都能正常返回
- 跨租户查询返回 403

## 5.8 SOP-8：开启 Langfuse 可观测性

**目标：** 把 Agent 对话 trace 推送到 Langfuse，可视化分析。

### 前置条件

- 已部署 Langfuse（自建或用 Langfuse Cloud）
- 拿到 `public_key` 和 `secret_key`

### 操作步骤

**1. 配置环境变量**

编辑 `.env`，添加：

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_HOST=https://your-langfuse.example.com
```

**2. 在 xruntime.yaml 中启用**

```yaml
observability:
  langfuse_enabled: true
  langfuse_public_key: "${LANGFUSE_PUBLIC_KEY}"
  langfuse_secret_key: "${LANGFUSE_SECRET_KEY}"
  langfuse_host: "${LANGFUSE_HOST}"
```

**3. 重启服务**

```bash
docker compose restart runtime
```

**4. 发起对话并查看 trace**

发起几次对话后，到 Langfuse 控制台查看 trace，应该能看到：

- 每轮对话的完整 span
- 中间件链的执行顺序和耗时
- LLM 调用的 token 用量
- 工具调用的入参和结果

### 完成标志

- Langfuse 控制台能看到 trace
- trace 中包含中间件链的 span

## 5.9 SOP-9：配置 Human-in-the-Loop 审批

**目标：** 对敏感工具调用启用人工审批。

### 操作步骤

**1. 在 xruntime.yaml 中启用 approval**

```yaml
approval:
  enabled: true
  strategy: "always"             # always / once / never / predicate
  timeout_seconds: 300          # 5 分钟超时
  always_require_tools:         # 这些工具必须审批
    - "Bash"
    - "Write"
    - "Edit"
  never_require_tools: []       # 这些工具永不审批
```

**2. 实现 approval callback**

Approval 需要一个外部回调来接收审批请求。你可以用 webhook 方式：

```python
# 在你的审批服务中
from fastapi import FastAPI, Request
from xruntime._runtime._middleware._approval import (
    ApprovalDecision,
    ApprovalRequest,
)

app = FastAPI()

@app.post("/approval/callback")
async def handle_approval(request: Request):
    data = await request.json()
    approval_req = ApprovalRequest(**data)

    # 你的审批逻辑
    # 比如推送到 Slack，等人类点击 Approve / Deny
    approved = await ask_human(approval_req)

    return ApprovalDecision(
        approved=approved,
        reason="Approved by admin" if approved else "Denied",
        approver="admin@acme.com",
    )
```

**3. 重启服务**

```bash
docker compose restart runtime
```

**4. 测试**

发起一个会触发 `Bash` 工具的对话，应该看到：

- Agent 调用 `Bash` 前被挂起
- 你的审批服务收到请求
- 审批通过后，Agent 继续执行

### 完成标志

- Agent 在调用配置的工具前会等待审批
- 审批通过后能继续执行
- 审批拒绝时返回 `PermissionError`

## 5.10 SOP-10：生产环境上线 Checklist

**目标：** 上线前确认所有安全配置就位。

### Checklist

#### 认证与授权

- [ ] `XRUNTIME_JWT_SECRET` 使用 `openssl rand -hex 32` 生成（>= 32 字节）
- [ ] `XRUNTIME_API_KEY_RECORDS` 中所有 key 都是强随机字符
- [ ] 每个 key 的 `role` 符合最小权限原则（不滥用 `admin`）
- [ ] `kb_ids` 字段限制了 key 能访问的 KB 范围

#### 沙箱与隔离

- [ ] `XRUNTIME_PRODUCTION=1`
- [ ] `XRUNTIME_WORKSPACE_BACKEND=docker`（绝不使用 `local`）
- [ ] Docker socket **没有**挂载到容器内（`docker-compose.yml` 中已移除）
- [ ] 多租户测试：租户 A 的 key 无法查询租户 B 的数据

#### 网络

- [ ] 服务端口 8900 不直接暴露到公网（用反向代理 + TLS）
- [ ] Redis 端口 6379 不暴露到公网
- [ ] 配置了限流（`XRUNTIME_RATE_LIMIT`）
- [ ] 配置了 WAF 或 Cloudflare 防护

#### 可观测性

- [ ] 启用了 Langfuse 或 OpenTelemetry
- [ ] 配置了 Prometheus 指标采集
- [ ] 日志聚合到 ELK / Loki
- [ ] 配置了告警规则（错误率 / 延迟 / 内存）

#### 数据持久化

- [ ] Redis 启用了 `appendonly yes`
- [ ] 配置了 Redis 备份策略
- [ ] 知识库 / 记忆数据有定期备份

#### LLM Provider

- [ ] API Key 通过环境变量注入，不硬编码
- [ ] 配置了模型 fallback 策略
- [ ] 设置了 per-tenant 模型 allowlist（如需要）

#### 性能

- [ ] 做过压测（建议 >= 50 并发）
- [ ] P95 延迟符合预期
- [ ] 内存使用稳定（无泄漏）

#### 应急

- [ ] 有回滚方案
- [ ] 有故障联系人
- [ ] 知道如何快速停服（`docker compose down`）
- [ ] 知道如何查看日志（`docker compose logs`）

### 上线命令

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 重新构建镜像
docker compose build

# 3. 启动（带健康检查）
docker compose up -d

# 4. 等待健康
until curl -sf http://localhost:8900/health; do
    echo "Waiting for service..."
    sleep 5
done

# 5. 验证
curl -H "Authorization: Bearer $API_KEY" \
     http://localhost:8900/admin/status
```

---

# 附录

## A. 常用命令速查

```bash
# 启动
docker compose up -d

# 停止
docker compose down

# 查看日志
docker compose logs -f runtime
docker compose logs -f redis

# 重启
docker compose restart runtime

# 重新构建
docker compose build runtime

# 进入容器
docker compose exec runtime bash

# Redis CLI
docker compose exec redis redis-cli -a $REDIS_PASSWORD

# 健康检查
curl http://localhost:8900/health

# 查看状态
curl -H "Authorization: Bearer $API_KEY" \
     http://localhost:8900/admin/status
```

## B. 开发常用命令

```bash
# 安装开发依赖
uv pip install -e ".[dev]"

# 跑测试
pytest tests/

# 跑单个测试
pytest tests/test_foo.py::test_bar -p no:cacheprovider

# 代码格式化
black --line-length=79 src/
flake8 src/
mypy src/

# 预提交检查
pre-commit run --all-files

# 启动开发服务器（热重载）
uvicorn xruntime._server:app --reload --port 8900
```

## C. 文档导航

| 文档 | 路径 | 内容 |
|------|------|------|
| 架构详解 | `docs/xruntime/ARCHITECTURE.md` | 深入架构设计 |
| 模块文档 | `docs/xruntime/MODULE-ARCHITECTURE.md` | 各模块详细说明 |
| 部署指南 | `docs/xruntime/DEPLOYMENT-GUIDE.md` | 生产部署详解 |
| Docker 部署 | `docs/xruntime/DOCKER_DEPLOY.md` | Docker 专项 |
| 安全架构 | `docs/xruntime/FINAL-SECURITY-ARCHITECTURE.md` | 10 层防御体系 |
| 多租户隔离 | `docs/xruntime/MULTI-TENANT-ISOLATION.md` | 隔离机制详解 |
| Langfuse 指南 | `docs/xruntime/LANGFUSE-GUIDE.md` | 可观测性配置 |
| SDK 指南 | `docs/xruntime/SDK-GUIDE.md` | SDK 使用 |
| 运维手册 | `docs/xruntime/OPS-GUIDE.md` | 日常运维 |
| 沙箱架构 | `docs/xruntime/SANDBOX-ARCHITECTURE.md` | 沙箱设计 |
| 快速开始 | `docs/xruntime/QUICKSTART.md` | 5 分钟上手 |
| 生产部署 | `docs/xruntime/PRODUCTION-DEPLOYMENT.md` | 生产环境 |
| ADR | `docs/adr/` | 架构决策记录 |

## D. 关键 ADR

| ADR | 标题 | 摘要 |
|-----|------|------|
| ADR-001 | XRuntime as Extension | XRuntime 作为 AgentScope 扩展而非独立运行时 |
| ADR-002 | Tenant Key Prefix Isolation | 用 Redis key 前缀实现租户隔离 |
| ADR-003 | BM25 Retrieval | 知识库默认用 BM25 检索 |
| ADR-004 | Runtime Execution Plan | 三协议统一执行计划 |
| ADR-005 | Workspace Production Safety | 生产环境拒绝 local 沙箱 |
| ADR-006 | Model Governance | 模型治理框架 |
| ADR-007 | Langfuse Observability | 可观测性采用 Langfuse |

## E. 故障排查流程图

```
问题发生
   │
   ├─ 服务无法启动？
   │   ├─ 检查 docker compose ps
   │   ├─ 检查 docker compose logs runtime
   │   └─ 检查 .env 配置（特别是 REDIS_PASSWORD / JWT_SECRET）
   │
   ├─ 请求返回 401？
   │   ├─ 检查 API Key 是否在 API_KEY_RECORDS 中
   │   ├─ 检查 Authorization header 格式
   │   └─ 检查 key 的 active 字段
   │
   ├─ 请求返回 403？
   │   ├─ 检查 role 是否足够
   │   ├─ 检查是否跨租户查询
   │   └─ 检查 kb_ids 是否包含目标 KB
   │
   ├─ 请求返回 429？
   │   ├─ 限流触发，调整 XRUNTIME_RATE_LIMIT
   │   └─ 检查是否有异常客户端在刷接口
   │
   ├─ LLM 调用失败？
   │   ├─ 检查 provider API Key 是否有效
   │   ├─ 检查模型名是否正确
   │   └─ 检查网络是否能访问 provider
   │
   └─ 性能问题？
       ├─ 查看 /admin/metrics/summary
       ├─ 检查 Redis 内存使用
       ├─ 检查容器 CPU / 内存
       └─ 启用 Langfuse 查看 trace 耗时分布
```

## F. 联系与反馈

- **GitHub Issues**: https://github.com/tohnee/xin-agent-runtime/issues
- **文档目录**: `docs/xruntime/`
- **贡献指南**: `CONTRIBUTING.md`

---

**文档版本：** v1.0
**最后更新：** 2026-07-02
**适用代码版本：** main 分支
