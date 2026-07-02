# 新注入模块架构设计文档

> 日期: 2026-06-26
> 模块: SkillRegistry / MemoryStore / SubAgentExecutor

---

## 一、模块关系图

```
create_xruntime_extension()
  │
  ├── skill_registry (SkillRegistry)
  │     ├── discover() → [SkillManifest]
  │     ├── load_content(name) → SkillContent
  │     └── inject_to_system_prompt() → str
  │
  ├── memory_store (MemoryStore)
  │     ├── add(MemoryItem) → str
  │     ├── search(query, user, tenant) → [MemoryItem]
  │     └── HybridRetriever(store).search() → [MemoryItem]
  │
  ├── subagent_executor (SubAgentExecutor)
  │     ├── execute(SubAgentTask) → SubAgentResult
  │     ├── execute_batch([Task]) → [Result]
  │     └── TaskTool(executor).__call__() → dict
  │
  └── extra_agent_middlewares (factory)
        ├── LangfuseTracerMiddleware
        ├── LoopDetectionMiddleware
        ├── LLMErrorHandlingMiddleware
        ├── AuditMiddleware
        ├── QuotaMiddleware
        ├── RbacMiddleware
        └── SecretRedactionMiddleware
```

## 二、协同工作流程

```
用户请求 "Research Python trends"
  ↓
1. 中间件链处理请求
   ├── LangfuseTracer 开始 trace
   ├── LoopDetection 初始化
   ├── LLMErrorHandling 熔断检查
   ├── Audit 记录请求
   ├── Quota 检查配额
   ├── RBAC 权限验证
   └── Redaction 脱敏
  ↓
2. Agent 启动
   ├── SkillRegistry.inject_to_system_prompt()
   │   → "Available Skills: 1. research: ..."
   ├── MemoryStore.search("Python trends", user, tenant)
   │   → [MemoryItem("User prefers Python"), ...]
   └── 注入到 Agent system prompt
  ↓
3. Agent 决策: 委派给 researcher 子 Agent
   ├── TaskTool(subagent="researcher", description="...")
   ├── SubAgentExecutor.execute(SubAgentTask)
   │   ├── 上下文隔离: 只传 objective + input_context
   │   ├── 并行执行 (Semaphore max 3)
   │   └── 返回 SubAgentResult (summary + findings)
   └── MetricsCollector.record_subagent_call()
  ↓
4. 子 Agent 执行
   ├── SkillRegistry.load_content("research")
   │   → SkillContent(instructions="# Research\n...")
   ├── 执行研究任务
   └── 返回 findings
  ↓
5. 结果存储
   ├── MemoryStore.add(MemoryItem(content=finding, ...))
   └── HybridRetriever 索引更新
  ↓
6. Agent 汇总 → 返回用户
   ├── LangfuseTracer trace model call + tool call
   ├── Audit 记录完成
   └── Quota 扣减
```

## 三、数据流

| 阶段 | 数据 | 来源 | 去向 |
|------|------|------|------|
| 请求处理 | AuthPrincipal | AuthMiddleware | RBAC/Quota |
| 技能注入 | SkillManifest[] | SkillRegistry.discover() | system prompt |
| 记忆检索 | MemoryItem[] | MemoryStore.search() | system prompt |
| 子 Agent 委派 | SubAgentTask | TaskTool | SubAgentExecutor |
| 子 Agent 结果 | SubAgentResult | SubAgentExecutor | Agent context |
| 发现存储 | MemoryItem | MemoryStore.add() | HybridRetriever |
| 指标记录 | subagent stats | MetricsCollector | /metrics endpoint |
| 追踪 | trace spans | LangfuseTracer | Langfuse 面板 |

## 四、配置接口

```python
ext = create_xruntime_extension()

# 访问模块
skill_registry = ext["skill_registry"]
memory_store = ext["memory_store"]
executor = ext["subagent_executor"]

# 添加自定义子 Agent
from xruntime._runtime._subagents import SubAgentSpec
executor.add_spec(SubAgentSpec(
    name="analyst",
    description="Data analyst",
    system_prompt="You analyze data.",
))

# 添加自定义技能目录
skill_registry.add_dir("/path/to/custom/skills")
skill_registry.discover()

# 使用混合检索
from xruntime._runtime._memory._hybrid_retriever import HybridRetriever
retriever = HybridRetriever(memory_store)
results = retriever.search("query", user_id="alice", tenant_id="acme")
```

## 五、Prometheus 指标

```
# 会话
xruntime_active_sessions{tenant="acme"} 3

# 工具调用
xruntime_tool_calls_total{tool="bash"} 42

# Token 消耗
xruntime_tokens_total{tenant="acme",type="input"} 15000
xruntime_tokens_total{tenant="acme",type="output"} 8000

# 子 Agent 执行 ← 新增
xruntime_subagent_calls_total{spec="researcher",status="success"} 12
xruntime_subagent_calls_total{spec="researcher",status="failure"} 1
xruntime_subagent_duration_seconds{spec="researcher"} 2.3500
xruntime_subagent_tokens_total{spec="researcher"} 45000
```
