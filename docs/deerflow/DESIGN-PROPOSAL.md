# SkillRegistry / SubAgentTask / MemorySystem 详细设计文档

> 日期: 2026-06-26
> 状态: 设计草案（待评审决定）
> 参考: DeerFlow 2.0 技能系统 + LangGraph Subagents + 业界 Memory System 实践

---

## 一、SkillRegistry — 技能系统

### 1.1 目标

让 Agent 通过声明式技能定义（非硬编码）获得新能力，支持渐进式加载以节省 context window。

### 1.2 DeerFlow 实现分析

DeerFlow 的技能系统核心特点：
- **SKILL.md 格式**：YAML frontmatter（name/description/license/allowed-tools）+ Markdown body
- **三级渐进加载**：Stage 1 技能列表（始终加载）→ Stage 2 完整内容（按需加载）→ Stage 3 scripts/references（执行时加载）
- **目录结构**：`skills/public/`（内置）+ `skills/custom/`（用户安装）
- **.skill 归档**：支持打包分发
- **权限联动**：技能声明的 `allowed-tools` 与 Guardrails 系统联动

### 1.3 XAR 设计方案

#### 选型建议：轻量 SkillManifest + 渐进加载

**不建议**照搬 DeerFlow 完整方案（SKILL.md + .skill 归档 + Docker 沙箱执行），复杂度过高。建议采用**简化版**：

#### 核心数据模型

```python
class SkillManifest(BaseModel):
    """技能元数据 — Stage 1 始终加载（约 100 tokens/技能）"""
    name: str
    description: str
    version: str = "1.0.0"
    allowed_tools: list[str] = []
    permissions: list[str] = []
    entrypoint: str = ""

class SkillContent(BaseModel):
    """技能完整内容 — Stage 2 按需加载"""
    name: str
    instructions: str
    system_prompt_addition: str
    tool_overrides: dict = {}

class SkillRegistry:
    """技能注册与发现中心"""
    def discover(self) -> list[SkillManifest]: ...
    def get_manifest(self, name: str) -> SkillManifest | None: ...
    def load_content(self, name: str) -> SkillContent: ...
    def inject_to_system_prompt(self, skills: list[str]) -> str: ...
```

#### 技能定义格式

```yaml
# skills/research/SKILL.yaml
name: research
description: >
  Conduct multi-source web research with citations.
  Use when user asks for research, analysis, or fact-checking.
version: "1.0.0"
allowed_tools: [web_search, browse, read_file, write_file]
permissions: [network, filesystem:read]
```

```markdown
<!-- skills/research/SKILL.md -->
# Research Skill
## When to Use
- User requests research or analysis
## Workflow
1. Break down research question into sub-queries
2. Search multiple sources
3. Cross-reference findings
4. Generate cited summary
```

#### 渐进加载流程

```
Agent 启动
  |-> SkillRegistry.discover() -> [SkillManifest, ...]   (Stage 1: ~100 tokens/skill)
  |-> 注入 system prompt: "Available Skills: 1. research ... 2. coding ..."
  |-> Agent 决定使用某技能
  |-> SkillRegistry.load_content("research") -> SkillContent  (Stage 2: ~500-2000 tokens)
  |-> 注入技能指令到 context
  |-> Agent 使用技能定义的工具执行任务
```

#### 复杂度评估

| 组件 | 工作量 |
|------|--------|
| SkillManifest + SkillContent | 0.5 天 |
| SkillRegistry (扫描+解析+缓存) | 1 天 |
| LoadSkillTool + System prompt 注入 | 0.5 天 |
| 内置技能 (3-5 个) | 1 天 |
| 测试 | 0.5 天 |
| **合计** | **约 2-3 天** |

---

## 二、SubAgentTask — 子智能体系统

### 2.1 目标

让主 Agent 能将复杂任务拆分委派给子 Agent，实现并行执行、专家分工、上下文隔离。

### 2.2 DeerFlow 实现分析

DeerFlow 的子智能体系统核心特点：
- **task 工具**：主 Agent 通过 `task()` 工具委派任务
- **SubagentExecutor**：独立执行器，支持并行
- **并发控制**：SubagentLimitMiddleware 限制最大并行数（默认 3）
- **多批次执行**：超过 3 个子任务分批执行
- **隔离事件循环**：每个子 Agent 独立 asyncio loop
- **协作取消**：threading.Event 在迭代边界检查
- **ACP 集成**：外部 Agent（Claude Code/Codex）可作为子 Agent

### 2.3 XAR 设计方案

#### 选型建议：基于 AgentScope Agent 的轻量子 Agent

**不建议**实现完整的 SubagentExecutor + 隔离事件循环 + ACP 集成。建议采用**基于 AgentScope Agent 的工具模式**：

#### 核心数据模型

```python
class SubAgentSpec(BaseModel):
    """子智能体规格定义"""
    name: str
    description: str
    system_prompt: str
    model_config_name: str = ""
    allowed_tools: list[str] = []
    max_turns: int = 10

class SubAgentTask(BaseModel):
    """子智能体任务包 — 上下文隔离"""
    task_id: str
    parent_session_id: str
    spec_name: str
    objective: str
    constraints: list[str] = []
    input_context: str = ""
    expected_output: str = ""

class SubAgentResult(BaseModel):
    """子智能体执行结果"""
    task_id: str
    success: bool
    summary: str
    findings: list[str] = []
    artifacts: list[str] = []
    errors: list[str] = []
    token_usage: int = 0
    duration_seconds: float = 0

class SubAgentExecutor:
    """子智能体执行器"""
    def __init__(self, specs: list[SubAgentSpec], max_concurrent: int = 3): ...

    async def execute(self, task: SubAgentTask) -> SubAgentResult:
        """执行单个子智能体任务"""
        # 创建独立的 AgentScope Agent
        # 使用 semaphore 控制并发
        # 返回结果摘要

    async def execute_batch(
        self, tasks: list[SubAgentTask]
    ) -> list[SubAgentResult]:
        """批量并行执行（分批，每批 max_concurrent 个）"""
```

