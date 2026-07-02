# Xin Agent Runtime 生产部署指南

> 内核（AgentScope）+ 扩展层（XRuntime）联合部署

---

## 一、部署架构

```
                        ┌───────────────────────────────────┐
                        │        Load Balancer / Ingress     │
                        └───────────────┬───────────────────┘
                                        │
                        ┌───────────────▼───────────────────┐
                        │   Xin Agent Runtime (FastAPI)      │
                        │                                   │
                        │  ┌─────────────────────────────┐  │
                        │  │   XRuntime 企业扩展层        │  │
                        │  │  Auth / RBAC / Quota /       │  │
                        │  │  Knowledge / Workspace       │  │
                        │  └─────────────┬───────────────┘  │
                        │                │ 注入中间件         │
                        │  ┌─────────────▼───────────────┐  │
                        │  │   AgentScope 执行内核        │  │
                        │  │  Agent / Model / Tool /      │  │
                        │  │  ChatService / Storage        │  │
                        │  └─────────────────────────────┘  │
                        └───────┬───────────────┬───────────┘
                                │               │
                     ┌──────────▼──┐   ┌───────▼────────┐
                     │   Redis     │   │   Docker       │
                     │  Storage +  │   │  Workspace     │
                     │  MessageBus │   │  Sandbox       │
                     └─────────────┘   └────────────────┘
```

## 二、环境要求

### 必需组件

| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 运行时 |
| Redis | 6.0+ | AgentScope Storage + MessageBus |
| Docker | 24.0+ | XRuntime Workspace 沙箱（生产必选） |

### 可选组件

| 组件 | 用途 |
|------|------|
| Langfuse | LLM 追踪 |
| OTel Collector | 分布式追踪 |
| Prometheus | 指标采集 |

## 三、安装（内核 + 扩展层同时安装）

```bash
# 克隆仓库
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime

# 安装 — 一条命令同时安装 agentscope 内核和 xruntime 扩展
pip install -e ".[xruntime-dev]"

# 验证两个包都已安装
python -c "import agentscope; print('AgentScope:', agentscope.__version__)"
python -c "import xruntime; print('XRuntime:', xruntime.__version__)"
```

> `pip install -e ".[xruntime-dev]"` 会安装 `src/agentscope/` 和 `src/xruntime/` 两个包，因为 `pyproject.toml` 中 `xruntime-dev` 依赖 `agentscope[dev]`。

## 四、配置

### 4.1 内核配置（AgentScope）

AgentScope 内核通过 `create_app()` 参数配置：

| 参数 | 说明 | 生产推荐 |
|------|------|----------|
| `storage` | RedisStorage | Redis 6.0+，密码认证 |
| `message_bus` | RedisMessageBus | 同 Redis 实例，不同 db |
| `workspace_manager` | DockerWorkspaceManager | 容器隔离沙箱 |

### 4.2 扩展层配置（XRuntime）

XRuntime 通过环境变量 + YAML 配置：

```bash
# === 认证 ===
export XRUNTIME_API_KEY_RECORDS='[
  {"key":"sk-admin","tenant_id":"acme","user_id":"alice","role":"admin","kb_ids":["kb1"]}
]'
export XRUNTIME_JWT_SECRET="your-jwt-secret"

# === Workspace（生产必选 docker）===
export XRUNTIME_WORKSPACE_BACKEND=docker
export XRUNTIME_PRODUCTION=1

# === 限流 ===
export XRUNTIME_RATE_LIMIT=100/60
```

```yaml
# xruntime.yaml
server:
  auth_enabled: true
  port: 8900

tenants:
  - id: acme
    name: "ACME Corp"

storage:
  redis_host: redis
  redis_port: 6379
  redis_password: ${REDIS_PASSWORD}
  tenant_prefix: "xrt:{tid}:"

knowledge:
  enabled: true
  backend: llm_wiki
  mode: both
  retrieval_top_k: 5

permission:
  default_role: viewer

enable_enterprise_middlewares: true
```

### 4.3 完整启动配置

```bash
# Redis
docker run -d --name xruntime-redis \
  -p 6379:6379 \
  redis:7-alpine \
  redis-server --requirepass your-redis-password

# 环境变量
export XRUNTIME_CONFIG=xruntime.yaml
export XRUNTIME_API_KEY_RECORDS='[{"key":"sk-admin","tenant_id":"acme","user_id":"alice","role":"admin"}]'
export XRUNTIME_JWT_SECRET="your-jwt-secret"
export XRUNTIME_WORKSPACE_BACKEND=docker
export XRUNTIME_PRODUCTION=1
export XRUNTIME_RATE_LIMIT=100/60

# 启动（同时启动内核 + 扩展层）
python -m xruntime._server
```

### 4.4 启动链路

