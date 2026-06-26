# Xin Agent Runtime 生产快速启动指南

> 5 分钟从零到生产就绪

---

## 1. 前置准备

```bash
# 克隆代码
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime

# 安装
uv pip install -e ".[dev]"
pre-commit install
```

## 2. 初始化 Redis

### 2.1 Docker 快速启动

```bash
docker run -d \
  --name xruntime-redis \
  -p 6379:6379 \
  -v xruntime-redis-data:/data \
  redis:7-alpine \
  redis-server --requirepass your-redis-password --maxmemory 512mb --maxmemory-policy allkeys-lru
```

### 2.2 验证 Redis

```bash
redis-cli -a your-redis-password ping
# 预期输出: PONG
```

### 2.3 Redis 配置说明

| 配置项 | 说明 | 推荐值 |
|--------|------|--------|
| `--requirepass` | 认证密码 | 强密码 |
| `--maxmemory` | 最大内存 | 512mb+ |
| `--maxmemory-policy` | 淘汰策略 | allkeys-lru |
| `--appendonly` | 持久化 | yes（生产） |

## 3. 配置 RBAC 权限

### 3.1 创建 API Key 记录

Xin Agent Runtime 使用结构化 API Key 记录绑定租户、用户、角色和知识库权限。

```bash
# 生成 API Key
python3 -c "import secrets; print('sk-' + secrets.token_urlsafe(32))" | tee /tmp/admin_key
python3 -c "import secrets; print('sk-' + secrets.token_urlsafe(32))" | tee /tmp/viewer_key
python3 -c "import secrets; print('sk-' + secrets.token_urlsafe(32))" | tee /tmp/contrib_key
```

### 3.2 构建 API Key 记录 JSON

```bash
ADMIN_KEY=$(cat /tmp/admin_key)
VIEWER_KEY=$(cat /tmp/viewer_key)
CONTRIB_KEY=$(cat /tmp/contrib_key)

export XRUNTIME_API_KEY_RECORDS="[
  {
    \"key\": \"$ADMIN_KEY\",
    \"tenant_id\": \"acme\",
    \"user_id\": \"admin-alice\",
    \"role\": \"admin\",
    \"kb_ids\": [\"kb-docs\", \"kb-policies\"],
    \"key_id\": \"admin-001\",
    \"active\": true
  },
  {
    \"key\": \"$CONTRIB_KEY\",
    \"tenant_id\": \"acme\",
    \"user_id\": \"dev-bob\",
    \"role\": \"contributor\",
    \"kb_ids\": [\"kb-docs\"],
    \"active\": true
  },
  {
    \"key\": \"$VIEWER_KEY\",
    \"tenant_id\": \"acme\",
    \"user_id\": \"guest-charlie\",
    \"role\": \"viewer\",
    \"kb_ids\": [\"kb-docs\"],
    \"active\": true
  }
]"
```

### 3.3 RBAC 权限矩阵

| 角色 | search_knowledge | ingest_knowledge | 管理成员 | 删除租户 |
|------|:---:|:---:|:---:|:---:|
| **Owner** | ✅ | ✅ | ✅ | ✅ |
| **Admin** | ✅ | ✅ | ✅ | ❌ |
| **Contributor** | ✅ | ✅ | ❌ | ❌ |
| **Viewer** | ✅ | ❌ | ❌ | ❌ |

> **安全说明**: 默认角色是 `viewer`（最小权限）。未认证请求被拒绝 (401)。
> 客户端无法通过 header 伪造 tenant_id —— 认证 principal 的 tenant_id 强制覆盖。

### 3.4 JWT 配置（可选）

```bash
export XRUNTIME_JWT_SECRET="your-jwt-signing-secret-$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

## 4. 生产环境启动

### 4.1 设置环境变量

```bash
# === 认证 ===
export XRUNTIME_API_KEY_RECORDS="$XRUNTIME_API_KEY_RECORDS"
export XRUNTIME_JWT_SECRET="$XRUNTIME_JWT_SECRET"

# === Workspace 沙箱（生产必选 docker）===
export XRUNTIME_WORKSPACE_BACKEND=docker
export XRUNTIME_PRODUCTION=1
# 不设置 XRUNTIME_ALLOW_LOCAL_WORKSPACE → 生产拒绝 local

# === 限流 ===
export XRUNTIME_RATE_LIMIT=100/60

# === Redis ===
export XRUNTIME_STORAGE_REDIS_PASSWORD=your-redis-password
```

### 4.2 创建 YAML 配置

```bash
cat > xruntime.yaml << 'EOF'
server:
  auth_enabled: true
  port: 8900

