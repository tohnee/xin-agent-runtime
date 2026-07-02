# Xin Agent Runtime 升级设计总览

> 日期: 2026-06-26
> 状态: 设计草案（待评审决定）
> 参考: DeerFlow 2.0 架构 + LangGraph Subagents + 业界实践

---

## 文档索引

| # | 模块 | 文档 | 工作量 | 优先级 |
|---|------|------|--------|--------|
| 01 | SkillRegistry（技能系统） | [01-SKILL-DESIGN.md](./01-SKILL-DESIGN.md) | 2-3 天 | P0 |
| 02 | SubAgentTask（子智能体） | [02-SUBAGENT-DESIGN.md](./02-SUBAGENT-DESIGN.md) | 3-4 天 | P0 |
| 03 | MemorySystem（长期记忆） | [03-MEMORY-DESIGN.md](./03-MEMORY-DESIGN.md) | 2-3 天 | P0 |
| 04 | Sandbox（沙箱环境） | [04-SANDBOX-DESIGN.md](./04-SANDBOX-DESIGN.md) | 5-6 天 | P1 |
| 05 | Observability（可观测性） | [05-OBSERVABILITY-DESIGN.md](./05-OBSERVABILITY-DESIGN.md) | 4-6 天 | P1 |

---

## 一、背景

通过 [DeerFlow 2.0 对比分析](./COMPARISON.md)，识别出 XAR 需要补齐的 5 个核心能力模块。本文档汇总各模块的设计方案、选型建议和开发计划。

---

## 二、模块摘要

### 01. SkillRegistry — 技能系统

**目标**: Agent 通过声明式技能定义获得新能力，渐进式加载节省 context window。

**选型**: 轻量 SkillManifest + 两级渐进加载（不照搬 DeerFlow .skill 归档 + Docker 执行）

**核心组件**:
- `SkillManifest` — 元数据（~100 tokens/技能，始终加载）
- `SkillContent` — 完整指令（~500-2000 tokens，按需加载）
- `SkillRegistry` — 目录扫描 + YAML 解析 + 缓存
- `LoadSkillTool` — Agent 按需加载技能的工具

**详细设计**: 见下方 "一、SkillRegistry" 章节

---

### 02. SubAgentTask — 子智能体系统

**目标**: 主 Agent 将复杂任务委派给子 Agent，实现并行执行、专家分工、上下文隔离。

**选型**: 基于 AgentScope Agent 的工具模式（不实现隔离事件循环 + ACP）

**核心组件**:
- `SubAgentSpec` — 子 Agent 规格（name/system_prompt/tools/max_turns）
- `SubAgentTask` — 任务包（目标 + 约束 + 输入摘要，上下文隔离）
- `SubAgentResult` — 执行结果（摘要 + 发现 + 产物 + token 用量）
- `SubAgentExecutor` — 并行执行 + 信号量限制（max 3）+ 批次
- `TaskTool` — 主 Agent 委派子任务的工具

**详细设计**: 见下方 "二、SubAgentTask" 章节

---

### 03. MemorySystem — 长期记忆

**目标**: Agent 跨会话记住用户偏好、项目信息、历史事件。

**选型**: Redis 存储 + 关键词检索（MVP），向量检索（V2 追加 2 天）

**核心组件**:
- `MemoryItem` — 多租户隔离 + 类型 + 置信度 + 过期
- `MemoryStore` — Redis CRUD + 关键词/向量检索
- `MemoryMiddleware` — 回复前检索注入 top-5，回复后后台提取

**详细设计**: 见下方 "三、MemorySystem" 章节

---

### 04. Sandbox — 沙箱环境升级

**目标**: 将 Placeholder 替换为可执行的 Docker/E2B 沙箱，增加审计和资源限制。

**现状**: `WorkspaceManagerFactory` 对 docker/e2b 只返回空壳 Placeholder。

**选型**: 直接接入 AgentScope 已有的 DockerWorkspace/E2BWorkspace（不自建 Provider 抽象）

**核心改动**:
- 修复 `WorkspaceManagerFactory.create()` — 接入真实 Docker/E2B
- 扩展 `WorkspaceConfig` — 新增 image/network/memory/cpu/timeout 配置
- `SandboxAuditMiddleware` — 危险命令检测 + 审计日志
- `VirtualPathMapper` — Agent 看到虚拟路径 `/workspace/`，实际映射到沙箱

**详细设计**: [04-SANDBOX-DESIGN.md](./04-SANDBOX-DESIGN.md)

---

### 05. Observability — 可观测性集成

**目标**: 将已有的 Langfuse 骨架代码接入中间件链，实现端到端 trace。

**现状**: `LangfuseExporter` 有完整方法但无人调用；`MetricsCollector` 仅内存无 endpoint。

**选型**: 接入 Langfuse callback（方案 A，已有骨架代码）

**核心改动**:
- `LangfuseTracerMiddleware` — 在 model_call/tool_call 后 trace
- Knowledge retrieve trace — 在 LlmWikiAdapter 中调用
- Prometheus HTTP endpoint — `/metrics` 路由
- OTel tracer（可选 V2）— 标准化分布式追踪
- `TraceContext` — 统一 tenant/user/session 关联

**详细设计**: [05-OBSERVABILITY-DESIGN.md](./05-OBSERVABILITY-DESIGN.md)

---

## 三、实施计划

### 阶段一：快速见效（1 天）

| # | 模块 | 工作量 | 说明 |
|---|------|--------|------|
| 0a | 循环检测中间件 | 0.5 天 | 重复 action 检测，防止 Agent 死循环 |
| 0b | LLM 错误处理中间件 | 0.5 天 | retry + 降级 + 熔断 |

### 阶段二：核心能力（7-10 天）

| # | 模块 | 工作量 | 依赖 |
|---|------|--------|------|
| 1 | SkillRegistry | 2-3 天 | 无 |
| 2 | MemorySystem MVP | 2-3 天 | 无 |
| 3 | SubAgentTask | 3-4 天 | SkillRegistry（子 Agent 可使用技能） |

### 阶段三：基础设施升级（9-12 天）

| # | 模块 | 工作量 | 依赖 |
|---|------|--------|------|
| 4 | Sandbox 升级 | 5-6 天 | 无 |
| 5 | Observability | 4-6 天 | 无 |

### 总计

| 阶段 | 工作量 |
|------|--------|
| 阶段一 | 1 天 |
| 阶段二 | 7-10 天 |
| 阶段三 | 9-12 天 |
| **总计** | **17-23 天** |

---

## 四、选型决策点

需要你决定的问题：

1. **SkillRegistry**: SKILL.yaml + SKILL.md 格式是否可以？还是想用纯 Python 类定义？
2. **SubAgentTask**: 是否需要并行执行？串行会简单很多（减 1 天）。
3. **MemorySystem**: MVP 用关键词检索是否够用？还是一开始就要向量检索？
4. **Sandbox**: 是否同意直接接入 AgentScope 已有的 DockerWorkspace？还是想自建 Provider 抽象？
5. **Observability**: OTel 是否需要在第一版就做？还是先只做 Langfuse + Prometheus？
6. **实施顺序**: 是否同意阶段一→二→三的顺序？