`python -m xruntime._server` 执行以下操作：

```
1. 加载 XRuntimeConfig (YAML + env)
2. 创建 XRuntime extension
    ├── 注册 3 个 Protocol Adapter
    ├── 创建企业中间件工厂
    └── 创建 MiddlewareStateCache
3. 创建 AgentScope Storage (RedisStorage + tenant prefix)
4. 创建 AgentScope MessageBus (RedisMessageBus)
5. 创建 WorkspaceManager (DockerWorkspaceManager via Factory)
6. 调用 AgentScope create_app()
    └── 注入 extra_agent_middlewares (企业中间件)
7. 挂载 AuthMiddleware + RateLimitMiddleware
8. 挂载 /health + /ready
9. 挂载协议路由 (/v1/messages, /v1/claude-code/query, /v1/opencode)
10. uvicorn 启动
```

## 五、Docker Compose 部署

```yaml
version: "3.8"
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes: [redis-data:/data]
    restart: unless-stopped

  runtime:
    build: .
    ports: ["8900:8900"]
    environment:
      - XRUNTIME_CONFIG=/app/xruntime.yaml
      - XRUNTIME_API_KEY_RECORDS=${API_KEY_RECORDS}
      - XRUNTIME_JWT_SECRET=${JWT_SECRET}
      - XRUNTIME_WORKSPACE_BACKEND=docker
      - XRUNTIME_PRODUCTION=1
      - XRUNTIME_RATE_LIMIT=100/60
    volumes:
      - ./xruntime.yaml:/app/xruntime.yaml
      - /var/run/docker.sock:/var/run/docker.sock  # Docker-in-Docker
    depends_on: [redis]
    restart: unless-stopped

volumes:
  redis-data:
```

```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
COPY . /app
RUN pip install --no-cache-dir -e ".[xruntime-dev]"
EXPOSE 8900
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8900/health')"
CMD ["python", "-m", "xruntime._server"]
```

```bash
# .env
REDIS_PASSWORD=your-redis-password
JWT_SECRET=your-jwt-secret
API_KEY_RECORDS=[{"key":"sk-admin","tenant_id":"acme","user_id":"alice","role":"admin"}]

# 启动
docker-compose up -d

# 验证
curl http://localhost:8900/health
```

## 六、Kubernetes 部署

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xin-agent-runtime
spec:
  replicas: 3
  selector:
    matchLabels:
      app: xin-agent-runtime
  template:
    metadata:
      labels:
        app: xin-agent-runtime
    spec:
      containers:
        - name: runtime
          image: xin-agent-runtime:latest
          ports:
            - containerPort: 8900
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
          livenessProbe:
            httpGet: { path: /health, port: 8900 }
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet: { path: /ready, port: 8900 }
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: xin-agent-runtime
spec:
  selector:
    app: xin-agent-runtime
  ports:
    - port: 80
      targetPort: 8900
  type: LoadBalancer
```

## 七、验证清单

```bash
# [1] 内核可用
python -c "from agentscope.agent import Agent; print('AgentScope OK')"

# [2] 扩展层可用
python -c "from xruntime import create_xruntime_extension; print('XRuntime OK')"

# [3] 生产模式
echo $XRUNTIME_PRODUCTION  # 预期: 1

# [4] Workspace 后端
echo $XRUNTIME_WORKSPACE_BACKEND  # 预期: docker

# [5] API Key 配置
echo $XRUNTIME_API_KEY_RECORDS | python -c "import sys,json; print(len(json.load(sys.stdin)),'keys')"

# [6] 健康检查
curl -sf http://localhost:8900/health && echo " Health OK"
curl -sf http://localhost:8900/ready && echo " Ready OK"

# [7] 认证生效（无 key → 401）
curl -sf http://localhost:8900/v1/messages && echo " AUTH BYPASS!" || echo " Auth OK (401)"

# [8] 测试通过
pytest tests/xruntime -q  # 预期: 446 passed
```

## 八、内核与扩展层的版本兼容

| 组件 | 版本管理 | 升级影响 |
|------|----------|----------|
| AgentScope | `src/agentscope/` 内嵌 | 升级需保证 `create_app`、`Agent`、`AgentEvent` 公共 API 不变 |
| XRuntime | `src/xruntime/` 内嵌 | 可独立升级，只要 AgentScope API 不变 |
| pyproject.toml | `name = "xin-agent-runtime"` | 一次 `pip install` 安装两个包 |

> 两个包在同一个仓库中同步发布，不存在版本不匹配问题。

---

> 更多文档: [架构说明](./ARCHITECTURE.md) | [快速启动](./QUICKSTART.md) | [CI/CD](./CI-CD-GUIDE.md) | [安全架构](./FINAL-SECURITY-ARCHITECTURE.md)
