# Langfuse 可观测性运行指南

> 日期: 2026-06-26
> 模块: Observability / Langfuse Tracing

---

## 一、架构概览

```
Agent 请求
  ↓
LangfuseTracerMiddleware          ← on_reasoning: trace model call
  ├── on_reasoning → trace_generation(model, tokens, tenant, user, session)
  └── on_acting → trace_tool_call(tool_name, duration, success)
  ↓
LangfuseExporter
  ├── enabled → Langfuse client → Langfuse Server → Web 面板
  └── disabled → No-op (零开销)
```

## 二、启动本地 Langfuse

### 2.1 Docker Compose 配置

```yaml
# /tmp/langfuse/docker-compose.yml
services:
  langfuse-db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse
      POSTGRES_DB: langfuse
    volumes:
      - langfuse-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse"]
      interval: 5s
      timeout: 3s
      retries: 10

  clickhouse:
    image: clickhouse/clickhouse-server:24.3-alpine
    environment:
      CLICKHOUSE_DB: langfuse
      CLICKHOUSE_USER: langfuse
      CLICKHOUSE_PASSWORD: langfuse
    volumes:
      - clickhouse-data:/var/lib/clickhouse
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:8123/ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  langfuse:
    image: langfuse/langfuse:latest
    ports:
      - "3000:3000"
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      CLICKHOUSE_URL: http://clickhouse:8123
      CLICKHOUSE_USER: langfuse
      CLICKHOUSE_PASSWORD: langfuse
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: local-dev-secret-not-for-production-32chars
      SALT: local-dev-salt-not-for-production-32chars!!
      TELEMETRY_ENABLED: "false"
    depends_on:
      langfuse-db:
        condition: service_healthy
      clickhouse:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:3000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 15

volumes:
  langfuse-db-data:
  clickhouse-data:
```

### 2.2 启动

```bash
mkdir -p /tmp/langfuse
# 将上面的 docker-compose.yml 保存到 /tmp/langfuse/docker-compose.yml
cd /tmp/langfuse
docker compose up -d

# 等待就绪（首次需拉取镜像，约 5-10 分钟）
for i in $(seq 1 60); do
  if curl -sf http://localhost:3000/api/health 2>/dev/null | grep -qi "ok\|health"; then
    echo "Langfuse ready after ${i}0s"
    break
  fi
  sleep 10
done

# 验证
curl http://localhost:3000/api/health
```

### 2.3 获取 API Keys

1. 打开 http://localhost:3000
2. 注册管理员账号（首次访问）
3. 创建 Organization → 创建 Project
4. 在 Settings → API Keys 页面获取:
   - Public Key: `pk-lf-...`
   - Secret Key: `sk-lf-...`

## 三、配置 XRuntime

### 3.1 环境变量

```bash
export XRUNTIME_LANGFUSE_ENABLED=true
export XRUNTIME_LANGFUSE_HOST=http://localhost:3000
export XRUNTIME_LANGFUSE_PUBLIC_KEY=pk-lf-xxx
export XRUNTIME_LANGFUSE_SECRET_KEY=sk-lf-xxx
```

### 3.2 YAML 配置

```yaml
# xruntime.yaml
observability:
  langfuse_enabled: true
  langfuse_host: "http://localhost:3000"
  langfuse_public_key: "pk-lf-xxx"
  langfuse_secret_key: "sk-lf-xxx"
  audit_enabled: true
```

### 3.3 安装 Langfuse SDK

```bash
pip install langfuse
```

## 四、运行 E2E 测试

### 4.1 Mock 测试（无需 Langfuse 服务器）

```bash
# 验证 tracer 逻辑正确（使用 mock exporter）
pytest tests/xruntime/test_langfuse_tracer.py -v

# 验证完整中间件链
pytest tests/xruntime/integration/test_e2e_full_runtime.py -v

# 验证所有 568 个测试
pytest tests/xruntime -q
```

### 4.2 真实 Langfuse 测试（需要服务器运行）

