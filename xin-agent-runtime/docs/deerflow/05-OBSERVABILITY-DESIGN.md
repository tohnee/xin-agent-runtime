# 可观测性集成设计文档：Langfuse / LangSmith / OTel 升级方案

> 日期: 2026-06-26
> 状态: 设计草案
> 模块: Observability / Tracing / Metrics

---

## 一、现状对比

### 1.1 Xin Agent Runtime 现状

| 能力 | 状态 | 实现 |
|------|------|------|
| Langfuse 集成 | ⚠️ 骨架 | `LangfuseExporter` 有代码但默认 Noop，未接入中间件链 |
| OTel tracing | ❌ | 无 |
| Prometheus metrics | ⚠️ 内存版 | `MetricsCollector` 仅内存，无 HTTP endpoint |
| 审计日志 | ✅ | `AuditMiddleware` — JSONL 格式 |
| Token 用量跟踪 | ✅ | `QuotaMiddleware` — per-session |
| 知识库审计 | ✅ | `knowledge-audit.jsonl` |
| Trace 关联 (tenant/user/session) | ⚠️ | Langfuse 有参数但未实际注入 |
| Trace payload 脱敏 | ✅ | `_redact_payload()` |
| Tool call tracing | ⚠️ | `trace_tool_call()` 有但未被调用 |
| Model generation tracing | ⚠️ | `trace_generation()` 有但未被调用 |
| Knowledge retrieval tracing | ⚠️ | `trace_knowledge_retrieve()` 有但未被调用 |

**核心问题**: Langfuse exporter 有完整的方法定义，但没有任何地方调用这些方法 — 中间件链中没有接入 Langfuse trace。

### 1.2 DeerFlow 现状

| 能力 | 状态 | 实现 |
|------|------|------|
| LangSmith 集成 | ✅ | LangGraph 原生支持 LangSmith tracing |
| Langfuse 集成 | ✅ | Langfuse callback handler 注入 LangChain |
| OTel tracing | ✅ | LangGraph 原生 OTel spans |
| Token 用量 | ✅ | `TokenUsageMiddleware` — 精确到 model/turn |
| 沙箱审计 | ✅ | `SandboxAuditMiddleware` |
| Trace 关联 | ✅ | thread_id → trace session |
| 实时 streaming 可视化 | ✅ | SSE + 前端实时展示 |

---

## 二、DeerFlow 值得借鉴的设计

### 2.1 LangChain Callback Handler 模式

DeerFlow 通过 LangChain 的 callback 机制自动 trace 所有 LLM 调用，不需要手动在每个调用点插入 trace 代码：

```python
# DeerFlow 模式 (LangChain callback)
from langchain.callbacks import LangfuseCallbackHandler

langfuse_handler = LangfuseCallbackHandler(
    public_key="pk-...",
    secret_key="sk-...",
    host="https://cloud.langfuse.com",
)

agent = create_agent(
    model=model,
    tools=tools,
    # LangChain 自动 trace 所有 LLM/tool 调用
)
```

### 2.2 TokenUsageMiddleware

DeerFlow 的 Token 跟踪中间件精确到每次 model call：
- 记录 input/output tokens per turn
- 按 model 分类统计
- 支持预算告警
- 数据注入 trace

### 2.3 统一 trace context

DeerFlow 将 `thread_id` 作为 trace session ID，所有 span（model call、tool call、subagent）都关联到同一个 trace，形成完整的调用链。

---

## 三、XAR 升级设计方案

### 3.1 目标

将已有的 Langfuse 骨架代码接入中间件链，实现端到端 trace；同时接入 OTel 和 Prometheus HTTP endpoint。

### 3.2 选型建议

| 方案 | 复杂度 | 推荐度 | 说明 |
|------|--------|--------|------|
| A: 接入 Langfuse callback | 低 | ⭐⭐⭐ | Langfuse 已有骨架，只需在中间件中调用 |
| B: 接入 LangSmith | 中 | ⭐⭐ | 需要 LangChain callback，AgentScope 可能不兼容 |
| C: 自建 OTel tracing | 高 | ⭐ | 重复造轮子 |
| D: 接入 OpenTelemetry SDK | 中 | ⭐⭐ | 标准化，但工作量比 A 大 |

**推荐方案 A**：XAR 已有 `LangfuseExporter` 完整代码，只需在中间件中调用其 `trace_generation()` / `trace_tool_call()` / `trace_knowledge_retrieve()` 方法。

### 3.3 核心改动

#### 3.3.1 接入 Langfuse 到中间件链

```python
# src/xruntime/_runtime/_middleware/_langfuse_tracer.py

class LangfuseTracerMiddleware(MiddlewareBase):
    """将 Langfuse trace 接入中间件链"""

    def __init__(self, exporter: LangfuseExporter):
        self._exporter = exporter

    async def on_model_call(
        self,
        context: MiddlewareContext,
    ) -> None:
        """Model 调用后 trace"""
        if self._exporter.is_noop:
            return

        self._exporter.trace_generation(
            model=context.model_name,
            input_tokens=context.input_tokens,
            output_tokens=context.output_tokens,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=context.session_id,
            turn=context.turn_number,
        )

    async def on_acting(
        self,
        context: MiddlewareContext,
    ) -> None:
        """Tool 调用后 trace"""
        if self._exporter.is_noop:
            return

        self._exporter.trace_tool_call(
            tool_name=context.current_tool_name,
            tenant_id=context.tenant_id,
            session_id=context.session_id,
            duration_ms=context.tool_duration_ms,
            success=context.tool_success,
        )
```

