# Vercel Eve / Agent Stack 深度调研报告

> 生成日期：2026-06-30 | 来源：30+ 官方文档页面、GitHub 仓库、社区分析文章 | 置信度：高

---

## 一、执行摘要

**Vercel Eve** 是 Vercel 于 2026 年 6 月 16 日开源的 **TypeScript Agent 框架**，定位为"像 Next.js 之于 Web 应用一样，给 Agent 提供一套约定明确、可直接进入生产的框架"。核心理念是 **"文件系统即接口" (Filesystem-First)**——Agent 是磁盘上的一个目录，目录即契约。Vercel 内部已在生产环境跑了 100+ 个 Agent（数据分析、销售、客服），Eve 是把这些共用基础设施提炼出来的产物。

**与本项目 (xin-agent-runtime) 的根本差异**：Eve 走 **"TypeScript + 文件系统约定 + Vercel 全家桶"** 路线，强调开发者体验和工程化；本项目走 **"Python + 企业扩展 + 多租户隔离"** 路线，强调企业级安全和租户隔离。两者解决的问题域有交集（Agent 构建、工具、沙箱、模型接入）但侧重不同——Eve 是"让 Agent 开发像搭积木"，本项目是"让 Agent 在企业多租户环境安全运行"。

---

## 二、Eve 的核心设计哲学

### 2.1 文件系统即接口（Filesystem-First）

受 Next.js App Router 启发，**文件位置决定其作用**：

```
my-agent/
└── agent/
    ├── agent.ts              # 模型与运行时配置（defineAgent）
    ├── instructions.md       # 系统提示词（必需）
    ├── tools/                # 文件名 = 工具名
    │   └── get_weather.ts
    ├── skills/               # 按需加载的技能（Markdown）
    ├── subagents/            # 子 Agent（完全隔离）
    ├── channels/             # Slack/Discord/HTTP 入口
    ├── schedules/            # Cron 定时任务
    ├── connections/          # MCP / OpenAPI 外部服务
    ├── hooks/                # 生命周期事件订阅
    └── sandbox/              # 隔离的 bash 工作区
```

- **添加文件 = 添加能力**；删除 = 移除；重命名 = 重命名工具
- Agent 定义可进 Git、走 PR 审查、看 diff、建 Preview 环境
- 每个目录有对应的 `define*` helper（`defineTool`、`defineSkill`、`defineChannel` 等）

### 2.2 Durable by Default（持久化优先）