#### task 工具

```python
class TaskTool(ToolBase):
    """主 Agent 通过此工具委派子任务"""
    async def __call__(
        self, subagent: str, description: str
    ) -> str:
        task = SubAgentTask(
            task_id=uuid4(),
            parent_session_id=current_session(),
            spec_name=subagent,
            objective=description,
        )
        result = await self._executor.execute(task)
        return result.summary
```

#### 配置定义

```yaml
subagents:
  enabled: true
  max_concurrent: 3
  specs:
    - name: researcher
      description: "Conduct web research with citations"
      system_prompt: "You are a research specialist..."
      allowed_tools: [web_search, browse, read_file]
      max_turns: 10
    - name: coder
      description: "Write and execute code"
      system_prompt: "You are a coding specialist..."
      allowed_tools: [bash, read_file, write_file, edit]
      max_turns: 15
```

#### 复杂度评估

| 组件 | 工作量 |
|------|--------|
| SubAgentSpec + Task + Result 模型 | 0.5 天 |
| SubAgentExecutor (并行+信号量+批次) | 1.5 天 |
| TaskTool + SubAgentLimitMiddleware | 0.5 天 |
| 配置解析 + 结果合并 | 0.5 天 |
| 测试 | 1 天 |
| **合计** | **约 3-4 天** |

---

## 三、MemorySystem — 长期记忆

### 3.1 目标

让 Agent 跨会话记住用户偏好、项目信息、历史事件，实现个性化服务。

### 3.2 DeerFlow 实现分析

DeerFlow 的记忆系统核心特点：
- **多类型记忆**：用户偏好、项目信息、事件记忆、语义知识、程序性记忆
- **MemoryMiddleware**：在回复后异步提取记忆候选
- **置信度评分**：每条记忆有 confidence score
- **后台更新**：不阻塞主对话
- **注入策略**：检索 top-k 相关记忆注入 context

### 3.3 XAR 设计方案

#### 选型建议：Redis 存储 + 关键词检索（MVP），向量检索（V2）

分两阶段实施：
- **MVP**：Redis hash 存储 + 关键词匹配检索
- **V2**：接入 Redis Vector Search 或外部向量库

#### 核心数据模型

```python
class MemoryItem(BaseModel):
    """记忆条目"""
    id: str
    user_id: str
    tenant_id: str               # 多租户隔离
    scope: str = "user"          # user / project / global
    type: str = "fact"           # preference / fact / procedure / episode
    content: str
    source_session_id: str = ""
    confidence: float = 0.5
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    tags: list[str] = []

class MemoryStore:
    """记忆存储 — 基于 Redis"""
    async def add(self, item: MemoryItem) -> str: ...
    async def search(
        self, query: str, user_id: str, top_k: int = 5
    ) -> list[MemoryItem]: ...
    async def delete(self, memory_id: str) -> bool: ...

class MemoryMiddleware(MiddlewareBase):
    """记忆中间件 — 注入 + 提取"""
    async def on_reply_start(self, context):
        # 检索相关记忆注入 context
        memories = await self._store.search(
            query=context.user_message, user_id=context.user_id
        )
        if memories:
            context.system_prompt += self._format_memories(memories)

    async def on_reply_end(self, context):
        # 后台提取记忆（不阻塞）
        asyncio.create_task(self._extract_memories(context))
```

#### 记忆注入格式

```
## Long-term Memory
The following may be relevant. If it conflicts with the current request, follow the current request.

- [preference] User prefers Chinese responses and table format
- [fact] User's project uses Python 3.11 and FastAPI
- [episode] Last session: generated deployment script for Docker
```

#### 配置

```yaml
memory:
  enabled: true
  backend: redis          # redis (MVP) / vector (V2)
  max_injected: 5
  auto_extract: true
  extract_confidence_threshold: 0.6
  ttl_days: 90
```

#### 复杂度评估

| 组件 | 工作量 |
|------|--------|
| MemoryItem 模型 | 0.5 天 |
| MemoryStore MVP (Redis CRUD + 关键词) | 1 天 |
| MemoryMiddleware (注入 + 后台提取) | 1 天 |
| LLM 记忆提取 prompt | 0.5 天 |
| 测试 | 0.5 天 |
| **合计 (MVP)** | **约 2-3 天** |
| **V2 向量检索** | **+2 天** |

---

## 四、实施优先级建议

| 优先级 | 模块 | 工作量 | 理由 |
|--------|------|--------|------|
| 1 | 循环检测中间件 | 0.5 天 | 最小工作量，立即见效 |
| 2 | LLM 错误处理中间件 | 0.5 天 | 最小工作量，立即见效 |
| 3 | **SkillRegistry** | 2-3 天 | 扩展性基础，其他能力可作为技能实现 |
| 4 | **MemorySystem MVP** | 2-3 天 | 用户体验提升，独立模块 |
| 5 | **SubAgentTask** | 3-4 天 | 最复杂，依赖技能系统成熟 |

**总计约 9-12 天**（含 2 个小中间件）。

### 选型决策点

需要你决定的问题：

1. **SkillRegistry**：SKILL.yaml + SKILL.md 格式是否可以？还是想用更简单的纯 Python 类定义？
2. **SubAgentTask**：是否需要并行执行？如果串行会简单很多（减 1 天）。
3. **MemorySystem**：MVP 用关键词检索是否够用？还是一开始就要向量检索？
4. **实施顺序**：是否同意先做循环检测 + 错误处理（1 天见效），再做技能系统？
