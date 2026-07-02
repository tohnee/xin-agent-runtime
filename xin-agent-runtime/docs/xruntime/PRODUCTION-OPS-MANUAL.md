# Xin Agent Runtime 生产环境部署操作手册

> 版本: v2.0
> 日期: 2026-06-26
> 状态: 已验证 (574 tests, 3 CI workflows green)

---

## 一、环境要求

| 组件 | 版本 | 必需 |
|------|------|------|
| Python | 3.11+ | ✅ |
| Redis | 6.0+ | ✅ |
| Docker | 24.0+ | 推荐 (Workspace 沙箱) |
| Langfuse | Cloud 或自建 | 可选 (可观测性) |

## 二、环境变量清单

### 2.1 必需变量

```bash
# ── 认证 ──
export XRUNTIME_API_KEY_RECORDS='[
  {"key":"sk-admin","tenant_id":"acme","user_id":"admin","role":"admin","kb_ids":["kb1"],"active":true}
]'
export XRUNTIME_JWT_SECRET="your-jwt-secret-at-least-32-chars!!"

# ── 生产模式 ──
export XRUNTIME_PRODUCTION=1
export XRUNTIME_WORKSPACE_BACKEND=docker

# ── Redis ──
export XRUNTIME_STORAGE_REDIS_HOST=redis
export XRUNTIME_STORAGE_REDIS_PORT=6379
export XRUNTIME_STORAGE_REDIS_PASSWORD=your-redis-password
export XRUNTIME_MESSAGE_BUS_REDIS_HOST=redis
export XRUNTIME_MESSAGE_BUS_REDIS_PORT=6379
```

### 2.2 可选变量

```bash
# ── 限流 ──
export XRUNTIME_RATE_LIMIT=100/60

# ── Langfuse 可观测性 ──
export XRUNTIME_LANGFUSE_ENABLED=true
export XRUNTIME_LANGFUSE_HOST=https://cloud.langfuse.com
export XRUNTIME_LANGFUSE_PUBLIC_KEY=pk-lf-xxx
export XRUNTIME_LANGFUSE_SECRET_KEY=sk-lf-xxx

# ── 审计日志 ──
export XRUNTIME_AUDIT_ENABLED=true
export XRUNTIME_AUDIT_STORAGE=file

# ── 知识库 ──
export XRUNTIME_KNOWLEDGE_ENABLED=true
export XRUNTIME_KNOWLEDGE_BACKEND=llm_wiki

# ── 端口 ──
export XRUNTIME_PORT=8900
```

### 2.3 YAML 配置

```yaml
# xruntime.yaml
server:
  auth_enabled: true
  host: 0.0.0.0
  port: 8900

tenants:
  - id: acme
    name: "ACME Corp"

storage:
  redis_host: redis
  redis_port: 6379
  redis_password: "${REDIS_PASSWORD}"
  tenant_prefix: "xrt:{tid}:"

observability:
  langfuse_enabled: true
  langfuse_host: "https://cloud.langfuse.com"
  langfuse_public_key: "pk-lf-xxx"
  langfuse_secret_key: "sk-lf-xxx"
  audit_enabled: true
  audit_storage: file

knowledge:
  enabled: true
  backend: llm_wiki
  mode: both
  retrieval_top_k: 5

permission:
  default_role: viewer

enable_enterprise_middlewares: true

# ── 新增模块配置 ──
loop_detection:
  max_repeats: 3
  window_size: 10

llm_error_handling:
  max_retries: 3
  retry_delay: 1.0
  retry_backoff: 2.0
  max_delay: 30.0
  fallback_model: ""
  circuit_breaker_threshold: 5
  circuit_breaker_reset_time: 60.0
  timeout_seconds: 120.0
  timeout_retries: 2
  timeout_retry_delay: 5.0
```

## 三、部署步骤

### 3.1 Docker Compose 部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime

# 2. 配置环境
cp .env.example .env
# 编辑 .env 填入 API Key、JWT Secret、Redis 密码

# 3. 一键启动
docker compose up -d

# 4. 验证
curl http://localhost:8900/health
curl http://localhost:8900/ready
```

### 3.2 裸金属部署

```bash
# 1. 安装依赖
pip install -e ".[xruntime-dev]"

# 2. 启动 Redis
docker run -d --name redis -p 6379:6379 \
  redis:7-alpine redis-server --requirepass your-password

# 3. 配置环境变量 (见上方)

# 4. 一键部署
./scripts/deploy.sh --docker

# 5. 健康检查
./scripts/health-check.sh
```

### 3.3 Kubernetes 部署

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xin-agent-runtime
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: runtime
          image: xin-agent-runtime:latest
          ports: [{containerPort: 8900}]
          env:
            - name: XRUNTIME_PRODUCTION
              value: "1"
            - name: XRUNTIME_WORKSPACE_BACKEND
              value: "docker"
            - name: XRUNTIME_API_KEY_RECORDS
              valueFrom:
                secretKeyRef:
                  name: xruntime-secrets
                  key: api-key-records
            - name: XRUNTIME_JWT_SECRET
              valueFrom:
                secretKeyRef:
                  name: xruntime-secrets
                  key: jwt-secret
            - name: XRUNTIME_LANGFUSE_ENABLED
              value: "true"
            - name: XRUNTIME_LANGFUSE_HOST
              value: "https://cloud.langfuse.com"
            - name: XRUNTIME_LANGFUSE_PUBLIC_KEY
              valueFrom:
                secretKeyRef:
                  name: langfuse-secrets
                  key: public-key
            - name: XRUNTIME_LANGFUSE_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: langfuse-secrets
                  key: secret-key
          livenessProbe:
            httpGet: {path: /health, port: 8900}
          readinessProbe:
            httpGet: {path: /ready, port: 8900}
```