```python
# scripts/test_langfuse_live.py
import asyncio
from xruntime._runtime._langfuse import LangfuseConfig, LangfuseExporter
from xruntime._runtime._middleware._langfuse_tracer import (
    LangfuseTracerMiddleware,
)

class FakeAgent:
    name = "test-agent"
    model = type("M", (), {"model_name": "gpt-4o", "name": "gpt-4o"})()

class FakeToolCall:
    name = "bash"
    input = {"command": "ls"}

async def _gen():
    return
    yield

async def main():
    # 配置真实 Langfuse
    exporter = LangfuseExporter(LangfuseConfig(
        enabled=True,
        host="http://localhost:3000",
        public_key="pk-lf-xxx",
        secret_key="sk-lf-xxx",
    ))

    mw = LangfuseTracerMiddleware(
        exporter=exporter,
        tenant_id="acme",
        user_id="alice",
        session_id="sess-test-1",
    )

    # 模拟 3 轮 model + tool 调用
    for i in range(3):
        async for _ in mw.on_reasoning(FakeAgent(), {}, lambda: _gen()):
            pass
        async for _ in mw.on_acting(
            FakeAgent(), {"tool_call": FakeToolCall()}, lambda: _gen()
        ):
            pass

    # 等待 Langfuse 异步刷新
    await asyncio.sleep(3)
    print("Traces sent! Check http://localhost:3000")

asyncio.run(main())
```

```bash
python scripts/test_langfuse_live.py
```

## 五、查看 Langfuse 面板

### 5.1 访问面板

打开浏览器: http://localhost:3000

### 5.2 查看追踪链路

1. 左侧导航 → **Tracing**
2. 可以看到 trace 列表，每条 trace 包含:
   - **Session ID**: `sess-test-1`
   - **User ID**: `alice`
   - **Generations**: `model:gpt-4o` (3 次)
   - **Spans**: `tool:bash` (3 次)

### 5.3 Trace 详情

点击任意 trace 查看:
- **Timeline**: model call → tool call 的时间线
- **Metadata**: `tenant_id=acme`, `user_id=alice`, `session_id=sess-test-1`, `turn=1/2/3`
- **Duration**: 每次调用的耗时
- **Secret Redaction**: 确认 `sk-*` 密钥已被 `[REDACTED_API_KEY]` 替换

### 5.4 按 Tenant 过滤

在 Langfuse 面板的 metadata 中搜索 `tenant_id=acme` 可以过滤特定租户的 trace。

## 六、中间件日志

### 6.1 启用 DEBUG 日志

```bash
export PYTHONPATH=src
python3 -c "
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
)
# 运行 Agent...
"
```

### 6.2 日志输出示例

**循环检测**:
```
2026-06-26 xruntime.middleware.loop_detection DEBUG tool=bash repeat_count=2/3 window=5
2026-06-26 xruntime.middleware.loop_detection WARNING LOOP DETECTED: tool=bash repeated 4 times (max=3). Injecting break message.
```

**LLM 错误处理**:
```
2026-06-26 xruntime.middleware.llm_error_handling WARNING model call failed (attempt 1/3): RateLimitError. Retrying in 1.0s
2026-06-26 xruntime.middleware.llm_error_handling INFO model call succeeded after 1 retries
2026-06-26 xruntime.middleware.llm_error_handling ERROR model call failed after 3 retries: RateLimitError
2026-06-26 xruntime.middleware.llm_error_handling ERROR circuit breaker: CLOSED → OPEN (failures=5, threshold=5)
2026-06-26 xruntime.middleware.llm_error_handling INFO circuit breaker: OPEN → HALF_OPEN
2026-06-26 xruntime.middleware.llm_error_handling INFO circuit breaker: HALF_OPEN → CLOSED
```

## 七、Mock 测试验证结果

9 个 mock 测试全部通过，验证了:

| 测试 | 验证内容 | 状态 |
|------|----------|------|
| noop exporter skips | 未配置时不产生 trace | ✅ |
| model call traced | model 调用产生 generation trace | ✅ |
| tenant context | trace 含 tenant/user/session | ✅ |
| tool call traced | tool 调用产生 span trace | ✅ |
| tool metadata | trace 含 duration/success | ✅ |
| turn counter | 多次调用 turn 递增 | ✅ |
| mixed calls | model+tool 交替全部 trace | ✅ |
| secret redaction | trace 中无原始密钥 | ✅ |
| duration recorded | trace 含耗时 | ✅ |

## 八、故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `is_noop=True` | Langfuse 未启用或 SDK 未安装 | `pip install langfuse` + 设置 `langfuse_enabled=true` |
| 面板无数据 | trace 异步发送，需等待 | `await asyncio.sleep(3)` 后刷新 |
| ClickHouse 启动失败 | 镜像拉取慢 | 使用 `clickhouse/clickhouse-server:24.3-alpine` |
| 端口 3000 被占用 | 其他服务占用了端口 | 修改 docker-compose 端口映射 |
