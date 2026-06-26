# Xin Agent Runtime 生产环境部署手册

> 版本: v1.0.0
> 日期: 2026-06-25
> 适用对象: 运维工程师、SRE、平台管理员

---

## 目录

1. [部署架构](#1-部署架构)
2. [环境要求](#2-环境要求)
3. [安装部署](#3-安装部署)
4. [配置详解](#4-配置详解)
5. [安全配置](#5-安全配置)
6. [多租户配置](#6-多租户配置)
7. [Docker 部署](#7-docker-部署)
8. [Kubernetes 部署](#8-kubernetes-部署)
9. [健康检查与监控](#9-健康检查与监控)
10. [故障排查](#10-故障排查)

---

## 1. 部署架构

```
                        ┌─────────────────────────────────────────┐
                        │           Load Balancer / Ingress       │
                        └────────────────┬────────────────────────┘
                                         │
                        ┌────────────────▼────────────────────────┐
                        │       Xin Agent Runtime (FastAPI)       │
                        │  ┌─────────────────────────────────┐    │
                        │  │  AuthMiddleware (API Key + JWT)  │    │
                        │  │  RateLimitMiddleware             │    │
                        │  │  Protocol Adapters (3 protocols) │    │
                        │  │  RuntimeExecutionPlan            │    │
                        │  │  Enterprise Middlewares          │    │
                        │  │    (Audit/Quota/RBAC/Redaction)  │    │
                        │  │  KnowledgeMiddleware             │    │
                        │  └─────────────────────────────────┘    │
                        └──────┬──────────────┬───────────────────┘
                               │              │
                    ┌──────────▼──┐   ┌───────▼────────┐
                    │   Redis     │   │   Workspace    │
                    │  (Storage + │   │  (Docker/E2B)  │
                    │  MessageBus)│   │                │
                    └─────────────┘   └────────────────┘
```

## 2. 环境要求

### 必需组件

| 组件 | 最低版本 | 用途 |
|------|----------|------|
| Python | 3.11+ | 运行时 |
| Redis | 6.0+ | 存储 + 消息总线 |
| Docker | 24.0+ | Workspace 沙箱（生产必选） |

### 可选组件

| 组件 | 用途 |
|------|------|
| Langfuse | LLM 追踪（默认 noop） |
| OpenTelemetry Collector | 分布式追踪 |
| Prometheus | 指标采集 |

## 3. 安装部署

### 3.1 从源码安装

```bash
# 克隆仓库
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime

# 安装（推荐使用 uv）
uv pip install -e ".[dev]"

# 或使用 pip
pip install -e ".[dev]"
```

### 3.2 验证安装

```bash
# 运行测试套件
pytest tests/xruntime -q

# 预期输出: 446 passed
```

## 4. 配置详解

### 4.1 环境变量

#### 认证配置

| 环境变量 | 说明 | 示例 |
|----------|------|------|
| `XRUNTIME_API_KEYS` | 逗号分隔的 API Key 列表 | `sk-key1,sk-key2` |
| `XRUNTIME_API_KEY_RECORDS` | JSON 格式的结构化 Key 记录 | 见下方示例 |
| `XRUNTIME_JWT_SECRET` | JWT 签名密钥 | `my-jwt-secret` |

**结构化 API Key 记录示例:**

```json
[
  {
    "key": "sk-admin-key",
    "tenant_id": "acme",
    "user_id": "alice",
    "role": "admin",
    "kb_ids": ["kb1", "kb2"],
    "key_id": "admin-key-001",
    "active": true
  },
  {
    "key": "sk-viewer-key",
    "tenant_id": "acme",
    "user_id": "bob",
    "role": "viewer",
    "kb_ids": ["kb1"],
    "active": true
  }
]
```

#### Workspace 配置

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `XRUNTIME_WORKSPACE_BACKEND` | 沙箱后端 (`local`/`docker`/`e2b`) | `local` |
| `XRUNTIME_WORKSPACE_DIR` | 本地 workspace 根目录 | `./xruntime-workspaces` |
| `XRUNTIME_PRODUCTION` | 生产模式 (`1`/`true`/`yes`) | 空 |
| `XRUNTIME_ALLOW_LOCAL_WORKSPACE` | 生产允许 local (`1`/`true`) | 空 |

#### 限流配置

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `XRUNTIME_RATE_LIMIT` | 速率限制 | 空（不限流） |

格式: `max_requests/window_seconds`，例如 `100/60` = 每分钟 100 请求。

### 4.2 YAML 配置文件

```yaml
# xruntime-config.yaml
server:
  auth_enabled: true
  host: 0.0.0.0
  port: 8900

tenants:
  - id: acme
    name: "ACME Corp"

storage:
  backend: redis
  redis_host: redis
  redis_port: 6379
  redis_db: 0
  redis_password: ${REDIS_PASSWORD}
  tenant_prefix: "xrt:{tid}:"

message_bus:
  redis_host: redis
  redis_port: 6379
  redis_db: 1

knowledge:
  enabled: true
  backend: llm_wiki
  mode: both
  retrieval_top_k: 5
  auto_compile: true

permission:
  default_role: viewer

observability:
  audit_enabled: true
  otel_enabled: false
  otel_endpoint: ""

enable_enterprise_middlewares: true
```

启动时指定配置文件:
```bash
XRUNTIME_CONFIG=xruntime-config.yaml python -m xruntime._server
```

## 5. 安全配置

### 5.1 生产环境安全清单

```bash
# === 必须设置 ===
export XRUNTIME_PRODUCTION=1
export XRUNTIME_WORKSPACE_BACKEND=docker
export XRUNTIME_API_KEY_RECORDS='[...]'
export XRUNTIME_JWT_SECRET='your-secure-jwt-secret'

# === 推荐设置 ===
export XRUNTIME_RATE_LIMIT=100/60

# === 不要设置（保持默认 false） ===
# XRUNTIME_ALLOW_LOCAL_WORKSPACE  ← 不设置 = 生产拒绝 local
```

### 5.2 RBAC 角色权限矩阵

| 角色 | KB 查询 | 文档录入 | 成员管理 | 租户删除 |
|------|---------|----------|----------|----------|
| Owner | ✅ | ✅ | ✅ | ✅ |
| Admin | ✅ | ✅ | ✅ | ❌ |
| Contributor | ✅ | ✅ | ❌ | ❌ |
| Viewer | ✅ | ❌ | ❌ | ❌ |

### 5.3 密钥脱敏

系统自动脱敏以下模式:
- API Key: `sk-[a-zA-Z0-9]{20,}` → `[REDACTED_API_KEY]`
- Bearer Token: `Bearer xxx` → `Bearer [REDACTED_TOKEN]`
- 私钥块: `-----BEGIN PRIVATE KEY-----` → `[REDACTED_PRIVATE_KEY]`

## 6. 多租户配置

### 6.1 租户隔离机制

- **Redis Key 隔离**: 每个 tenant 的 key 前缀为 `xrt:{tenant_id}:`
- **认证绑定**: API Key 绑定到特定 tenant_id，客户端无法伪造
- **Anti-spoofing**: 认证 principal 的 tenant_id 覆盖客户端 header 值
- **Workspace 隔离**: 路径包含 `tenants/{tenant_id}/sessions/{session_id}/`

### 6.2 添加租户

1. 在 YAML 配置中添加 tenant:
```yaml
tenants:
  - id: new-tenant
    name: "New Tenant Corp"
```

2. 为租户创建 API Key:
```bash
export XRUNTIME_API_KEY_RECORDS='[
  {"key":"sk-new-tenant-admin","tenant_id":"new-tenant","user_id":"admin1","role":"admin"},
  {"key":"sk-new-tenant-viewer","tenant_id":"new-tenant","user_id":"user1","role":"viewer"}
]'
```

3. 重启服务。

## 7. Docker 部署

### 7.1 Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# 克隆并安装
COPY . /app
RUN pip install --no-cache-dir -e ".[dev]"

# 暴露端口
EXPOSE 8900

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8900/health')"

# 启动
CMD ["python", "-m", "xruntime._server"]
```

### 7.2 Docker Compose

```yaml
# docker-compose.yml
version: "3.8"

services:
  xin-agent-runtime:
    build: .
    ports:
      - "8900:8900"
    environment:
      - XRUNTIME_PRODUCTION=1
      - XRUNTIME_WORKSPACE_BACKEND=docker
      - XRUNTIME_API_KEY_RECORDS=${API_KEY_RECORDS}
      - XRUNTIME_JWT_SECRET=${JWT_SECRET}
      - XRUNTIME_RATE_LIMIT=100/60
    depends_on:
      - redis
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    restart: unless-stopped

volumes:
  redis-data:
```

### 7.3 启动

```bash
# 创建 .env 文件
cat > .env << 'EOF'
API_KEY_RECORDS=[{"key":"sk-admin","tenant_id":"acme","user_id":"alice","role":"admin"}]
JWT_SECRET=your-secure-secret
EOF

# 启动
docker-compose up -d

# 验证
curl http://localhost:8900/health
curl http://localhost:8900/ready
```

## 8. Kubernetes 部署

### 8.1 Deployment

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
            httpGet:
              path: /health
              port: 8900
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /ready
              port: 8900
            initialDelaySeconds: 5
            periodSeconds: 10
```

### 8.2 Service

```yaml
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

### 8.3 Secret

```bash
kubectl create secret generic xruntime-secrets \
  --from-literal=api-key-records='[{"key":"sk-admin","tenant_id":"acme","user_id":"alice","role":"admin"}]' \
  --from-literal=jwt-secret='your-secure-secret'
```

## 9. 健康检查与监控

### 9.1 健康检查端点

| 端点 | 说明 |
|------|------|
| `GET /health` | 存活检查（不需要认证） |
| `GET /ready` | 就绪检查（不需要认证） |

```bash
# 存活检查
curl http://localhost:8900/health
# {"status": "healthy"}

# 就绪检查
curl http://localhost:8900/ready
# {"status": "ready"}
```

### 9.2 CI/CD 流水线

```bash
# Lint
flake8 --extend-ignore=E203 src/xruntime
black --line-length=79 --check src/xruntime

# 测试（覆盖率 ≥ 80%）
pytest tests/xruntime --cov=xruntime --cov-fail-under=80

# 安全门禁
pytest tests/xruntime/integration/test_workspace_rbac_integration.py -v
pytest tests/xruntime/test_phase1_security.py -v
pytest tests/xruntime/test_coverage_gaps.py -v
```

### 9.3 审计日志

知识操作审计日志位于:
```
{workspace_dir}/tenants/{tenant_id}/kbs/{kb_id}/audit/knowledge-audit.jsonl
```

每行一个 JSON 条目:
```json
{
  "timestamp": "2026-06-25T12:00:00",
  "action": "ingest",
  "tenant_id": "acme",
  "kb_id": "kb1",
  "source_id": "doc-001",
  "title": "Policy Document"
}
```

## 10. 故障排查

### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 401 Unauthorized | API Key 未配置或无效 | 检查 `XRUNTIME_API_KEY_RECORDS` |
| 403 LocalWorkspace rejected | 生产模式使用 local | 设置 `XRUNTIME_WORKSPACE_BACKEND=docker` |
| 429 Too Many Requests | 限流触发 | 调整 `XRUNTIME_RATE_LIMIT` |
| Redis connection refused | Redis 未启动 | 检查 Redis 连接配置 |
| ValueError: Path traversal | tenant_id 含非法字符 | 检查 tenant_id 格式 |

### 日志位置

| 日志 | 位置 |
|------|------|
| 应用日志 | stdout/stderr |
| 知识审计 | `{workspace}/tenants/{tid}/kbs/{kid}/audit/knowledge-audit.jsonl` |
| Manifest | `{workspace}/tenants/{tid}/kbs/{kid}/index/manifest.json` |