tenants:
  - id: acme
    name: "ACME Corp"

storage:
  redis_host: localhost
  redis_port: 6379
  redis_password: your-redis-password
  tenant_prefix: "xrt:{tid}:"

knowledge:
  enabled: true
  backend: llm_wiki
  mode: both
  retrieval_top_k: 5

permission:
  default_role: viewer

enable_enterprise_middlewares: true
EOF

export XRUNTIME_CONFIG=xruntime.yaml
```

### 4.3 启动

```bash
python -m xruntime._server
```

### 4.4 验证

```bash
# 健康检查
curl http://localhost:8900/health
# {"status": "healthy"}

# 就绪检查
curl http://localhost:8900/ready
# {"status": "ready"}

# 认证测试（用 admin key）
ADMIN_KEY=$(cat /tmp/admin_key)
curl -H "x-api-key: $ADMIN_KEY" \
  http://localhost:8900/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet","max_tokens":100,"messages":[{"role":"user","content":"Hi"}]}'

# Viewer 尝试 ingest（应被拒绝）
VIEWER_KEY=$(cat /tmp/viewer_key)
curl -X POST -H "x-api-key: $VIEWER_KEY" \
  http://localhost:8900/v1/claude-code/query \
  -H "Content-Type: application/json" \
  -d '{"prompt":"ingest doc","tools":["ingest_knowledge"]}'
# 预期: check_permissions 拒绝 (viewer 无 doc:ingest 权限)
```

## 5. Docker Compose 一键启动

```bash
cat > docker-compose.yml << 'EOF'
version: "3.8"
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: redis-server --requirepass ${REDIS_PASSWORD} --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes: [redis-data:/data]
    restart: unless-stopped

  runtime:
    build: .
    ports: ["8900:8900"]
    environment:
      - XRUNTIME_PRODUCTION=1
      - XRUNTIME_WORKSPACE_BACKEND=docker
      - XRUNTIME_API_KEY_RECORDS=${API_KEY_RECORDS}
      - XRUNTIME_JWT_SECRET=${JWT_SECRET}
      - XRUNTIME_RATE_LIMIT=100/60
    depends_on: [redis]
    restart: unless-stopped

volumes:
  redis-data:
EOF

# 创建 .env
cat > .env << EOF
REDIS_PASSWORD=your-redis-password
JWT_SECRET=your-jwt-secret
API_KEY_RECORDS=$XRUNTIME_API_KEY_RECORDS
EOF

# 启动
docker-compose up -d

# 验证
curl http://localhost:8900/health
```

## 6. 安全检查清单

启动后逐项确认：

```bash
# [1] 生产模式已开启
echo $XRUNTIME_PRODUCTION
# 预期: 1

# [2] Workspace 后端是 docker（不是 local）
echo $XRUNTIME_WORKSPACE_BACKEND
# 预期: docker

# [3] API Key 已配置
echo $XRUNTIME_API_KEY_RECORDS | python3 -c "import sys,json; print(len(json.load(sys.stdin)),'keys configured')"
# 预期: 3 keys configured

# [4] JWT 密钥已设置
test -n "$XRUNTIME_JWT_SECRET" && echo "JWT OK" || echo "JWT MISSING"

# [5] 限流已配置
echo $XRUNTIME_RATE_LIMIT
# 预期: 100/60

# [6] 健康检查通过
curl -sf http://localhost:8900/health && echo " Health OK"
curl -sf http://localhost:8900/ready && echo " Ready OK"

# [7] 认证生效（无 key → 401）
curl -sf http://localhost:8900/v1/messages && echo "AUTH BYPASS!" || echo "Auth OK (401)"

# [8] 测试套件通过
pytest tests/xruntime -q
# 预期: 446 passed
```

## 7. 常见问题

| 问题 | 解决 |
|------|------|
| `LocalWorkspace not allowed in production` | `export XRUNTIME_WORKSPACE_BACKEND=docker` |
| `401 Authentication is enabled but no API keys` | 检查 `XRUNTIME_API_KEY_RECORDS` JSON 格式 |
| `429 Too Many Requests` | 调高 `XRUNTIME_RATE_LIMIT` |
| Redis connection refused | 确认 Redis 已启动且密码正确 |
| `Path traversal detected` | tenant_id 不能含 `..` 或 `/` |

---

> 完整部署文档: [DEPLOYMENT-GUIDE.md](./DEPLOYMENT-GUIDE.md)
> 安全架构: [FINAL-SECURITY-ARCHITECTURE.md](./FINAL-SECURITY-ARCHITECTURE.md)
> 覆盖率报告: [COVERAGE-REPORT.md](./COVERAGE-REPORT.md)
