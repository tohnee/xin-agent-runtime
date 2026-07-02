# XRuntime 代码实现方案详解

> 面向初学者的完整架构指南。读完本文，你将理解 XRuntime 每一层在做什么、为什么这样设计、代码在哪里。

---

## 目录

1. [XRuntime 是什么](#1-xruntime-是什么)
2. [整体架构鸟瞰图](#2-整体架构鸟瞰图)
3. [第一层：协议网关层](#3-第一层协议网关层)
4. [第二层：认证与安全层](#4-第二层认证与安全层)
5. [第三层：多租户与 RBAC 层](#5-第三层多租户与-rbac-层)
6. [第四层：中间件管道层](#6-第四层中间件管道层)
7. [第五层：知识库层](#7-第五层知识库层)
8. [第六层：模型治理层](#8-第六层模型治理层)
9. [第七层：工作空间沙箱层](#9-第七层工作空间沙箱层)
10. [第八层：可观测性层](#10-第八层可观测性层)
11. [核心数据流：一个请求的完整旅程](#11-核心数据流一个请求的完整旅程)
12. [配置系统](#12-配置系统)
13. [测试体系](#13-测试体系)
14. [文件索引](#14-文件索引)

---

## 1. XRuntime 是什么

### 一句话解释

XRuntime 是一个**企业级 AI Agent 运行时平台**——它让你能安全地把 AI 助手部署给多个团队使用，每个团队有自己的知识库、权限、预算，互不干扰。

### 为什么需要它

想象你在一个大公司，不同部门都想用 AI 助手：

- **法务部**：需要查询合同模板，不能让其他部门看到
- **研发部**：需要执行代码，但不能影响服务器
- **客服部**：有每日预算限制，不能无限调用
- **管理层**：需要审计所有 AI 操作

直接用 OpenAI API 解决不了这些问题。XRuntime 就是在 AI 模型和业务之间加了一层"安全+管理"的中间层。

### 它和 AgentScope 的关系

```
你的业务代码
     ↓
XRuntime (企业扩展层)     ← 权限、多租户、知识库、审计
     ↓
AgentScope (运行时内核)   ← Agent 执行、消息总线、工具调用
     ↓
LLM (大语言模型)          ← Claude/GPT/GLM 等
```

XRuntime 是 AgentScope 的**扩展插件**，复用了 AgentScope 的核心能力，在上面加了企业需要的安全和管理功能。

---

## 2. 整体架构鸟瞰图

```
                    ┌─────────────────────────────────────┐
                    │         客户端 / SDK                 │
                    │  (Anthropic SDK / Claude Code /     │
                    │   OpenCode / curl)                  │
                    └─────────────┬───────────────────────┘
                                  │ HTTP 请求
                    ┌─────────────▼───────────────────────┐
     第1层 网关      │   Protocol Adapters (协议适配器)      │
                    │   /v1/messages    (Anthropic)        │
                    │   /v1/claude-code/query (Claude Code)│
                    │   /v1/opencode    (OpenCode)         │
                    └─────────────┬───────────────────────┘
                                  │ XRuntimeRequest (统一格式)
                    ┌─────────────▼───────────────────────┐
     第2层 安全      │   Auth + RateLimit                   │
                    │   API Key / JWT 验证 + 滑动窗口限流   │
                    └─────────────┬───────────────────────┘
                                  │ AuthPrincipal (认证主体)
                    ┌─────────────▼───────────────────────┐
     第3层 租户      │   Tenant + RBAC                     │
                    │   租户隔离 + 4角色 × 16权限矩阵        │
                    └─────────────┬───────────────────────┘
                                  │ RuntimeExecutionPlan (执行计划)
                    ┌─────────────▼───────────────────────┐
     第4层 中间件    │   Middleware Chain (中间件管道)       │
                    │   RBAC → Quota → Audit → Redaction   │
                    │   → Knowledge → SkillInjection → ...  │
                    └─────────────┬───────────────────────┘
                                  │ AgentEvent (事件流)
                    ┌─────────────▼───────────────────────┐
     内核 AgentScope │   Agent + Tools + Message Bus       │
                    │   (实际执行 AI 对话和工具调用)          │
                    └─────────────┬───────────────────────┘
                                  │ 响应事件流
                    ┌─────────────▼───────────────────────┐
     第8层 可观测    │   Langfuse + OTel + Prometheus      │
                    │   (追踪、指标、日志)                   │
                    └─────────────────────────────────────┘
```

### 设计原则

| 原则 | 说明 |
|------|------|
| **默认安全** | 所有功能默认关闭危险选项，需要显式开启 |
| **Fail-Closed** | 认证失败时拒绝所有请求，而非放行 |
| **权限只收紧** | 客户端请求的权限不能超过租户配置的范围 |
| **协议无关** | 三种协议统一转换为内部格式，下游不需要关心来源 |
| **优雅降级** | 可选依赖（Langfuse/OTel）缺失时不影响主流程 |

---

## 3. 第一层：协议网关层

### 它解决什么问题

不同 AI 客户端说不同的"语言"：Anthropic SDK 用 `messages` 数组，Claude Code 用 `metadata` 字段传递沙箱配置，OpenCode 用 `allowed_tools` 列表。如果每种协议都要写一套独立的后端逻辑，代码会爆炸。

### 怎么解决的

**协议适配器模式**：每种协议有一个 Adapter，只负责把自有格式翻译成统一的 `XRuntimeRequest`。

```
Anthropic 请求 ──→ AnthropicAdapter ──→ XRuntimeRequest
Claude Code   ──→ ClaudeCodeAdapter ──→ XRuntimeRequest  ──→ 统一处理
OpenCode      ──→ OpenCodeAdapter  ──→ XRuntimeRequest
```

### 代码位置

| 文件 | 职责 |
|------|------|
| `_knowledge/_base.py` | 数据模型: KnowledgeQuery, KnowledgeResult |
| `_knowledge/_adapter.py` | 适配器基类 |
| `_knowledge/_llm_wiki_adapter.py` | LLM-Wiki 实现 (BM25, 审计, 脱敏) |
| `_knowledge/_acl.py` | KB 级别 ACL 权限 |
| `_knowledge/_middleware.py` | 知识注入中间件 |
| `_knowledge/_tools.py` | Agent 可调用的搜索/写入工具 |
| `_knowledge/_registry.py` | 多后端注册管理 |

### 关键设计

**BM25 检索算法** (替代简单关键词匹配):

BM25 是信息检索领域的经典算法，考虑了词频(TF)、逆文档频率(IDF)和文档长度归一化。比简单的 `if keyword in text` 效果好很多，且不需要训练模型。

**租户物理路径隔离**:

不同租户的知识库存储在不同目录下，即使 source_id 相同也不会冲突:

```
data/raw/tenants/acme/kbs/product-docs/raw/doc1.json     ← ACME的文档
data/raw/tenants/globex/kbs/product-docs/raw/doc1.json   ← Globex的文档
```

**路径穿越防护** (代码审查修复):

```python
# tenant_id = "../../etc" 会被拒绝
if ".." in value or "/" in value or os.sep in value:
    raise ValueError(f"Path traversal detected in {label}: {value}")
```

**Ingest 前密钥脱敏**:

文档写入存储前，自动替换 API Key、Bearer Token、私钥:

```python
redacted = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[REDACTED_API_KEY]", content)
```

---

## 8. 第六层：模型治理层

### 它解决什么问题

不同模型能力不同、价格不同。比如:
- 简单问答用 Haiku (便宜)
- 复杂推理用 Opus (贵)
- 有些模型不支持工具调用
- 不同租户允许使用不同模型

### 怎么解决的

**能力注册表 + 智能路由**:

```
任务进来 → 判断需要什么能力(工具?视觉?) → 查注册表匹配 → 检查租户白名单 → 选择模型
```

### 代码位置

| 文件 | 职责 |
|------|------|
| `_runtime/_model_governance.py` | ModelCapability + Registry + Router |
| `_runtime/_model_router.py` | 基于复杂度的多模型路由 |
| `_runtime/_model_resolver.py` | 模型名→真实凭证+模型实例 |

### 关键设计

**模型能力声明**:

```python
@dataclass(frozen=True)
class ModelCapability:
    supports_tools: bool = False       # 是否支持函数调用
    supports_vision: bool = False      # 是否支持图片输入
    max_tokens: int = 0               # 上下文窗口大小
    cost_per_1k_input: float = 0.0    # 每1K输入token费用
    cost_per_1k_output: float = 0.0   # 每1K输出token费用
```

**租户白名单**:

即使模型能力匹配，如果不在租户的 allowlist 里，也不能使用:

```python
if tenant_allowlist is not None and model not in tenant_allowlist:
    raise ValueError(f"Model '{model}' is not allowed by tenant allowlist")
```

---

## 9. 第七层：工作空间沙箱层

### 它解决什么问题

Agent 可能需要执行代码、操作文件。如果直接在服务器上执行，一个恶意用户可以删除文件、窃取数据。

### 怎么解决的

**三种后端 + 生产安全守卫**:

| 后端 | 安全级别 | 用途 |
|------|---------|------|
| Local | ⚠ 无隔离 | 仅开发调试 |
| Docker | ✅ 容器隔离 | 生产默认 |
| E2B | ✅✅ 云沙箱 | 最高安全要求 |

### 代码位置

| 文件 | 职责 |
|------|------|
| `_runtime/_workspace.py` | WorkspaceConfig + Factory + 路径防护 |

### 关键设计

**生产环境 Local 阻断**:

```python
if production and effective == "local" and not allow_local_in_production:
    raise ValueError("Local workspace backend is not allowed in production")
```

**路径穿越防护**:

`tenant_id` 和 `session_id` 中的 `..` `/` 会被拒绝，防止 `../../etc/passwd` 攻击。

---

## 10. 第八层：可观测性层

### 它解决什么问题

生产环境出了问题，你需要知道:
- 谁在什么时候调用了什么
- 每次调用花了多少钱
- 哪个工具调用失败了
- Agent 的推理过程是什么

### 怎么解决的

**三重可观测性**:

| 系统 | 用途 | 代码文件 |
|------|------|---------|
| Prometheus | 实时指标(CPU/内存/QPS) | `_infra/_metrics.py` |
| OpenTelemetry | 分布式追踪 | `_server.py` _setup_otel() |
| Langfuse | LLM 专用追踪 | `_runtime/_langfuse.py` |

### Langfuse 关键设计

**优雅降级**:

```python
# Langfuse 未安装或未配置 → NoopExporter (空操作，不影响主流程)
if config.enabled and config.public_key and config.secret_key:
    try:
        from langfuse import Langfuse
        self._client = Langfuse(...)
        self._noop = False
    except ImportError:
        pass  # 降级为 Noop
```

**Payload 脱敏**:

发送到 Langfuse 的数据会先脱敏:

```python
def _redact_payload(payload):
    # 递归遍历 dict/list/str
    # 替换 sk-xxx → [REDACTED_API_KEY]
    # 替换 Bearer xxx → Bearer [REDACTED_TOKEN]
```

---

## 11. 核心数据流：一个请求的完整旅程

```
1. 用户发 HTTP POST 请求到 /v1/messages
   {
     "model": "claude-3-sonnet",
     "messages": [{"role": "user", "content": "查询退货政策"}],
     "max_tokens": 1024
   }
   Headers: {"x-api-key": "xrk-prod-abc123"}

2. AuthMiddleware 检查 x-api-key
   → ApiKeyStore.authenticate("xrk-prod-abc123")
   → 返回 AuthPrincipal(tenant_id="acme", user_id="alice", role="viewer")

3. RateLimitMiddleware 检查限流
   → RateLimiter.check("xrk-prod-abc123") → True (未超限)

4. AnthropicMessagesAdapter.parse_request(raw)
   → 生成 XRuntimeRequest(protocol=ANTHROPIC, prompt="查询退货政策", ...)

5. build_plan_from_request(request, tenant_tool_allowlist={"search_knowledge"})
   → 生成 RuntimeExecutionPlan(allowed_tools=["search_knowledge"], ...)

6. ChatService.run() 启动 Agent，中间件链依次执行:
   6a. RBAC: check_tool(session, "search_knowledge") → allow
   6b. Quota: 检查预算 → 未超限
   6c. Knowledge: 检索"退货政策" → 找到3条相关知识
       → 注入到 Agent 上下文: "以下是相关知识: ..."
   6d. Agent 调用 LLM 生成回复
   6e. Audit: 记录 "alice@acme 查询了退货政策"
   6f. Langfuse: 追踪本次调用 (token数、延迟)

7. AgentEvent 流通过 AnthropicMessagesAdapter.serialize_event_stream()
   → 转换为 SSE 格式字节流

8. HTTP 响应返回给客户端
   data: {"type": "content_block_delta", "delta": {"text": "我们的退货政策是..."}}
```

---

## 12. 配置系统

### 配置加载顺序

```
1. 默认值 (代码中的 BaseModel 默认值)
2. YAML 配置文件 (XRUNTIME_CONFIG_PATH 指定)
3. 环境变量 (XRUNTIME_* 前缀，覆盖 YAML)
```

### 主要配置段

| 配置段 | 说明 | 示例环境变量 |
|--------|------|------------|
| `server` | HTTP 服务 | `XRUNTIME_SERVER_PORT=8900` |
| `storage` | Redis 存储 | `XRUNTIME_STORAGE_REDIS_HOST=redis` |
| `message_bus` | 消息总线 | `XRUNTIME_MESSAGE_BUS_REDIS_DB=1` |
| `tenants` | 租户定义 | YAML 中配置 |
| `agents` | Agent 蓝图 | YAML 中配置 |
| `permission` | 权限配置 | `XRUNTIME_PERMISSION_DEFAULT_ROLE=viewer` |
| `observability` | 可观测性 | `XRUNTIME_OBSERVABILITY_OTEL_ENABLED=1` |
| `knowledge` | 知识库 | `XRUNTIME_KNOWLEDGE_ENABLED=1` |

### 代码位置

`_config.py` — 所有配置模型定义，从 `ServerConfig` 到 `XRuntimeConfig` 的完整树形结构。

---

## 13. 测试体系

### 测试分类

| 目录 | 用途 | 测试数 |
|------|------|--------|
| `tests/xruntime/` | 核心测试 | 655 |
| `tests/xruntime/unit/` | 单元测试 | (预留) |
| `tests/xruntime/contract/` | 协议契约测试 | (预留) |
| `tests/xruntime/integration/` | 集成测试 | 5 个文件 |
| `tests/xruntime/e2e/` | 端到端测试 | (需真实API) |

### 运行测试

```bash
# 全部测试
pytest tests/xruntime/ -q

# 单个测试文件
pytest tests/xruntime/test_rbac_policy.py -v

# 按关键词过滤
pytest tests/xruntime/ -k "test_jwt" -v
```

---

## 14. 文件索引

### 完整代码地图

```
src/xruntime/
├── __init__.py              ← 公共 API 导出
├── _version.py              ← 版本号
├── _config.py               ← 配置模型 (所有 *Config 类)
├── _server.py               ← 服务器入口 (build_xruntime_app)
├── _admin_api.py            ← Admin API 端点 (/admin/*)
│
├── _gateway/                ← 第1-2层: 协议网关 + 认证
│   ├── _adapter.py          ← ProtocolAdapter 抽象基类
│   ├── _anthropic_adapter.py   ← Anthropic 适配器
│   ├── _claude_code_adapter.py ← Claude Code 适配器
│   ├── _opencode_adapter.py    ← OpenCode 适配器
│   ├── _request.py          ← XRuntimeRequest 统一请求
│   ├── _plan.py             ← RuntimeExecutionPlan 执行计划
│   ├── _extension.py        ← create_xruntime_extension()
│   ├── _auth.py             ← AuthMiddleware
│   ├── _ratelimit.py        ← RateLimiter + RateLimitMiddleware
│   └── _mw_state.py         ← MiddlewareStateCache
│
├── _infra/                  ← 基础设施
│   ├── _tenant.py           ← Redis Key 前缀隔离
│   └── _metrics.py          ← Prometheus 指标收集
│
├── _runtime/                ← 第3-8层: 运行时核心
│   ├── _tenant/             ← 第3层: 多租户
│   │   ├── _policy.py       ← 角色+权限矩阵
│   │   └── _store.py        ← 成员/APIKey/JWT 存储
│   │
│   ├── _middleware/         ← 第4层: 中间件管道
│   │   ├── _rbac.py         ← RBAC 权限检查
│   │   ├── _quota.py        ← 预算配额
│   │   ├── _audit.py        ← 审计日志
│   │   ├── _redaction.py    ← 密钥脱敏
│   │   ├── _skill_injection.py ← 技能注入
│   │   ├── _loop_detection.py  ← 循环检测
│   │   ├── _llm_error_handling.py ← LLM 错误处理
│   │   └── _langfuse_tracer.py  ← Langfuse 追踪
│   │
│   ├── _knowledge/          ← 第5层: 知识库
│   │   ├── _base.py         ← 数据模型
│   │   ├── _adapter.py      ← 适配器基类
│   │   ├── _llm_wiki_adapter.py ← LLM-Wiki 实现
│   │   ├── _acl.py          ← KB ACL
│   │   ├── _middleware.py   ← 知识注入中间件
│   │   ├── _tools.py        ← 搜索/写入工具
│   │   └── _registry.py     ← 多后端注册
│   │
│   ├── _memory/             ← Agent 记忆系统
│   │   ├── _models.py       ← 记忆数据模型
│   │   ├── _store.py        ← 记忆存储抽象
│   │   ├── _redis_store.py  ← Redis 实现
│   │   ├── _extractor.py    ← 记忆提取器
│   │   ├── _hybrid_retriever.py ← 混合检索
│   │   ├── _embedding_providers.py ← 嵌入提供者
│   │   └── _middleware.py   ← 记忆注入中间件
│   │
│   ├── _skills/             ← 技能系统
│   │   ├── _registry.py     ← 技能注册表
│   │   ├── _manifest.py     ← 技能清单解析
│   │   └── _load_skill_tool.py ← 加载技能工具
│   │
│   ├── _subagents/          ← 子 Agent 系统
│   │   ├── _models.py       ← 子Agent模型
│   │   ├── _executor.py     ← 执行器
│   │   └── _task_tool.py    ← 任务分配工具
│   │
│   ├── _workspace.py        ← 第7层: 工作空间沙箱
│   ├── _model_governance.py ← 第6层: 模型治理
│   ├── _model_router.py     ← 模型路由
│   ├── _model_resolver.py   ← 模型解析器
│   ├── _langfuse.py         ← 第8层: Langfuse 导出器
│   ├── _orchestrator.py     ← Agent 编排器
│   ├── _migrator.py         ← 数据迁移
│   ├── _plugin.py           ← 插件系统
│   └── _llm_test_config.py  ← 测试用 LLM 配置
```

---

> 本文档基于 XRuntime v1.0 代码编写。如有疑问，请参考 `docs/xruntime/` 下的其他文档或源代码注释。
| `_gateway/_adapter.py` | 抽象基类 `ProtocolAdapter`，定义接口 |
| `_gateway/_anthropic_adapter.py` | Anthropic Messages API 适配器 |
| `_gateway/_claude_code_adapter.py` | Claude Code SDK 适配器 |
| `_gateway/_opencode_adapter.py` | OpenCode 协议适配器 |
| `_gateway/_request.py` | 统一请求模型 `XRuntimeRequest` |
| `_gateway/_plan.py` | 执行计划 `RuntimeExecutionPlan` |
| `_gateway/_extension.py` | 扩展入口 `create_xruntime_extension()` |

### 关键设计

**统一请求模型** (`_request.py`) 把三种协议的差异归一到一组字段：`protocol`、`prompt`、`session_id`、`user_id`、`tenant_id`、`allowed_tools`、`max_turns`、`metadata`。

**执行计划** (`_plan.py`) 进一步把协议特有的配置（如 Claude Code 的 `sandbox`）映射到标准字段 `workspace_policy`、`knowledge_scope`、`max_budget_usd`。

**权限收紧逻辑**（核心安全设计）：

```python
# 客户端请求的工具 ∩ 租户允许的工具 = 最终允许的工具
if tenant_tool_allowlist is not None:
    allowed = [t for t in raw_allowed if t in tenant_tool_allowlist]
```

即使用户在请求中写了 `allowed_tools: ["delete_database"]`，如果租户没配置这个工具权限，它会被过滤掉。**权限只能收紧，永远不能放宽。**

---

## 4. 第二层：认证与安全层

### 它解决什么问题

没有认证的话，任何人都可以调用你的 AI 助手，消耗你的 API 额度，查看你的知识库。

### 怎么解决的

**双因素认证**：同时支持 API Key 和 JWT。

```
请求进来
  ↓
检查 Authorization: Bearer xxx → JWT 验证
  ↓ (如果没有 JWT)
检查 x-api-key: xxx → API Key 验证
  ↓ (如果都没有)
返回 401 Unauthorized
```

### 代码位置

| 文件 | 职责 |
|------|------|
| `_gateway/_auth.py` | `AuthMiddleware` — HTTP 认证中间件 |
| `_gateway/_ratelimit.py` | `RateLimiter` + `RateLimitMiddleware` — 限流 |
| `_runtime/_tenant/_store.py` | `ApiKeyStore` / `JwtClaimsParser` |

### 关键安全设计

**Fail-Closed（默认拒绝）**：如果认证中间件已挂载但没有配置任何 API Key，拒绝所有非公开路由的请求。

**JWT 安全**（代码审查修复）：无 secret 时直接拒绝所有 token（防 `alg=none` 攻击）；只接受 HS256；用 `hmac.compare_digest` 防时序攻击。

**滑动窗口限流**：记录每个客户端的请求时间戳，时间窗口内超过上限就返回 429。

**内存泄漏防护**（代码审查修复）：当追踪的客户端超过 10000 个时，主动清理所有过期记录。

---

## 5. 第三层：多租户与 RBAC 层

### 它解决什么问题

公司有多个部门（租户），每个部门有不同的权限等级。

### 四角色 × 十六权限矩阵

```
                 Viewer  Contributor  Admin   Owner
查询知识库        ✅       ✅          ✅      ✅
写入文档          ❌       ✅          ✅      ✅
创建知识库        ❌       ❌          ✅      ✅
管理成员          ❌       ❌          ✅      ✅
删除租户          ❌       ❌          ❌      ✅
```

### 代码位置

| 文件 | 职责 |
|------|------|
| `_runtime/_tenant/_policy.py` | 角色定义 + 权限矩阵 + 策略检查 |
| `_runtime/_tenant/_store.py` | 成员存储 + API Key 绑定 |
| `_infra/_tenant.py` | Redis Key 前缀隔离 |

### 关键设计

**默认拒绝**：没有匹配到任何 allow 规则的工具调用 = deny。

**Redis Key 前缀隔离**：每个租户的数据在 Redis 中有不同的前缀 `tenant:{tid}:`。

**Header 欺骗防护**：认证后的 `principal.tenant_id` 覆盖 Header 中的 `x-tenant-id`。

---

## 6. 第四层：中间件管道层

### 它解决什么问题

一次 AI 对话需要经过多个处理步骤：权限检查 → 预算检查 → 审计记录 → 密钥脱敏 → 知识注入 → 实际执行。如果全写在一个函数里，代码不可维护。

### 中间件管道模式

每个关注点是一个独立中间件，按顺序执行，任何中间件都可以中断链：

```
用户请求 → [RBAC] → [Quota] → [Audit] → [Redaction] → [Knowledge] → Agent 执行
            ↓         ↓         ↓           ↓             ↓
          拒绝？    超预算？   记录日志    脱敏密钥      注入知识
```

### 9 个中间件

| 中间件 | 文件 | 职责 |
|--------|------|------|
| RBAC | `_middleware/_rbac.py` | 工具调用前检查角色权限 |
| Quota | `_middleware/_quota.py` | 追踪 Token/费用/工具调用次数 |
| Audit | `_middleware/_audit.py` | 记录所有操作到审计日志 |
| SecretRedaction | `_middleware/_redaction.py` | 工具输入输出中脱敏 API Key |
| Knowledge | `_knowledge/_middleware.py` | 自动注入知识库内容 |
| SkillInjection | `_middleware/_skill_injection.py` | 注入可用技能列表 |
| Memory | `_memory/_middleware.py` | 注入历史记忆 |
| LoopDetection | `_middleware/_loop_detection.py` | 检测 Agent 重复循环 |
| Langfuse | `_middleware/_langfuse_tracer.py` | 发送追踪到 Langfuse |

### 关键设计

**中间件状态缓存** (`_mw_state.py`)：配额追踪器、审计日志器等需要跨轮次共享状态。`MiddlewareStateCache` 缓存这些对象，使第一轮创建的状态在后续轮次中复用。

---

## 7. 第五层：知识库层

### 它解决什么问题

AI 模型只知道训练数据里的知识，不知道你公司的内部文档。知识库让 AI 能查询你的私有数据来回答问题。

### LLM-Wiki 编译器模式（AOT 预编译）

```
原始文档 → [分块] → [LLM提取] → [生成Wiki页面] → [建立索引] → 存储
                                                          ↓
用户提问 ──────────────────────────────────────────→ [BM25检索] → 相关Wiki页面
```

### 三层存储模型

```
data/
├── raw/                    ← 第1层: 原始文档 (JSON文件)
│   └── tenants/
│       └── acme/
│           └── kbs/
│               └── product-docs/
│                   └── raw/
│                       └── source-001.json
├── wiki/                   ← 第2层: 编译后的Wiki页面 (Markdown)
│   └── tenants/acme/kbs/product-docs/
│       └── wiki/
│           └── acme__product-docs__source-001__0.md
└── index/                  ← 第3层: BM25索引 (manifest.json)
    └── tenants/acme/kbs/product-docs/
        └── index/
            └── manifest.json
```

### 代码位置

| 文件 | 职责 |
|------|------|
| `_knowledge/_base.py` | 数据模型: `KnowledgeQuery`, `KnowledgeResult`, `KnowledgeChunk` |
| `_knowledge/_adapter.py` | `KnowledgeAdapter` 抽象基类 + `KnowledgeBaseConfig` |
| `_knowledge/_llm_wiki_adapter.py` | LLM-Wiki 编译器实现（BM25 检索、租户隔离、审计、脱敏） |
| `_knowledge/_registry.py` | `KnowledgeRegistry` — 多后端管理 + `_chunk_in_scope` 租户过滤 |
| `_knowledge/_acl.py` | `KnowledgeAclStore` — per-KB 访问控制 |
| `_knowledge/_middleware.py` | `KnowledgeMiddleware` — 自动注入知识到 Agent 上下文 |
| `_knowledge/_tools.py` | Agent 可调用的 `SearchKnowledgeTool` / `IngestKnowledgeTool` |

### 关键设计

**BM25 检索算法**：替代简单关键词匹配，基于词频和文档长度的相关性排序，参数 `k1=1.5`、`b=0.75`。

**租户物理隔离**：不同租户/KB 的文档写入不同物理目录，通过 `_validate_path_component` 防止路径穿越。

**Ingest 前 Secret 脱敏**：文档写入前自动替换 `sk-xxx` API Key 和 `Bearer xxx` Token。

**操作审计**：每次 ingest/compile/retrieve 操作写入 `knowledge-audit.jsonl`。

---

## 8. 第六层：模型治理层

### 它解决什么问题

不同模型的能力和成本差异巨大：GPT-4 支持工具调用但贵，Haiku 便宜但不支持视觉。需要根据任务需求自动选择合适的模型。

### 三层架构

```
1. ModelCapabilityRegistry  — 注册每个模型的能力声明
2. ModelRouter.select()     — 根据任务需求 + 租户白名单筛选模型
3. MultiModelRouter         — 基于任务复杂度的智能路由
```

### 代码位置

| 文件 | 职责 |
|------|------|
| `_runtime/_model_governance.py` | `ModelCapability` + `ModelCapabilityRegistry` + `ModelRouter` |
| `_runtime/_model_router.py` | `MultiModelRouter` — 复杂度分级路由 |
| `_runtime/_model_resolver.py` | `ModelResolver` — 蓝图模型名 → 真实 Credential+Model |

### 关键设计

**能力匹配**：`requires_tools=True` 时跳过不支持工具调用的模型。

**租户白名单**：不在租户允许列表中的模型被拒绝，fallback 模型也受白名单约束。

---

## 9. 第七层：工作空间沙箱层

### 它解决什么问题

Agent 可能需要执行代码、读写文件。如果直接在宿主机上执行，恶意代码可能删除文件或窃取数据。

### 四层安全防护

```
1. Backend 选择矩阵 — 生产模式默认 Docker，local 需显式 override
2. Path Traversal Guard — 拒绝 tenant_id/session_id 中的 .. 和 /
3. Tenant/Session Scoped Directory — 每个会话有独立工作目录
4. 安全默认值 — 生产 + 无配置 = 拒绝所有执行
```

### 代码位置

| 文件 | 职责 |
|------|------|
| `_runtime/_workspace.py` | `WorkspaceConfig` + `WorkspaceManagerFactory` |

### 关键设计

**生产环境 local 阻断**：

```python
if production and effective == "local" and not allow_local_in_production:
    raise ValueError("Local workspace backend is not allowed in production")
```

**路径穿越防护**：

```python
if ".." in value or "/" in value or os.sep in value:
    raise ValueError(f"Path traversal detected in {label}: {value}")
```

---

## 10