## 四、中间件链

生产环境中 `create_xruntime_extension` 生成 7 个中间件，按顺序执行：

```
请求 → LangfuseTracer → LoopDetection → LLMErrorHandling →
       Audit → Quota → RBAC → SecretRedaction → Agent 执行
```

| 中间件 | 职责 | 配置项 |
|--------|------|--------|
| LangfuseTracer | trace model/tool 调用 | `langfuse_*` |
| LoopDetection | 防止重复工具调用死循环 | `loop_detection.*` |
| LLMErrorHandling | 重试+降级+熔断+超时 | `llm_error_handling.*` |
| Audit | 审计日志 | `audit_*` |
| Quota | 配额管控 | enterprise_middlewares |
| RBAC | 权限检查 | `permission.*` |
| SecretRedaction | Secret 脱敏 | enterprise_middlewares |

## 五、熔断器超时处理

LLMErrorHandling 中间件现在有**独立的超时处理分支**：

| 场景 | 行为 | 配置 |
|------|------|------|
| API 错误 (4xx/5xx) | 指数退避重试 max_retries 次 | `max_retries`, `retry_delay`, `retry_backoff` |
| 网络超时 | 固定延迟重试 timeout_retries 次 | `timeout_seconds`, `timeout_retries`, `timeout_retry_delay` |
| 重试耗尽 | 切换 fallback_model (如果有) | `fallback_model` |
| 连续失败 ≥ 阈值 | 熔断 OPEN，拒绝请求 | `circuit_breaker_threshold` |
| 熔断超时后 | HALF_OPEN，允许试探 | `circuit_breaker_reset_time` |
| 试探成功 | CLOSED，恢复正常 | - |

日志示例：
```
WARNING model call failed (attempt 1/3): RateLimitError. Retrying in 1.0s
WARNING model call TIMEOUT (attempt 1, 120s). Timeout retries: 1/2
ERROR   circuit breaker: CLOSED → OPEN (failures=5, threshold=5)
INFO    circuit breaker: OPEN → HALF_OPEN
INFO    circuit breaker: HALF_OPEN → CLOSED
```

## 六、验证清单

```bash
# 1. 服务健康
curl -sf http://localhost:8900/health && echo " OK"
curl -sf http://localhost:8900/ready && echo " OK"

# 2. 认证生效
curl -sf http://localhost:8900/v1/messages && echo "AUTH BYPASS!" || echo "Auth OK"

# 3. 测试通过
pytest tests/xruntime -q  # 预期: 574 passed

# 4. 健康检查脚本
./scripts/health-check.sh  # 预期: 42/42 PASS

# 5. Trace 验证
PYTHONPATH=src python3 scripts/verify_langfuse_trace.py

# 6. Lint
black --line-length=79 --check src/xruntime tests/xruntime
flake8 --extend-ignore=E203,W503,E704 src/xruntime
```

## 七、日志配置

```bash
# 启用 DEBUG 日志 (排查死循环/重试)
export PYTHONPATH=src
python3 -c "
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(name)s] %(levelname)s %(message)s',
)
"

# 日志 logger 列表:
# xruntime.middleware.loop_detection    — 循环检测
# xruntime.middleware.llm_error_handling — 错误处理/熔断
# xruntime.middleware.audit              — 审计
# xruntime.middleware.quota              — 配额
```

## 八、Langfuse 配置

### 方案 A: Langfuse Cloud (推荐，零运维)

1. 注册 https://cloud.langfuse.com (免费层支持 50k events/月)
2. 创建 Organization → Project
3. Settings → API Keys → 获取 Public Key + Secret Key
4. 配置环境变量:
   ```bash
   export XRUNTIME_LANGFUSE_ENABLED=true
   export XRUNTIME_LANGFUSE_HOST=https://cloud.langfuse.com
   export XRUNTIME_LANGFUSE_PUBLIC_KEY=pk-lf-xxx
   export XRUNTIME_LANGFUSE_SECRET_KEY=sk-lf-xxx
   pip install langfuse
   ```

### 方案 B: 本地 Langfuse (开发测试)

```bash
# 使用一键脚本 (精简 Docker 环境)
./scripts/start_langfuse.sh
```

### 查看 Trace

1. 打开 Langfuse 面板 (Cloud: https://cloud.langfuse.com, 本地: http://localhost:3000)
2. 左侧导航 → Tracing
3. 按 session_id / user_id / tenant_id 过滤
4. 查看 trace 详情: model generation + tool span + metadata

## 九、故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| 服务启动超时 | Redis 未就绪 | 检查 Redis 连接 |
| 401 Unauthorized | API Key 未配置 | 检查 `XRUNTIME_API_KEY_RECORDS` |
| Langfuse 无数据 | SDK 未安装 | `pip install langfuse` |
| 熔断器 OPEN | 连续失败过多 | 检查模型 API 可用性，等待 reset_time |
| 循环检测触发 | Agent 重复调用 | 检查 prompt，增加 `max_repeats` |
| 超时 | 模型响应慢 | 增加 `timeout_seconds` 或 `timeout_retries` |