#### 3.3.2 接入 Knowledge 检索 trace

```python
# 在 LlmWikiAdapter.retrieve() 中
class LlmWikiAdapter:
    async def retrieve(self, query: str, top_k: int = 5) -> list[WikiPage]:
        results = await self._bm25_search(query, top_k)

        # 接入 Langfuse trace
        if self._langfuse and not self._langfuse.is_noop:
            self._langfuse.trace_knowledge_retrieve(
                query=query,
                results=len(results),
                tenant_id=current_tenant.get(),
                top_k=top_k,
            )

        return results
```

#### 3.3.3 Prometheus HTTP endpoint

```python
# src/xruntime/_infra/_metrics_endpoint.py

from starlette.routing import Route
from starlette.responses import PlainTextResponse

def create_metrics_route(collector: MetricsCollector) -> Route:
    """创建 /metrics Prometheus endpoint"""

    async def metrics_endpoint(request):
        text = collector.export_prometheus()
        return PlainTextResponse(text, media_type="text/plain")

    return Route("/metrics", metrics_endpoint)

# 在 _server.py 中挂载
app.routes.append(create_metrics_route(metrics_collector))
```

#### 3.3.4 OTel tracing（可选 V2）

```python
# src/xruntime/_infra/_otel.py

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

class OtelConfig(BaseModel):
    enabled: bool = False
    endpoint: str = ""        # OTel collector URL
    service_name: str = "xin-agent-runtime"

class OtelTracer:
    """OpenTelemetry tracer — 标准化分布式追踪"""

    def __init__(self, config: OtelConfig):
        if not config.enabled:
            self._tracer = None
            return
        provider = TracerProvider()
        # 配置 OTLP exporter...
        self._tracer = trace.get_tracer(config.service_name)

    def span(self, name: str, **attrs):
        """创建 span"""
        if not self._tracer:
            return nullcontext()
        span = self._tracer.start_span(name)
        for k, v in attrs.items():
            span.set_attribute(k, v)
        return span
```

#### 3.3.5 统一 Trace Context

```python
# 确保所有 trace 都关联 tenant/user/session
class TraceContext(BaseModel):
    tenant_id: str
    user_id: str
    session_id: str
    trace_id: str = ""        # Langfuse trace ID
    parent_span_id: str = ""  # OTel parent span

    def to_langfuse_metadata(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
        }
```

### 3.4 配置

```yaml
observability:
  # Langfuse (LLM 追踪)
  langfuse:
    enabled: true
    host: "https://cloud.langfuse.com"
    public_key: "${LANGFUSE_PUBLIC_KEY}"
    secret_key: "${LANGFUSE_SECRET_KEY}"

  # Prometheus (指标)
  prometheus:
    enabled: true
    endpoint: "/metrics"      # HTTP endpoint

  # OpenTelemetry (分布式追踪, 可选)
  otel:
    enabled: false
    endpoint: ""              # OTel collector URL
    service_name: "xin-agent-runtime"

  # 审计日志
  audit:
    enabled: true
    format: "jsonl"
```

### 3.5 中间件链顺序

```
on_reply_start:
  1. AuditMiddleware        — 记录请求开始
  2. MemoryMiddleware        — 注入记忆 (如果实现)
  3. KnowledgeMiddleware     — 注入知识上下文
  4. RbacMiddleware          — 权限检查

on_model_call:
  5. QuotaMiddleware         — 配额扣减
  6. LangfuseTracerMiddleware — trace generation  ← 新增
  7. SecretRedactionMiddleware — 脱敏

on_acting:
  8. RbacMiddleware          — 工具权限检查
  9. LangfuseTracerMiddleware — trace tool call   ← 新增
  10. SandboxAuditMiddleware  — 沙箱命令审计      ← 新增(沙箱方案)

on_reply_end:
  11. AuditMiddleware        — 记录请求结束
  12. LangfuseTracerMiddleware — trace 完整会话   ← 新增
```

---

## 四、复杂度评估

| 组件 | 工作量 | 说明 |
|------|--------|------|
| LangfuseTracerMiddleware | 1 天 | 接入已有 exporter 到中间件 |
| Knowledge retrieve trace | 0.5 天 | 在 LlmWikiAdapter 中调用 |
| Prometheus HTTP endpoint | 0.5 天 | /metrics 路由 |
| OTel tracer (可选) | 1.5 天 | OTel SDK 集成 |
| TraceContext 统一 | 0.5 天 | tenant/user/session 关联 |
| 配置解析 | 0.5 天 | YAML observability section |
| 测试 | 1.5 天 | 中间件/endpoint/脱敏/集成 |
| **合计 (Langfuse+Prometheus)** | **~4 天** | |
| **合计 (+OTel)** | **~6 天** | |

---

## 五、与 DeerFlow 的差异保留

| XAR 优势 | DeerFlow 无 |
|----------|------------|
| 多租户 trace 隔离 (tenant_id in every span) | ❌ |
| Trace payload 自动脱敏 | ❌ |
| Prometheus per-tenant 指标 | ❌ |
| 知识库检索 trace | ❌ |
| 配额 trace (cost/block) | ❌ |

---

## 六、实施建议

1. **Phase 1 (2 天)**: 接入 LangfuseTracerMiddleware — 让已有的 Langfuse 代码真正工作
2. **Phase 2 (1 天)**: Prometheus HTTP endpoint — 让 metrics 可被采集
3. **Phase 3 (1 天)**: Knowledge retrieve trace — 补全 trace 链路
4. **Phase 4 (2 天, 可选)**: OTel 分布式追踪 — 标准化接入企业可观测性平台