基于开源 [Workflow SDK](https://workflow-sdk.dev/) 实现：
- 每个 step 都有 **checkpoint**，可暂停/恢复
- 崩溃或重新部署后**从断点继续**，已完成操作不重复执行
- 支持 `sleep("7 days")` 不消耗资源
- 三层工作模型：**session**（持久化对话）→ **turn**（一条用户消息）→ **step**（durable checkpoint）

### 2.3 Trust Boundary（信任边界）

严格分离两个运行时上下文：

| | App Runtime（受信侧） | Sandbox（隔离侧） |
|---|---|---|
| `process.env` / secrets | ✅ | ❌ |
| Node.js 代码 | ✅ | ❌ |
| 网络 | 不受限 | 受 policy 控制 |
| 文件系统 | App 自己的 | 隔离的 `/workspace` |

**凭证永不进入 Sandbox**——Credential Brokering 在网络防火墙按域名注入 auth headers，sandbox 进程只见响应。

---

## 三、Eve 的三层内部架构

```
Channel（入站传输） → Harness（AI 工作单元） → Runtime（状态持久化）
```

| 层 | 职责 | 拥有的句柄 |
|----|------|-----------|
| **Channel** | 归一化入站传输、应用 auth 与 delivery policy | `continuationToken`（caller 用于启动下一轮） |
| **Harness** | 执行一个 AI 工作单元，返回 `{ session, next }` | — |
| **Runtime** | 持久化状态、推送事件流、提供 workflow 原语 | `sessionId`（用于流式与检视） |

这种分层是为什么公共 HTTP 协议显式分离两个标识符：`continuationToken`（channel 拥有）与 `sessionId`（runtime 拥有）。

### 消息流转流程（统一）

无论消息来自 Web、终端、Slack，流程相同：
1. 平台输入 → Eve 转为 message
2. 给模型 instructions + skills + tools + 历史
3. 运行工作（调用 tools、subagents）
4. 保存 session + 流式事件
5. 按平台期望格式回送结果

---

## 四、Eve 的基础组件全景

### 4.1 模型提供商

- **默认路由**：通过 Vercel AI Gateway（统一 OpenAI 兼容端点，自动 failover、缓存、限流平滑、成本追踪）
- **Model id 字符串**：`"anthropic/claude-sonnet-4.6"`、`"openai/gpt-5.3"`
- **直接 provider 调用**：传入 provider-authored `LanguageModel`
- 基于 **AI SDK 7**，支持 anthropic、openai、google、xAI 等

### 4.2 工具系统（Tools）

```typescript
import { defineTool } from "eve/tools";
import { z } from "zod";

export default defineTool({
  description: "退款一笔订单。",
  inputSchema: z.object({
    chargeId: z.string(),
    amount: z.number().positive(),
  }),
  outputSchema: z.object({
    success: z.boolean(),
    refundId: z.string(),
  }),
  needsApproval: always(),  // 人类审批门控
  async execute({ chargeId, amount }) {
    /* ... */
  },
  toModelOutput(output) {
    return {
      type: "text",
      value: `退款成功！单号：${output.refundId}`,
    };
  },
});
```

- **文件名即工具名**，无需注册表
- **`toModelOutput`**：将复杂返回精简后只暴露必要信息给模型
- **`needsApproval`**：`always()` / `once()` / `never()` / predicate，可暂停等待人工确认
- **内置工具**：`bash`、`read_file` / `write_file`、`glob`、`grep`、`web_fetch`、`web_search`、`todo`、`ask_question`（HITL 问用户）、`agent`（子任务委派）、`load_skill`
- **MCP 支持**：`defineMcpClientConnection` 自动发现远程工具，模型看不到 URL 和凭据

### 4.3 沙箱（四后端适配器）

| 后端 | 适用场景 | 隔离级别 |
|---|---|---|
| **Vercel Sandbox** | 部署到 Vercel 时自动使用 | Firecracker microVM，硬件级 |
| **Docker** | 本地开发 | 容器级 |
| **microsandbox** | 轻量级 VM | macOS Apple Silicon / Linux KVM |
| **just-bash** | 零依赖兜底 | 纯 JS bash 解释器，无真实 binaries |

每个 Agent 恰好一个 sandbox，文件系统根 `/workspace`，支持 seed 文件注入、network policy、lifecycle hooks（`bootstrap` 模板级、`onSession` 会话级）。

**Network Policy**：`"allow-all"`（默认）/ `"deny-all"` / `{ allow: [...], subnets: { deny: [...] } }`。Vercel 与 microsandbox 支持域名级 allow-list + 凭证 brokering；Docker 只支持 all-or-none。

**Credential Brokering**：secrets 永不进入 sandbox；网络防火墙按域名注入 auth headers（secret 留 app runtime，sandbox 仅见响应）。

### 4.4 状态管理（defineState）

```typescript
import { defineState } from "eve/context";

export const budget = defineState(
  "my-agent.budget",
  () => ({ count: 0, cap: 25 }),
);

// budget.get() / budget.update(fn)
```

- 值跨 step boundary 持久化，outlast crash / redeploy / days-long session
- 必须在 framework-managed runtime 上下文中调用（tool / hook）
- 不与 subagent 共享（每个 subagent 启动 fresh state）
- 短期会话记忆；要跨会话/用户共享 → 外部 store（connection 或自建 DB）

### 4.5 Channels（消息通道）

内置支持：HTTP（默认，无需 authoring）、Slack、Discord、Teams、Telegram、Twilio（SMS/语音）、GitHub（@mentions + PR review + checkout）、Linear。

- 工具不需要知道消息来源——同一份工具不管从 Slack 还是 HTTP 来，行为一致
- 通道之间可 handoff（如 incident webhook 开 Slack 调查 thread）
- `defineChannel` 自定义：声明 `GET` / `POST` / `PUT` / `PATCH` / `DELETE` / `WS` + `events` + `send`
- 文件 stem = channel id；local subagents 不声明 channels

### 4.6 Connections（外部服务）

- **MCP connections**：`defineMcpClientConnection`，模型通过 `connection_search` 发现、按 `<connection>__<tool>` 调用（如 `linear__list_issues`）
- **OpenAPI connections**：`defineOpenAPIConnection`，将 OpenAPI 3.x 的每个 operation 转为 connection tool
- **Auth 模式**：
  - **Static-token**：`getToken` 返回 `{ token, expiresAt? }`
  - **App vs User**：`principalType: "app"`（共享 credential）vs `"user"`（每用户 token）
  - **Vercel Connect**：`connect("linear/myagent")` 处理 OAuth consent / encrypted storage / refresh
  - **Self-hosted OAuth**：`defineInteractiveAuthorization`
- **Per-caller**：`auth` / `headers` 可为函数，接收 `ctx`，按 tenant / user 动态解析
- **Connection token 永不序列化到 durable state**，per-step 缓存

### 4.7 Schedules（定时任务）

```typescript
import { defineSchedule } from "eve/schedules";

export default defineSchedule({
  cron: "*/5 * * * *",
  markdown: "Pull open Linear issues and POST a summary.",
});
```

- 在 Vercel 上自动变为 Vercel Cron Job
- 支持 handler-form：`async run({ receive, waitUntil, appAuth })` 可投递到 Slack 等 channel
- UTC 评估

### 4.8 Hooks（生命周期事件订阅）

```typescript
import { defineHook } from "eve/hooks";

export default defineHook({
  events: {
    async "session.started"(_event, ctx) { /* ... */ },
    async "message.completed"(event) { /* ... */ },
    "*"(event) { /* 所有事件 */ },
  },
});
```

- 用途：audit logging、metrics、alerting、persisting 到自有 DB
- 执行顺序：Emit → Hooks（typed 先于 `*`）→ Dynamic tool resolvers
- 事件类型：`session.started`、`turn.completed`、`message.completed`、`action.result` 等

### 4.9 Evals（评测框架）

```typescript
export default defineEval({
  description: "分析师按团队规则回答营收问题。",
  async test(t) {
    await t.send("What was revenue last week?");
    t.calledTool("run_sql");
    t.check(t.reply, includes("net of refunds"));
  },
});
```

- `eve eval` 本地或指向部署的 app 运行
- 支持 Braintrust / JUnit reporter
- 可接入 CI

### 4.10 子 Agent 系统

两种模式：

| 能力 | 内置 `agent` 工具 | 声明式子 Agent (`subagents/xxx/`) |
|---|---|---|
| 指令 | 继承父 Agent | 自己的 instructions |
| 工具 | 继承 | 自己的 `tools/` |
| 沙箱 | 共享 | 自己的 sandbox |
| 技能 | 继承 | 自己的 `skills/` |
| 状态 | 全新 | 全新 |

声明式子 Agent **完全不继承**父 Agent 任何东西，避免权限过大。可通过 `lib/` 共享代码。还支持 `defineRemoteAgent` 将远程 Eve 部署作 subagent。

### 4.11 前端集成

`useEveAgent` hook 支持 React 19 / Vue 3.5 / Svelte 5，以及 Next.js 16 / Nuxt 4 / SvelteKit 2 / Vite 8 框架适配。

---

## 五、Vercel Agent Stack（"Agentic Infrastructure"）

Vercel 在 2026 年 4 月正式定义 **Agent Stack 四原语**：

1. **AI Gateway**：模型路由器，单一 OpenAI 兼容端点，failover / caching / 限流平滑 / per-request 成本追踪
2. **Sandbox**：Firecracker microVM 代码执行环境，60s–30min 生命周期，文件系统 + 网络 egress 控制
3. **Flags**：feature flag 服务，作为 Agent 运行时控制平面（toggle 模型/工具/prompt，边缘评估，个位数 ms 延迟）
4. **Microfrontends**：从独立部署的 app 组合 UI，让 Agent 渲染生成的 UI fragment 而不接管整个页面

加上 **Workflow SDK**（durable execution）、**Vercel Connect**（OAuth）、**Vercel Agent**（PR code review，$0.30/次 + token 成本）共同构成完整 stack。

### 部署方式

Eve **本地、Vercel、长运行 Node 主机行为一致**：

| 路径 | 命令 | 说明 |
|---|---|---|
| **Vercel（首选）** | `vercel deploy` | `eve build` 写 Vercel Build Output bundle；Workflow SDK 跑在 Vercel Workflow；`defaultBackend()` 选 Vercel Sandbox；Schedule 自动变 Vercel Cron Job |
| **自托管（Node 主机）** | `eve build && eve start` | Nitro Node 输出；Workflow SDK 用本地 world（默认 `.workflow-data`）；`defaultBackend()` 按 Docker→microsandbox→just-bash 择优 |
| **容器/进程管理器** | 适配 generated output | 注意：仅服务 HTTP 而不启动 Nitro schedule runner 的主机，schedule 不会自动触发 |

### 技术栈

- **语言**：TypeScript 97%（运行时要求 Node ≥ 24）
- **包管理**：pnpm 11.7 + Turborepo
- **打包**：rolldown + TypeScript 7 tsc
- **Lint/Format**：oxlint + oxfmt（非 ESLint/Prettier）
- **运行时**：Nitro 3.0 + Workflow SDK 5.0-beta
- **AI 抽象**：AI SDK 7
- **沙箱**：`@vercel/sandbox`、`just-bash`、`microsandbox`、Docker
- **测试**：Vitest（五套配置）+ autoevals
- **可观测性**：OpenTelemetry
- **认证**：`@vercel/oidc` + `jose`（JWT/JWK）
- **License**：Apache-2.0

---

## 六、Eve 的安全模型

### 6.1 Auth Fails Closed

- Routes 默认拒绝未认证流量，无 `AuthFn` 接受 → `401`
- 接受匿名需显式 `none()`
- `placeholderAuth()` 让半配置 app 在生产环境保持关闭

### 6.2 Channel 验证

- 平台 channels 用 **constant-time 比较** HMAC 签名
- **不信任 body-supplied identity**：caller 必须从 verified signature/token 派生，绝不从 body 字段

### 6.3 Authored Markdown 是 Data

- Skill / Schedule 的 YAML frontmatter **严格当数据**，禁用 `---js` / `---javascript` eval fence

### 6.4 凭证永不入 Sandbox

- Secrets 留 `process.env`；特权调用走 tools / connections
- Credential brokering 在网络防火墙按域名注入

### 6.5 连接凭证

- `getToken()` 或 OAuth 解析；token per-step 缓存，**永不序列化到 durable state**
- 最小权限 scope
- 401 处理：`ctx.requireAuth(provider)` 驱逐缓存 token 重启 consent flow

### 6.6 审批门控（HITL）

- `always()` / `once()` / `never()` / predicate
- 暂停期间不消耗计算资源，可等几分钟到几天
- Built-in approval 是"有 session 访问权的人批准"，**不是四眼原则**

### 6.7 多租户支持

Eve 提供 **multi-tenant 模式**（patterns 文档，非内置框架特性）：

- `tenantId` 通常放 `ctx.session.auth.current.attributes.tenantId`
- 用 `principalType: "user"` + per-caller `getToken` 实现 per-tenant connection credential
- `approval` 字段是 **async policy hook**：接收 `session`、`toolName`、`toolInput`、`approvedTools`
- **每次调用都评估**（不把 `approvedTools` 当 session-wide grant）
- Tenant policy 存储归开发者（PostgreSQL / policy service / KV）
- 应用层需校验 session ownership

### 6.8 已知安全限制

- **无内置 RBAC**
- **无内置 quota**
- **无内置 audit log**
- **无内置 tenant 隔离**
- 默认偏宽松（工具执行无 approval、沙箱网络出站非 deny-all）
- README 显式警告"不要仅依赖模型行为来阻止敏感/不可逆动作"

### 6.9 生产前 Checklist

1. 替换 `placeholderAuth()` 为 `vercelOidc()` / `httpBasic()` / `oidc()` / 自建
2. 校验 channel signatures（每个平台设签名 secret）
3. Secrets 仅留 `process.env`
4. Connection token 最小权限
5. Sandbox network policy 收紧（非 `allow-all`），authenticated egress 走 brokering
6. 不可信文本作为 markup 时按 surface 转义

---

## 七、能力对比：Eve vs xin-agent-runtime

### 7.1 总览对比表

| 维度 | Vercel Eve | xin-agent-runtime (AgentScope + Xruntime) |
|---|---|---|
| **定位** | Agent 开发框架（开发者体验优先） | 企业级 Agent 运行时平台（安全优先） |
| **语言** | TypeScript (Node 24+) | Python (3.11+) |
| **Agent 定义** | 文件系统约定（目录即契约） | YAML 配置 + Python 代码 |
| **底层框架** | AI SDK 7 + Workflow SDK + Nitro | AgentScope + FastAPI |
| **持久化** | Workflow SDK（durable checkpoint + replay） | MiddlewareStateCache + AS SessionRecord |
| **多租户** | Pattern 文档指导，开发者自实现 | **内置**（contextvars + Redis 前缀隔离） |
| **RBAC** | 无内置 | **内置** RbacMiddleware |
| **Quota** | 无内置 | **内置** QuotaMiddleware |
| **Audit Log** | Hooks 自行实现 | **内置** AuditMiddleware |
| **Secret Redaction** | 无内置 | **内置** SecretRedactionMiddleware |
| **Fail Closed** | Auth Fails Closed（路由层） | **TenantIsolationError**（存储 + 消息总线层） |
| **沙箱** | 4 后端（Vercel/Docker/microsandbox/just-bash） | 3 后端（Local/Docker/E2B） |
| **模型支持** | anthropic, openai, google, xAI（AI SDK 生态） | anthropic, openai, dashscope, deepseek, moonshot, ollama, gemini, xai |
| **协议适配** | 自有 HTTP 协议 + Channels | Anthropic Messages API, Claude Code SDK, OpenCode |
| **工具系统** | defineTool + Zod + 文件名注册 | AS ToolBase + permission engine |
| **子 Agent** | 声明式，完全隔离（不继承父 Agent） | AS Agent 嵌套 |
| **审批门控** | **原生** needsApproval（always/once/never/predicate） | 可在 RbacMiddleware 中实现 |
| **定时任务** | **原生** defineSchedule（Cron） | 无内置 |
| **Channels** | **内置** Slack/Discord/Teams/Telegram/Twilio/GitHub/Linear | 协议适配器（Anthropic/Claude Code/OpenCode） |
| **知识库** | 无内置 | **内置** KnowledgeBaseBase + LlmWikiAdapter |
| **Evals** | **内置** defineEval + CI 集成 | 依赖 pytest |
| **Tracing** | **OTel 原生** | MetricsCollector |
| **部署** | `vercel deploy` 一键 + 自托管 | Docker + docker-compose |
| **成熟度** | Beta（2026.6 开源） | 实现完成，有完整测试套件 |
| **开源协议** | Apache-2.0 | Apache-2.0 |

### 7.2 架构对比

```
┌─────────────────────────────────────────────────────────────┐
│                      Vercel Eve 架构                         │
├─────────────────────────────────────────────────────────────┤
│  Channels (Slack/Discord/HTTP/...)                          │
│    ↓ continuationToken                                      │
│  Harness (AI 工作单元)                                       │
│    ↓                                                         │
│  Runtime (Workflow SDK: durable checkpoint + replay)        │
│    ↓                                                         │
│  Sandbox (Vercel/Docker/microsandbox/just-bash)             │
│  AI Gateway (模型路由)                                       │
│  Connections (MCP/OpenAPI)                                  │
│                                                              │
│  安全 = 部署者负责（approval policy / route auth / sandbox）  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   xin-agent-runtime 架构                     │
├─────────────────────────────────────────────────────────────┤
│  Protocol Adapters (Anthropic/Claude Code/OpenCode)         │
│    ↓ XRuntimeRequest                                        │
│  Gateway (AuthMiddleware + RateLimiter)                     │
│    ↓                                                         │
│  Enterprise Middlewares:                                     │
│    AuditMiddleware → QuotaMiddleware → RbacMiddleware       │
│    → SecretRedactionMiddleware                              │
│    ↓                                                         │
│  AS ChatService.run() (Agent 执行)                          │
│    ↓                                                         │
│  TenantAwareRedisStorage (tenant:{tid}: 前缀)               │
│  TenantAwareMessageBus (tenant:{tid}: 前缀)                 │
│  WorkspaceManager (Local/Docker/E2B)                        │
│  KnowledgeBase (LlmWikiAdapter)                             │
│  ModelResolver (8 providers)                                │
│                                                              │
│  安全 = 内置（RBAC/Quota/Audit/SecretRedaction/Fail Closed） │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 各自的独特优势

#### Eve 的独特优势（本项目没有的）

1. **文件系统即接口**——Agent 定义可直接进 Git/PR 审查，工程化程度极高
2. **Durable Execution**——Workflow SDK 的 checkpoint + replay 机制比 MiddlewareStateCache 更完整
3. **原生人类审批门控**——`needsApproval` 内置，暂停不消耗资源
4. **内置 Channels**——Slack/Discord/Teams/Telegram/Twilio/GitHub/Linear 开箱即用
5. **原生定时任务**——`defineSchedule` + Cron
6. **原生 Evals**——`defineEval` + CI 集成
7. **OTel 原生 Tracing**——标准化可观测性
8. **子 Agent 完全隔离**——不继承父 Agent 任何东西
9. **Progressive Disclosure**——Skills 只暴露描述给模型，需要时才拉取完整内容
10. **Vercel 一键部署**——`vercel deploy` 即可上线

#### xin-agent-runtime 的独特优势（Eve 没有的）

1. **内置多租户隔离**——`contextvars.ContextVar` + Redis `tenant:{tid}:` 前缀，生产级隔离
2. **Fail Closed 机制**——存储和消息总线层在租户上下文缺失时直接抛 `TenantIsolationError`
3. **内置 RBAC**——RbacMiddleware，admin/viewer 角色
4. **内置 Quota**——QuotaMiddleware，per-session 配额追踪
5. **内置 Audit Log**——AuditMiddleware，per-tenant 审计日志
6. **内置 Secret Redaction**——SecretRedactionMiddleware
7. **知识库框架**——KnowledgeBaseBase + LlmWikiAdapter（compiler pattern AOT 检索）
8. **8 个模型提供商**——比 Eve 多出 dashscope、deepseek、moonshot、ollama（中国生态友好）
9. **Python 生态**——适合数据科学/AI 团队
10. **协议适配器**——直接兼容 Anthropic Messages API、Claude Code SDK、OpenCode 三个 wire format

---

## 八、对本项目的启发建议

### 8.1 值得借鉴的设计

| Eve 的设计 | 对本项目的启发 | 实现难度 |
|---|---|---|
| **Durable Execution**（checkpoint + replay） | 当前 `MiddlewareStateCache` 只跨 turn 持久化，可考虑引入 step 级 checkpoint，支持崩溃恢复 | 高 |
| **原生审批门控** | 可在 `RbacMiddleware` 或新 `ApprovalMiddleware` 中实现 `needsApproval` 语义 | 中 |
| **OTel Tracing** | 当前 `MetricsCollector` 可统一到 OTel 标准，接入 Jaeger/Honeycomb | 中 |
| **Evals 框架** | 当前依赖 pytest，可补 Agent 行为评测层（`defineEval` 等价物） | 中 |
| **Progressive Disclosure（Skills）** | 知识库的 `KnowledgeMiddleware` 可借鉴，只注入摘要、按需拉取全文 | 低 |
| **文件系统约定** | Agent blueprint 可考虑支持目录约定（instructions.md / tools/ 等），降低配置负担 | 中 |
| **Channels** | 协议适配器目前只接 3 个 wire format，可扩展 Slack/Discord 等 channel | 中 |
| **定时任务** | 可考虑在 Xruntime 中加 `ScheduleManager` | 低 |

### 8.2 本项目的护城河

Eve 明确把多租户/RBAC/quota/audit 留给开发者——这正是本项目的核心价值：

1. **多租户隔离是护城河**：Eve 没有内置，开发者要自己实现 session ownership 校验、tenant-scoped 存储、per-tenant credential
2. **Fail Closed 是企业刚需**：Eve 的 Auth Fails Closed 只在路由层，本项目在存储 + 消息总线层，更深更安全
3. **4 个企业中间件**（Audit / Quota / RBAC / SecretRedaction）是 Eve 完全没有的
4. **知识库框架**是 Eve 没有的差异化能力
5. **中国模型生态**（dashscope / deepseek / moonshot / ollama）是 Eve 不覆盖的

### 8.3 差异化定位建议

```
Eve 定位：       "让 Agent 开发像搭积木一样简单"（开发者体验）
本项目定位：     "让 Agent 在企业多租户环境安全运行"（企业安全）

→ 不直接竞争，可互补：
   - 用 Eve 的设计理念改进本项目的 Agent 定义体验
   - 用本项目的企业中间件补齐 Eve 缺失的安全层
   - 甚至可以考虑为 Eve 写一个 Xruntime adapter
```

---

## 九、Eve 的已知问题（Beta 风险）

来自 GitHub Issues 的实际踩坑：

1. [#432](https://github.com/vercel/eve/issues/432) Sandbox prewarm lock 被 killed process 孤立后阻塞 30 分钟
2. [#412](https://github.com/vercel/eve/issues/412) 子 Agent `MODEL_CALL_FAILED` 被吞掉，orchestrator 误报"completed successfully"
3. [#402](https://github.com/vercel/eve/issues/402) `withEve` Vercel build 报 `ENOENT __server.func/.vc-config.json`
4. [#396](https://github.com/vercel/eve/issues/396) `eve dev` 在长 run + 大量交错 tool 调用时因 microsandbox session flood 崩溃
5. [#388](https://github.com/vercel/eve/issues/388) `eve dev` 端口文档示例（3000）与默认（2000）不一致
6. [#387](https://github.com/vercel/eve/issues/387) Schedule 触发的 sessions 无法 park（`Cannot park: no continuation token available`）
7. [#393](https://github.com/vercel/eve/issues/393) 请求支持子 Agent 直接被调用或作为子 Agent 调用
8. Beta 状态：API / 文档 / 行为在 GA 前可能变动
9. 强 Vercel 绑定倾向（虽可自托管，但最佳体验在 Vercel）
10. TypeScript-only，Python 团队不适用

### 仓库活跃度

| 指标 | 数值 |
|------|------|
| **Stars** | 2,859（截至 2026-06-29） |
| **创建时间** | 2026-06-16 |
| **总 commits** | 197 |
| **Releases** | 37 个（最新 `eve@0.17.0`） |
| **最近 push** | 2026-06-28 |
| **License** | Apache-2.0 |

13 天内 2859 stars、197 commits、37 个 release——极其活跃，处于密集迭代期。

---

## 十、社区框架对比

| 维度 | Eve | LangChain/LangGraph | Mastra | OpenAI Agents SDK |
|---|---|---|---|---|
| 配置方式 | **文件系统约定** | Python/JS 代码 | TS 代码 | Python 代码 |
| 语言 | TypeScript | Python/JS | TypeScript | Python |
| 类型安全 | Zod + TS，强 | 弱 | Zod，强 | 弱 |
| 子 Agent 隔离 | **完全隔离** | 共享上下文 | 隔离 | 共享 |
| 沙箱 | **内置多后端** | 无 | 内置 | 无 |
| 多通道 | **内置** | 需自搭 | ChannelProvider | 无 |
| 持久化 | Workflow SDK（durable） | Checkpoint | durable workflows | 无 |
| 人类审批 | **原生** | 需自实现 | 内置 | 需自实现 |
| 部署 | `vercel deploy` | 自搞 | Vercel/CF | 自搞 |
| 平台依赖 | 偏向 Vercel（可自托管） | 无 | 无 | OpenAI |
| 成熟度 | Beta（2026.6 发布） | 成熟 | 成熟（1.0 于 2026.1） | 成熟 |

---

## 十一、信息来源

### 官方资源

- [vercel/eve — GitHub](https://github.com/vercel/eve)（2,859 stars, 197 commits, 37 releases）
- [Eve 官方文档](https://eve.dev/docs/introduction)
- [Introducing eve — Vercel Blog](https://vercel.com/blog/introducing-eve)
- [Vercel Eve 产品页](https://vercel.com/eve)
- [Workflow SDK](https://workflow-sdk.dev/)
- [Workflow SDK Human-in-the-Loop](https://workflow-sdk.dev/docs/ai/human-in-the-loop)
- [Vercel Agent Docs](https://vercel.com/docs/agent)
- [AI Agents on Vercel — KB](https://vercel.com/kb/guide/ai-agents)
- [How to build a durable AI code agent on Vercel](https://vercel.com/kb/guide/how-to-build-a-durable-ai-code-agent-on-vercel)
- [Vercel for Platforms docs](https://aming.ourdisc.net/platforms/docs)
- [Building an agent with OpenAI Agents SDK and Vercel Sandbox](https://geo-dns.vercel-infra-staging.com/kb/guide/building-an-agent-with-openai-agents-sdk-and-vercel-sandbox)

### 深度分析文章

- [Vercel's Agentic Infrastructure Stack Explained — Developers Digest](https://developersdigest.tech/blog/vercel-agentic-infrastructure-stack)
- [Why Vercel AI SDK is the right first-agent stack in 2026 — FrankX](https://www.frankx.ai/blog/vercel-ai-sdk-first-agent-stack)
- [Vercel AI SDK 5: Agent Loop, MCP, and Generative UI v2 Deep Dive — CallSphere](https://callsphere.ai/blog/td30-fw-vercel-ai-sdk-5-agent-loop-mcp-generative-ui-v2)
- [What is Vercel AI SDK? — VoltAgent](https://voltagent.dev/blog/vercel-ai-sdk/)
- [Choosing the Best AI Agent Framework in 2025 — FASHN](https://fashn.ai/tr/blog/choosing-the-best-ai-agent-framework-in-2025)
- [Comparing the Latest AI Agent Frameworks in 2025 — Daniel Broadhurst](https://new.danielbroadhurst.co.uk/posts/comparing-the-latest-ai-agent-frameworks-in-2025/)
- [Vercel AI SDK Alternatives in 2026 — FutureAGI](https://futureagi.com/blog/vercel-ai-sdk-alternatives-2026/)
- [How Vercel Runs on AI Agents — SaaStr](https://www.saastr.com/how-vercel-runs-on-ai-agents-96-of-marketing-93-of-support-and-an-sdr-team-reabsorbed-a-deep-dive-with-cpo-tom-occhino/)

### 中文深度文章

- [Vercel 出的 Agent 框架 eve：用文件系统定义 AI Agent — 掘金 吴琼琼](https://juejin.cn/post/7653060452369547305)
- [一个目录一个 Agent，Vercel Eve 的这套架构设计太舒服了！ — 掘金 程序猿DD](https://juejin.cn/post/7653131325730717742)
- [Vercel 开源了 Agent 框架，让开发AI agent像搭积木一样简单 — 掘金](https://juejin.cn/post/7654020257112719398)
- [2026 年前端 Agent 框架选型：Mastra 与 LangChain 该怎么选 — 掘金](https://juejin.cn/post/7617004481947697162)

### GitHub Issues（社区讨论与已知问题）

- [#432 Sandbox prewarm lock orphaned](https://github.com/vercel/eve/issues/432)
- [#412 Subagent MODEL_CALL_FAILED swallowed](https://github.com/vercel/eve/issues/412)
- [#402 withEve Vercel build ENOENT](https://github.com/vercel/eve/issues/402)
- [#396 eve dev crashes (microsandbox session flood)](https://github.com/vercel/eve/issues/396)
- [#393 Subagent direct call support](https://github.com/vercel/eve/issues/393)
- [#388 eve dev port mismatch](https://github.com/vercel/eve/issues/388)
- [#387 Schedule-triggered sessions cannot park](https://github.com/vercel/eve/issues/387)
- [#384 Feature request: EVE provider for AI SDK](https://github.com/vercel/eve/issues/384)
- [#281 HTTP header injection vulnerability](https://github.com/vercel/eve/issues/281)
