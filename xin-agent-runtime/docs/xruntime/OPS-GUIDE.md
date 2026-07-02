# Xin Agent Runtime 企业部署实践手册

> 版本: v1.0.0
> 更新日期: 2026-06-25
> 适用对象: 首次接触 Xin Agent Runtime 的开发运维人员

---

## 目录

1. [概述：Xin Agent Runtime 是什么](#1-概述xin-agent-runtime-是什么)
2. [架构与组件关系](#2-架构与组件关系)
3. [环境准备](#3-环境准备)
4. [配置详解](#4-配置详解)
5. [本地开发部署（Docker Compose）](#5-本地开发部署docker-compose)
6. [生产环境部署](#6-生产环境部署)
7. [Kubernetes 部署](#7-kubernetes-部署)
8. [安全配置](#8-安全配置)
9. [多租户配置](#9-多租户配置)
10. [模型供应商配置](#10-模型供应商配置)
11. [监控与可观测性](#11-监控与可观测性)
12. [健康检查与故障排查](#12-健康检查与故障排查)
13. [升级与回滚](#13-升级与回滚)
14. [常见问题 FAQ](#14-常见问题-faq)

---

## 1. 概述：Xin Agent Runtime 是什么

Xin Agent Runtime 是一个**企业级 Agent 开发运行时底座**，基于 AgentScope 执行内核与 XRuntime 企业扩展层联合开发。它提供：

- **三种协议适配**：Anthropic Messages API、Claude Code SDK、OpenCode SDK
- **企业中间件**：审计日志、配额管控、RBAC 权限、敏感数据脱敏、知识库注入
- **多租户隔离**：基于 Redis Key 前缀的租户级数据隔离 + 认证绑定
- **网关安全**：API Key / JWT 认证、滑动窗口限流、anti-spoofing
- **知识库治理**：LLM-Wiki AOT 编译 + BM25 检索 + per-KB ACL
- **Workspace 沙箱**：Local/Docker/E2B 后端 + 生产拒绝 local
- **可观测性**：OTel tracing、Prometheus 指标、Langfuse、结构化审计日志

**架构定位**：AgentScope 提供运行内核（Agent + ChatService + Storage），XRuntime 提供企业外壳（协议转换 + 安全 + 多租户 + 审计），二者联合构成 Xin Agent Runtime。

---

## 2. 架构与组件关系

```
┌─────────────────────────────────────────────────────────┐
│                    客户端请求                             │
│  (Anthropic SDK / Claude Code / OpenCode / curl)        │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP POST
                       ▼
┌─────────────────────────────────────────────────────────┐
│              XRuntime 网关层 (FastAPI)                   │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ AuthMiddleware│  │  RateLimiter │  │ProtocolAdapter│  │
│  │ (API Key 校验)│  │ (滑动窗口限流) │  │ (协议解析转换) │  │
│  └─────────────┘  └──────────────┘  └───────┬───────┘  │
│                                             │           │
│              ┌──────────────────────────────┘           │
│              ▼                                          │
│  ┌─────────────────────────────────────────────────┐   │
│  │        AS ChatService.run()                      │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐        │   │
│  │  │ Audit    │ │ Quota    │ │ RBAC     │        │   │
│  │  │Middleware│ │Middleware│ │Middleware│        │   │
│  │  └──────────┘ └──────────┘ └──────────┘        │   │
│  │  ┌──────────┐ ┌──────────────────────────┐     │   │
│  │  │Redaction │ │   Agent (LLM + Tools)    │     │   │
│  │  │Middleware│ │                          │     │   │
│  │  └──────────┘ └──────────────────────────┘     │   │
│  └─────────────────────────────────────────────────┘   │
└──────────────┬──────────────────────┬──────────────────┘
               │                      │
               ▼                      ▼
        ┌──────────┐          ┌──────────────┐
        │  Redis   │          │  Workspace   │
        │ (Storage │          │ (文件系统/    │
        │  + Bus)  │          │  Docker/E2B) │
        └──────────┘          └──────────────┘
```

**关键概念**：

| 概念 | 说明 |
|------|------|
| `create_app` | AS 的 FastAPI 工厂函数，创建带完整路由的应用 |
| `create_xruntime_extension()` | XRuntime 扩展工厂，返回中间件工厂 + 适配器注册表 |
| `mount_protocol_adapters()` | 把三个协议路由挂载到 AS app 上 |
| `build_xruntime_app()` | 一键组装 AS + XRuntime 的完整应用（服务器入口） |
| `MiddlewareStateCache` | 中间件状态缓存，确保配额/审计/RBAC 跨 turn 共享 |

---

## 3. 环境准备

### 3.1 系统要求

| 组件 | 最低版本 | 推荐版本 | 说明 |
|------|---------|---------|------|
| Python | 3.11 | 3.11.x | XRuntime 依赖 3.11+ 语法特性 |
| Redis | 6.0 | 7.x Alpine | 存储会话状态 + 消息总线 |
| Docker | 20.10 | 24.x+ | 容器化部署 |
| Docker Compose | v2 | v2.20+ | 多容器编排 |
| Kubernetes | 1.24 | 1.28+ | 生产级编排（可选） |

### 3.2 安装 Python 依赖

```bash
# 1. 克隆代码（或使用已有的代码目录）
git clone <your-repo-url> xin-agent-runtime
cd xin-agent-runtime/xin-agent-runtime

# 2. 创建虚拟环境（推荐 uv，也可以用 venv）
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装 XRuntime 及其开发依赖
#    xruntime-dev 包含: agentscope[dev] + claude-agent-sdk + pyyaml + pytest-asyncio + httpx
pip install -e ".[xruntime-dev]"

# 4. 验证安装
python -c "import xruntime; print('XRuntime version:', xruntime.__version__)"
```

**预期输出**：

```
XRuntime version: 0.1.0
```

### 3.3 准备 Redis

**方式一：Docker 快速启动（推荐本地开发）**

```bash
docker run -d \
  --name xruntime-redis \
  -p 6379:6379 \
  -v redis-data:/data \
  redis:7-alpine \
  redis-server --appendonly yes

# 验证 Redis 是否正常
docker exec xruntime-redis redis-cli ping
# 预期输出: PONG
```

**方式二：直接安装 Redis**

```bash
# macOS
brew install redis
brew services start redis

# Ubuntu/Debian
sudo apt-get install redis-server
sudo systemctl start redis-server

# 验证
redis-cli ping
# 预期输出: PONG
```

### 3.4 验证环境就绪

```bash
# 检查 Python
python --version
# 预期: Python 3.11.x

# 检查 Redis
redis-cli ping
# 预期: PONG

# 检查 XRuntime 导入
python -c "from xruntime._server import build_xruntime_app; print('OK')"
# 预期: OK
```

---

## 4. 配置详解

XRuntime 通过 **YAML 配置文件 + 环境变量覆盖** 双层配置。环境变量优先级高于 YAML。

### 4.1 配置文件结构

配置文件示例位于 `examples/xruntime/xruntime.yaml`，完整结构如下：

```yaml
# ========== 服务配置 ==========
server:
  host: "0.0.0.0"        # 监听地址
  port: 8900              # 监听端口
  auth_enabled: true       # 是否启用 API Key 认证

# ========== 存储配置 ==========
storage:
  backend: redis           # 目前仅支持 redis
  redis_host: localhost    # Redis 主机
  redis_port: 6379         # Redis 端口
  redis_db: 0              # Redis 数据库编号
  redis_password: null     # Redis 密码（无密码设为 null）
  tenant_prefix: "tenant:{tid}:"  # 多租户 Key 前缀模板

# ========== 消息总线配置 ==========
message_bus:
  backend: redis
  redis_host: localhost
  redis_port: 6379
  redis_db: 0
  tenant_prefix: "tenant:{tid}:"

# ========== 租户配置 ==========
tenants:
  - id: default            # 租户唯一标识
    name: Default Tenant   # 显示名称
  - id: acme
    name: ACME Corporation
    credentials: []        # 该租户可用的凭证 ID 列表

# ========== Agent 蓝图 ==========
agents:
  - name: code-engineer
    system_prompt: "You are a code engineering agent."
    model_config_name: claude-sonnet  # 引用下方 model_providers 的 key
    allowed_tools:          # 允许使用的工具
      - Read
      - Write
      - Edit
      - Bash
      - Glob
      - Grep
    disallowed_tools: []

# ========== 模型供应商 ==========
# 每个 key 对应 agents[].model_config_name 引用的配置名
model_providers:
  claude-sonnet:
    name: anthropic         # 供应商类型: anthropic/openai/dashscope/deepseek/moonshot/ollama/gemini/xai
    api_key: "sk-ant-..."   # API Key
    model: "claude-sonnet-4-20250514"  # 模型名称
    base_url: null           # 自定义 API 地址（null 用默认）
  gpt-4o:
    name: openai
    api_key: "sk-..."
    model: "gpt-4o"
    base_url: null

# ========== MCP 服务 ==========
mcps:
  - name: github
    transport: stdio        # stdio 或 http
    command: npx
    args: ["@github/mcp"]
    env: {}
  # - name: remote-mcp
  #   transport: http
  #   url: "https://mcp.example.com/sse"

# ========== Skill 目录 ==========
skills:
  - path: /path/to/skills
    scan_subdir: false

# ========== 权限配置 ==========
permission:
  mode: default            # default / accept_edits / explore / bypass / dont_ask
  rules: []

# ========== 插件 ==========
plugins: []
  # - name: my-plugin
  #   enabled: true
  #   config: {}

# ========== 可观测性 ==========
observability:
  otel_enabled: false       # 是否启用 OpenTelemetry
  otel_endpoint: ""         # OTLP 导出端点
  audit_enabled: true       # 是否启用审计日志
  audit_storage: file       # file 或 memory

# ========== 企业中间件开关 ==========
enable_enterprise_middlewares: true  # 是否注入审计/配额/RBAC/脱敏中间件
```

### 4.2 环境变量覆盖

所有 YAML 配置项都可通过环境变量覆盖，格式为 `XRUNTIME_<大节>_<字段>`：

| 配置项 | 环境变量 | 默认值 | 示例 |
|--------|---------|--------|------|
| server.host | `XRUNTIME_SERVER_HOST` | `0.0.0.0` | `export XRUNTIME_SERVER_HOST=127.0.0.1` |
| server.port | `XRUNTIME_SERVER_PORT` | `8900` | `export XRUNTIME_SERVER_PORT=9000` |
| server.auth_enabled | `XRUNTIME_SERVER_AUTH_ENABLED` | `true` | `export XRUNTIME_SERVER_AUTH_ENABLED=false` |
| storage.redis_host | `XRUNTIME_STORAGE_REDIS_HOST` | `localhost` | `export XRUNTIME_STORAGE_REDIS_HOST=redis` |
| storage.redis_port | `XRUNTIME_STORAGE_REDIS_PORT` | `6379` | `export XRUNTIME_STORAGE_REDIS_PORT=6380` |
| storage.redis_password | `XRUNTIME_STORAGE_REDIS_PASSWORD` | *(空)* | `export XRUNTIME_STORAGE_REDIS_PASSWORD=secret` |
| message_bus.redis_host | `XRUNTIME_MESSAGE_BUS_REDIS_HOST` | `localhost` | `export XRUNTIME_MESSAGE_BUS_REDIS_HOST=redis` |
| observability.audit_enabled | `XRUNTIME_OBSERVABILITY_AUDIT_ENABLED` | `true` | |
| observability.audit_storage | `XRUNTIME_OBSERVABILITY_AUDIT_STORAGE` | `file` | `export XRUNTIME_OBSERVABILITY_AUDIT_STORAGE=memory` |

**额外环境变量**（不在 YAML 中，仅环境变量）：

| 环境变量 | 说明 | 示例 |
|---------|------|------|
| `XRUNTIME_CONFIG_PATH` | YAML 配置文件路径 | `/etc/xruntime/config.yaml` |
| `XRUNTIME_API_KEYS` | API Key 列表（逗号分隔） | `key1,key2,key3` |
| `XRUNTIME_RATE_LIMIT` | 限流配置 `请求数/窗口秒` | `100/60` 表示 60 秒内最多 100 请求 |
| `XRUNTIME_AUDIT_DIR` | 审计日志目录（file 模式） | `/var/log/xruntime` |
| `XRUNTIME_MODEL_PROVIDER` | 默认模型供应商 | `anthropic` |
| `XRUNTIME_MODEL_API_KEY` | 默认模型 API Key | `sk-ant-...` |
| `XRUNTIME_MODEL_NAME` | 默认模型名称 | `claude-sonnet-4-20250514` |
| `XRUNTIME_MODEL_BASE_URL` | 默认模型 API 地址 | `https://api.anthropic.com` |

### 4.3 配置加载优先级

```
环境变量覆盖 > YAML 配置文件 > 代码默认值
```

**验证当前配置**：

```bash
python -c "
from xruntime._config import load_config
import json
config = load_config('examples/xruntime/xruntime.yaml')
print(json.dumps(config.model_dump(), indent=2, default=str))
"
```

---

## 5. 本地开发部署（Docker Compose）

这是最快速的启动方式，适合本地开发和功能验证。

### 5.1 使用内置 docker-compose

```bash
# 进入项目根目录
cd xin-agent-runtime/xin-agent-runtime

# 一键启动 XRuntime + Redis
docker compose -f deploy/docker-compose.yml up -d

# 查看容器状态
docker compose -f deploy/docker-compose.yml ps
```

**预期输出**：

```
NAME                  STATUS         PORTS
xin-agent-runtime...  Up 5 seconds   0.0.0.0:8900->8900/tcp
xin-agent-runtime...  Up 5 seconds   0.0.0.0:6379->6379/tcp
```

### 5.2 自定义配置启动

创建 `.env` 文件覆盖默认配置：

```bash
# .env 文件
XRUNTIME_STORAGE_REDIS_HOST=redis
XRUNTIME_MESSAGE_BUS_REDIS_HOST=redis
XRUNTIME_SERVER_AUTH_ENABLED=true
XRUNTIME_API_KEYS=dev-key-1,dev-key-2
XRUNTIME_MODEL_PROVIDER=anthropic
XRUNTIME_MODEL_API_KEY=sk-ant-your-key-here
XRUNTIME_MODEL_NAME=claude-sonnet-4-20250514
```

修改 `docker-compose.yml` 加载 `.env`：

```yaml
services:
  xruntime:
    # ... 其他配置不变 ...
    env_file:
      - .env
```

重新启动：

```bash
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml up -d
```

### 5.3 验证服务可用

```bash
# 1. 检查服务是否启动（OpenAPI 文档）
curl http://localhost:8900/docs

# 2. 发送测试请求（Anthropic 协议）
curl -X POST http://localhost:8900/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: dev-key-1" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 6. 生产环境部署

### 6.1 构建生产镜像

```bash
# 构建镜像
docker build -f deploy/Dockerfile -t xruntime:v0.2.0 .
docker tag xruntime:v0.2.0 xruntime:latest
```

**Dockerfile 说明**（`deploy/Dockerfile`）：

```dockerfile
FROM python:3.11-slim          # 基础镜像
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/                  # AS + XRuntime 源码
COPY conftest.py .
COPY tests/ tests/
RUN pip install --no-cache-dir -e ".[dev]" pyyaml  # 安装全部依赖
EXPOSE 8900
ENV XRUNTIME_SERVER_PORT=8900
CMD ["python", "-m", "xruntime._server"]  # 入口：build_xruntime_app → uvicorn
```

> **生产优化建议**：可创建多阶段构建镜像，分离构建依赖和运行时依赖以减小镜像体积。

### 6.2 生产级 docker-compose

创建 `deploy/docker-compose.prod.yml`：

```yaml
version: "3.9"

services:
  xruntime:
    image: xruntime:v0.2.0
    ports:
      - "${XRUNTIME_SERVER_PORT:-8900}:8900"
    environment:
      - XRUNTIME_STORAGE_REDIS_HOST=redis
      - XRUNTIME_MESSAGE_BUS_REDIS_HOST=redis
      - XRUNTIME_SERVER_AUTH_ENABLED=true
      - XRUNTIME_API_KEYS=${API_KEYS}
      - XRUNTIME_MODEL_PROVIDER=${MODEL_PROVIDER}
      - XRUNTIME_MODEL_API_KEY=${MODEL_API_KEY}
      - XRUNTIME_MODEL_NAME=${MODEL_NAME}
      - XRUNTIME_AUDIT_DIR=/var/log/xruntime
      - XRUNTIME_RATE_LIMIT=100/60
    volumes:
      - audit-logs:/var/log/xruntime
      - ./config.yaml:/app/config.yaml:ro
    depends_on:
      redis:
        condition: service_healthy
    restart: always
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8900/docs')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    restart: always
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  redis-data:
  audit-logs:
```

### 6.3 启动生产服务

```bash
# 创建环境文件
cat > .env.prod << 'EOF'
XRUNTIME_SERVER_PORT=8900
API_KEYS=prod-key-abc123,prod-key-def456
MODEL_PROVIDER=anthropic
MODEL_API_KEY=sk-ant-your-production-key
MODEL_NAME=claude-sonnet-4-20250514
EOF

# 启动
docker compose -f deploy/docker-compose.prod.yml --env-file .env.prod up -d

# 查看日志
docker compose -f deploy/docker-compose.prod.yml logs -f xruntime

# 查看审计日志
docker exec -it <container-id> cat /var/log/xruntime/audit_default.jsonl
```

---

## 7. Kubernetes 部署

### 7.1 使用内置 YAML 部署

项目自带 K8s 部署文件 `deploy/helm/xruntime.yaml`，包含 Deployment + Service + HPA：

```bash
# 部署到 K8s 集群
kubectl apply -f deploy/helm/xruntime.yaml

# 查看部署状态
kubectl get pods -l app=xruntime
kubectl get svc xruntime

# 预期输出
# NAME                        READY   STATUS    RESTARTS   AGE
# xruntime-xxx-yyy            1/1     Running   0          2m
# xruntime-xxx-zzz            1/1     Running   0          2m
```

### 7.2 创建 ConfigMap 和 Secret

```bash
# 1. 创建配置文件 ConfigMap
kubectl create configmap xruntime-config \
  --from-file=examples/xruntime/xruntime.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. 创建敏感信息 Secret
kubectl create secret generic xruntime-secrets \
  --from-literal=API_KEYS='prod-key-abc123' \
  --from-literal=MODEL_API_KEY='sk-ant-your-key' \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 7.3 完整生产级 K8s 部署

创建 `deploy/k8s/xruntime-prod.yaml`：

```yaml
---
# ConfigMap
apiVersion: v1
kind: ConfigMap
metadata:
  name: xruntime-config
data:
  xruntime.yaml: |
    server:
      host: "0.0.0.0"
      port: 8900
      auth_enabled: true
    storage:
      backend: redis
      redis_host: "redis-service"
      redis_port: 6379
      redis_db: 0
      tenant_prefix: "tenant:{tid}:"
    message_bus:
      backend: redis
      redis_host: "redis-service"
      redis_port: 6379
      tenant_prefix: "tenant:{tid}:"
    observability:
      otel_enabled: false
      audit_enabled: true
      audit_storage: file
    enable_enterprise_middlewares: true
    model_providers:
      claude-sonnet:
        name: anthropic
        api_key: "PLACEHOLDER"
        model: "claude-sonnet-4-20250514"
        base_url: null
---
# Secret
apiVersion: v1
kind: Secret
metadata:
  name: xruntime-secrets
type: Opaque
stringData:
  API_KEYS: "prod-key-abc123"
  MODEL_API_KEY: "sk-ant-your-production-key"
  REDIS_PASSWORD: ""
---
# XRuntime Deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xruntime
  labels:
    app: xruntime
spec:
  replicas: 3
  selector:
    matchLabels:
      app: xruntime
  template:
    metadata:
      labels:
        app: xruntime
    spec:
      containers:
        - name: xruntime
          image: xruntime:v0.2.0
          ports:
            - containerPort: 8900
          env:
            - name: XRUNTIME_CONFIG_PATH
              value: "/app/config/xruntime.yaml"
            - name: XRUNTIME_API_KEYS
              valueFrom:
                secretKeyRef:
                  name: xruntime-secrets
                  key: API_KEYS
            - name: XRUNTIME_MODEL_API_KEY
              valueFrom:
                secretKeyRef:
                  name: xruntime-secrets
                  key: MODEL_API_KEY
            - name: XRUNTIME_RATE_LIMIT
              value: "200/60"
            - name: XRUNTIME_AUDIT_DIR
              value: "/var/log/xruntime"
          volumeMounts:
            - name: config
              mountPath: /app/config
              readOnly: true
            - name: audit-logs
              mountPath: /var/log/xruntime
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          livenessProbe:
            httpGet:
              path: /docs
              port: 8900
            initialDelaySeconds: 15
            periodSeconds: 30
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /docs
              port: 8900
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 2
      volumes:
        - name: config
          configMap:
            name: xruntime-config
        - name: audit-logs
          emptyDir: {}
---
# Service
apiVersion: v1
kind: Service
metadata:
  name: xruntime-service
spec:
  selector:
    app: xruntime
  ports:
    - port: 80
      targetPort: 8900
      protocol: TCP
  type: ClusterIP
---
# Ingress
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: xruntime-ingress
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
spec:
  rules:
    - host: xruntime.your-company.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: xruntime-service
                port:
                  number: 80
---
# HPA
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: xruntime-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: xruntime
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
```

### 7.4 部署到集群

```bash
# 1. 确认集群可用
kubectl cluster-info

# 2. 创建命名空间（可选）
kubectl create namespace xruntime

# 3. 部署 Redis（推荐使用 Helm 部署生产级 Redis）
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install redis bitnami/redis \
  --namespace xruntime \
  --set architecture=standalone \
  --set auth.enabled=false \
  --set persistence.size=10Gi

# 4. 部署 XRuntime
kubectl apply -f deploy/k8s/xruntime-prod.yaml -n xruntime

# 5. 查看状态
kubectl get all -n xruntime

# 6. 端口转发测试
kubectl port-forward svc/xruntime-service 8900:80 -n xruntime
# 然后访问 http://localhost:8900/docs
```

---

## 8. 安全配置

### 8.1 API Key 认证

**原理**：所有非公开路由（`/health`、`/docs` 等除外）都需要在请求头中携带 `x-api-key`。

**配置方式**：

```bash
# 方式一：环境变量（逗号分隔多个 Key）
export XRUNTIME_API_KEYS="key-alpha,key-beta,key-gamma"

# 方式二：确保 server.auth_enabled=true（默认即 true）
export XRUNTIME_SERVER_AUTH_ENABLED=true
```

**验证**：

```bash
# 无 Key → 401
curl -X POST http://localhost:8900/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"hi"}]}'
# 预期: {"detail":"Invalid or missing API key"}

# 有 Key → 正常
curl -X POST http://localhost:8900/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: key-alpha" \
  -d '{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"hi"}]}'
```

### 8.2 限流配置

```bash
# 格式: 最大请求数/窗口秒数
# 以下表示 60 秒内每个客户端最多 100 个请求
export XRUNTIME_RATE_LIMIT="100/60"
```

超过限制的请求会收到 `429 Too Many Requests`。

### 8.3 RBAC 权限控制

XRuntime 内置两套默认角色：

| 角色 | 说明 | 工具权限 |
|------|------|---------|
| `admin` | 管理员（默认） | 允许所有工具（`*` → allow） |
| `viewer` | 只读用户 | 仅允许 `Read`、`Glob`、`Grep`，其余拒绝 |

**为会话分配角色**（通过 SDK 或代码）：

```python
from xruntime._gateway._mw_state import MiddlewareStateCache
from xruntime._config import XRuntimeConfig

# 获取扩展的 state_cache
ext = create_xruntime_extension(config=config)
state_cache = ext["middleware_state_cache"]

# 获取共享的 RBAC 中间件
rbac = await state_cache.get_rbac_middleware()

# 为特定会话分配 viewer 角色
rbac.assign_role("session-xxx", "viewer")

# 此后该会话的工具调用将受 RBAC 约束
# 尝试调用 Bash 会抛出 PermissionError
```

### 8.4 审计日志

审计日志记录每次工具调用的完整信息：谁、调用了什么工具、输入参数、结果、耗时。

**配置**：

```yaml
observability:
  audit_enabled: true       # 开启审计
  audit_storage: file       # file 持久化 或 memory 内存
```

```bash
# 指定审计日志目录（file 模式）
export XRUNTIME_AUDIT_DIR=/var/log/xruntime
```

**审计日志格式**（JSONL，每行一条）：

```json
{
  "timestamp": "2026-06-24T10:30:00.123456",
  "tenant_id": "acme",
  "user_id": "user-001",
  "session_id": "sess-abc",
  "tool_name": "Bash",
  "tool_input": {"command": "ls -la"},
  "decision": "ALLOW",
  "result": "success",
  "duration_ms": 152
}
```

> **注意**：当 `audit_storage: file` 但目录不可写时，会自动降级为内存模式并输出警告日志。

### 8.5 敏感数据脱敏

`SecretRedactionMiddleware` 自动在工具输入/输出中脱敏：

- API Key（`sk-`、`sk-ant-` 前缀）
- 密码字段
- Token 类凭证

无需配置，默认启用。

---

## 9. 多租户配置

### 9.1 概念

XRuntime 通过 Redis Key 前缀实现租户隔离：

```
tenant:acme:agentscope:user:alice:session:s1   ← ACME 租户的数据
tenant:globex:agentscope:user:alice:session:s1  ← Globex 租户的数据（互不可见）
```

### 9.2 配置租户

```yaml
tenants:
  - id: default
    name: Default Tenant
  - id: acme
    name: ACME Corporation
    credentials:
      - cred-anthropic-acme
  - id: globex
    name: Globex Inc
    credentials: []
```

### 9.3 请求中指定租户

客户端在请求中携带 `tenant_id`：

```bash
# Anthropic 协议：通过 header
curl -X POST http://localhost:8900/v1/messages \
  -H "x-api-key: your-key" \
  -H "x-tenant-id: acme" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"hi"}]}'
```

```python
# SDK 方式
from xruntime_sdk import create_client

client = create_client(
    "http://localhost:8900",
    tenant_id="acme",        # ← 指定租户
    api_key="your-key",
)
```

### 9.4 租户上下文安全

XRuntime 使用 `contextvars.ContextVar` 实现租户上下文隔离，确保并发请求不会互相污染租户信息：

```python
from xruntime._infra._tenant import TenantContext

ctx = TenantContext(default_tenant="default")

# 在 async 请求处理中
async def handle_request(tenant_id: str):
    with ctx.tenant(tenant_id):
        # 此上下文内所有代码看到的都是 tenant_id
        assert ctx.get() == tenant_id
    # 退出后自动恢复
    assert ctx.get() == "default"
```

> **重要**：`TenantContext` 是协程安全的。每个 asyncio Task 会获得独立的上下文副本，不会出现一个请求覆盖另一个请求租户的情况。

---

## 10. 模型供应商配置

### 10.1 支持的供应商

| 供应商 | `name` 值 | 典型模型 |
|--------|----------|---------|
| Anthropic | `anthropic` | `claude-sonnet-4-20250514` |
| OpenAI | `openai` | `gpt-4o`、`gpt-4o-mini` |
| 通义千问 | `dashscope` | `qwen-max` |
| DeepSeek | `deepseek` | `deepseek-chat` |
| Moonshot | `moonshot` | `moonshot-v1-128k` |
| Ollama | `ollama` | `llama3:8b` |
| Gemini | `gemini` | `gemini-2.0-flash` |
| xAI | `xai` | `grok-2` |

### 10.2 配置方式

**方式一：YAML 配置（推荐）**

```yaml
model_providers:
  claude-sonnet:
    name: anthropic
    api_key: "sk-ant-your-key"
    model: "claude-sonnet-4-20250514"
    base_url: null        # null 表示使用默认 API 地址

  gpt-4o:
    name: openai
    api_key: "sk-your-key"
    model: "gpt-4o"
    base_url: null

  local-llama:
    name: ollama
    api_key: ""            # Ollama 不需要 API Key
    model: "llama3:8b"
    base_url: "http://localhost:11434"
```

在 Agent 蓝图中引用：

```yaml
agents:
  - name: code-engineer
    model_config_name: claude-sonnet  # ← 引用 model_providers 的 key
```

**方式二：环境变量（适合 CI/CD）**

```bash
export XRUNTIME_MODEL_PROVIDER=anthropic
export XRUNTIME_MODEL_API_KEY=sk-ant-your-key
export XRUNTIME_MODEL_NAME=claude-sonnet-4-20250514
export XRUNTIME_MODEL_BASE_URL=https://api.anthropic.com  # 可选
```

> 环境变量配置的是**默认模型**，所有未显式指定模型的 Agent 都会使用它。

### 10.3 模型解析优先级

```
Agent 蓝图中的 model_config_name → YAML model_providers → 环境变量 XRUNTIME_MODEL_*
```

---

## 11. 监控与可观测性

### 11.1 Prometheus 指标

`MetricsCollector` 在内存中采集以下指标，可通过 `app.state.metrics` 访问：

| 指标 | 类型 | 说明 |
|------|------|------|
| `active_sessions` | Gauge | 每租户活跃会话数 |
| `tool_calls` | Histogram | 工具调用次数和延迟 |
| `tokens` | Counter | 每租户 token 消耗（输入/输出） |

```python
# 从 app.state 获取指标
metrics = app.state.metrics

# 查看活跃会话
print(metrics.active_sessions("acme"))

# 导出 Prometheus 格式
# (需自行实现 /metrics 端点暴露)
```

### 11.2 OpenTelemetry 链路追踪

```yaml
observability:
  otel_enabled: true
  otel_endpoint: "http://otel-collector:4317"
```

### 11.3 审计日志查询

```bash
# 查看审计日志（file 模式）
cat /var/log/xruntime/audit_acme.jsonl | python -m json.tool

# 按工具名过滤
grep '"tool_name": "Bash"' /var/log/xruntime/audit_acme.jsonl

# 统计工具调用次数
cat /var/log/xruntime/audit_acme.jsonl | \
  python -c "
import sys, json, collections
counter = collections.Counter()
for line in sys.stdin:
    entry = json.loads(line)
    counter[entry['tool_name']] += 1
for tool, count in counter.most_common():
    print(f'{tool}: {count}')
"
```

---

## 12. 健康检查与故障排查

### 12.1 服务健康检查

```bash
# 检查 OpenAPI 文档是否可访问（最基本的服务存活检查）
curl -sf http://localhost:8900/docs > /dev/null && echo "ALIVE" || echo "DOWN"

# 检查 Redis 连接
redis-cli -h <redis-host> -p <redis-port> ping
```

### 12.2 常见故障排查

#### 故障 1：服务启动失败 — `ImportError: No module named 'xruntime'`

```bash
# 原因：未安装包或虚拟环境未激活
# 解决：
pip install -e ".[xruntime-dev]"
python -c "import xruntime"  # 验证
```

#### 故障 2：Redis 连接失败 — `ConnectionRefusedError`

```bash
# 检查 Redis 是否运行
redis-cli ping

# 如果用 Docker，检查容器状态
docker ps | grep redis

# 检查配置的主机和端口
echo $XRUNTIME_STORAGE_REDIS_HOST
echo $XRUNTIME_STORAGE_REDIS_PORT
```

#### 故障 3：`PermissionError: [Errno 13] Permission denied: '/var/log/xruntime'`

```bash
# 原因：审计日志目录不可写
# 解决方式一：创建目录并赋权
sudo mkdir -p /var/log/xruntime
sudo chown $(whoami) /var/log/xruntime

# 解决方式二：指定可写目录
export XRUNTIME_AUDIT_DIR=/tmp/xruntime-logs

# 解决方式三：降级为内存模式（不推荐生产）
export XRUNTIME_OBSERVABILITY_AUDIT_STORAGE=memory
```

#### 故障 4：`401 Invalid or missing API key`

```bash
# 检查是否设置了 API Keys
echo $XRUNTIME_API_KEYS

# 如果为空，要么设置 Keys，要么关闭认证（仅开发环境）
export XRUNTIME_API_KEYS="your-key"
# 或
export XRUNTIME_SERVER_AUTH_ENABLED=false
```

#### 故障 5：模型调用失败 — `ModuleNotFoundError`

```bash
# 原因：对应模型供应商的依赖未安装
# 例如使用 Gemini 需要：
pip install "agentscope[gemini]"

# 使用 Ollama 需要：
pip install "agentscope[ollama]"

# 全量安装：
pip install -e ".[xruntime-dev]"
```

#### 故障 6：Docker 容器启动后立即退出

```bash
# 查看容器日志
docker logs <container-id>

# 常见原因：
# 1. Redis 未启动 → 检查 depends_on 或先启动 Redis
# 2. 配置文件路径错误 → 检查 XRUNTIME_CONFIG_PATH
# 3. 端口冲突 → 检查 8900 端口是否被占用
lsof -i :8900
```

### 12.3 查看日志

```bash
# Docker 部署
docker compose -f deploy/docker-compose.yml logs -f xruntime

# Kubernetes 部署
kubectl logs -f deployment/xruntime -n xruntime

# 直接运行
python -m xruntime._server 2>&1 | tee server.log
```

---

## 13. 升级与回滚

### 13.1 Docker 环境升级

```bash
# 1. 拉取新版本代码
git pull origin main

# 2. 构建新镜像
docker build -f deploy/Dockerfile -t xruntime:v0.3.0 .

# 3. 更新 docker-compose.yml 中的镜像版本
#    image: xruntime:v0.3.0

# 4. 滚动更新
docker compose -f deploy/docker-compose.yml up -d

# 5. 验证
curl http://localhost:8900/docs
```

### 13.2 Kubernetes 滚动更新

```bash
# 1. 更新镜像
kubectl set image deployment/xruntime \
  xruntime=xruntime:v0.3.0 \
  -n xruntime

# 2. 观察滚动更新
kubectl rollout status deployment/xruntime -n xruntime

# 3. 如果出问题，回滚
kubectl rollout undo deployment/xruntime -n xruntime

# 4. 查看历史版本
kubectl rollout history deployment/xruntime -n xruntime
```

### 13.3 数据备份

```bash
# Redis 数据备份
redis-cli -h <host> -p <port> BGSAVE
# 备份文件默认在 Redis 数据目录: dump.rdb

# Docker 卷备份
docker run --rm -v redis-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/redis-backup-$(date +%Y%m%d).tar.gz /data

# 审计日志备份
cp -r /var/log/xruntime /backup/xruntime-logs-$(date +%Y%m%d)
```

---

## 14. 常见问题 FAQ

### Q1: XRuntime 可以脱离 AgentScope 独立运行吗？

**不能。** XRuntime 是 AS 的扩展层，必须通过 `create_xruntime_extension()` + AS `create_app()` 组装使用。服务器入口 `build_xruntime_app()` 已自动完成这一组装。

### Q2: 如何在不停机的情况下更换 API Key？

```bash
# K8s 环境：更新 Secret 后重启 Pod
kubectl patch secret xruntime-secrets \
  -p '{"stringData":{"API_KEYS":"new-key-1,new-key-2"}}' \
  -n xruntime
kubectl rollout restart deployment/xruntime -n xruntime

# Docker 环境：更新 .env 后重启
docker compose -f deploy/docker-compose.prod.yml --env-file .env.prod up -d
```

### Q3: 如何为不同团队配置不同的模型？

```yaml
tenants:
  - id: team-a
    name: Team A (使用 Claude)
  - id: team-b
    name: Team B (使用 GPT-4)

model_providers:
  claude-sonnet:
    name: anthropic
    api_key: "sk-ant-team-a-key"
    model: "claude-sonnet-4-20250514"
  gpt-4o:
    name: openai
    api_key: "sk-team-b-key"
    model: "gpt-4o"

agents:
  - name: agent-team-a
    model_config_name: claude-sonnet
  - name: agent-team-b
    model_config_name: gpt-4o
```

### Q4: 配额限制是怎么工作的？

配额按**会话维度**累计，跨 turn 持久化。默认配置为不限制（`None` = 无限）。如需自定义配额：

```python
from xruntime._runtime._middleware._quota import QuotaConfig, QuotaTracker

# 创建自定义配额
config = QuotaConfig(
    max_tokens=100000,      # 最多 10 万 token
    max_tool_calls=50,      # 最多 50 次工具调用
    max_cost_usd=5.0,       # 最多花费 5 美元
)

# 通过 state_cache 为特定会话设置
tracker = QuotaTracker(config)
state_cache._quota_trackers["session-xxx"] = tracker
```

### Q5: 如何添加自定义协议适配器？

```python
from xruntime._gateway._adapter import ProtocolAdapter, AdapterRegistry
from xruntime._gateway._request import ProtocolType, XRuntimeRequest

class MyAdapter(ProtocolAdapter):
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.CUSTOM  # 需自行扩展枚举

    async def parse_request(self, raw, *, headers=None):
        return XRuntimeRequest(
            user_id=raw.get("user", "anonymous"),
            prompt=raw.get("query", ""),
            # ... 其他字段
        )

    async def serialize_event_stream(self, events):
        async for event in events:
            yield f"data: {event}\n\n".encode()

# 注册到扩展
registry = AdapterRegistry()
registry.register(MyAdapter())
ext = create_xruntime_extension(adapter_registry=registry)
```

### Q6: 审计日志能存到 Redis 吗？

当前版本支持 `file`（JSONL 文件）和 `memory`（内存列表）两种 sink。如需 Redis 存储，可继承 `AuditLogger` 自定义实现：

```python
from xruntime._runtime._middleware._audit import AuditLogger, AuditEntry

class RedisAuditLogger(AuditLogger):
    def __init__(self, redis_client):
        super().__init__(sink="redis")
        self.redis = redis_client

    async def log(self, entry: AuditEntry) -> None:
        import json
        self.redis.rpush(
            "audit:logs",
            json.dumps(entry.to_dict()),
        )
```

---

## 附录 A：快速启动检查清单

部署前逐项确认：

- [ ] Python 3.11+ 已安装
- [ ] Redis 7.x 已运行且可连接
- [ ] `pip install -e ".[xruntime-dev]"` 安装成功
- [ ] `python -c "import xruntime"` 无报错
- [ ] YAML 配置文件已创建（或使用环境变量）
- [ ] 模型供应商 API Key 已配置
- [ ] API Key 认证已配置（`XRUNTIME_API_KEYS`）
- [ ] 审计日志目录可写（`XRUNTIME_AUDIT_DIR`）
- [ ] 防火墙已开放 8900 端口
- [ ] Redis 持久化已开启（`--appendonly yes`）

## 附录 B：端点速查

| 路径 | 方法 | 需认证 | 说明 |
|------|------|--------|------|
| `/docs` | GET | 否 | Swagger UI OpenAPI 文档 |
| `/redoc` | GET | 否 | ReDoc OpenAPI 文档 |
| `/openapi.json` | GET | 否 | OpenAPI Schema JSON |
| `/v1/messages` | POST | 是 | Anthropic Messages API |
| `/v1/claude-code/query` | POST | 是 | Claude Code SDK 协议 |
| `/v1/opencode` | POST | 是 | OpenCode SDK 协议 |

## 附录 C：环境变量速查

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `XRUNTIME_CONFIG_PATH` | YAML 配置文件路径 | *(空，使用默认)* |
| `XRUNTIME_SERVER_HOST` | 监听地址 | `0.0.0.0` |
| `XRUNTIME_SERVER_PORT` | 监听端口 | `8900` |
| `XRUNTIME_SERVER_AUTH_ENABLED` | 启用 API Key 认证 | `true` |
| `XRUNTIME_STORAGE_REDIS_HOST` | Redis 主机 | `localhost` |
| `XRUNTIME_STORAGE_REDIS_PORT` | Redis 端口 | `6379` |
| `XRUNTIME_STORAGE_REDIS_PASSWORD` | Redis 密码 | *(空)* |
| `XRUNTIME_MESSAGE_BUS_REDIS_HOST` | 消息总线 Redis 主机 | `localhost` |
| `XRUNTIME_API_KEYS` | API Key 列表（逗号分隔） | *(空)* |
| `XRUNTIME_RATE_LIMIT` | 限流配置（`请求数/秒`） | *(空，不限流)* |
| `XRUNTIME_AUDIT_DIR` | 审计日志目录 | `/var/log/xruntime` |
| `XRUNTIME_MODEL_PROVIDER` | 默认模型供应商 | *(空)* |
| `XRUNTIME_MODEL_API_KEY` | 默认模型 API Key | *(空)* |
| `XRUNTIME_MODEL_NAME` | 默认模型名称 | *(空)* |
| `XRUNTIME_MODEL_BASE_URL` | 默认模型 API 地址 | *(空)* |
| `XRUNTIME_OBSERVABILITY_AUDIT_ENABLED` | 启用审计 | `true` |
| `XRUNTIME_OBSERVABILITY_AUDIT_STORAGE` | 审计存储方式 | `file` |
