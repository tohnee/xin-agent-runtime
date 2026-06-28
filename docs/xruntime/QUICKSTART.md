# XRuntime 快速启动指南

## 🚀 5 分钟上手

### 前置条件

- Python 3.11+
- Redis 7+ (或 Docker)
- Docker (生产部署推荐)

---

## 📦 开发环境启动

### 1. 安装依赖

```bash
cd xin-agent-runtime
uv pip install -e ".[xruntime-dev]"
```

### 2. 启动 Redis

```bash
# 使用 Docker 启动 Redis
docker run -d -p 6379:6379 --name xin-redis redis:7-alpine

# 或使用本地 Redis（需已安装）
redis-server --daemonize yes
```

### 3. 运行最小化配置测试

```bash
# 运行基础测试，验证环境正常
pytest tests/xruntime/test_config.py -v
pytest tests/xruntime/test_extension.py -v
```

### 4. 启动 XRuntime Gateway

```bash
# 开发模式 - 使用 local workspace
XRUNTIME_PRODUCTION=0 \
XRUNTIME_WORKSPACE_BACKEND=local \
XRUNTIME_STORAGE_REDIS_HOST=localhost \
python -m xruntime._server
```

服务启动在 `http://localhost:8900`

---

## 🔌 三种接入协议

### 1. Anthropic Messages API 兼容

```python
import requests

response = requests.post(
    "http://localhost:8900/v1/messages",
    headers={"x-api-key": "your-api-key"},
    json={
        "model": "claude-3-sonnet",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello!"}]
    }
)
print(response.json())
```

### 2. Claude Code SDK

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://localhost:8900",
    api_key="your-api-key"
)

# 支持 workspace sandbox 配置
response = client.messages.create(
    model="claude-3-sonnet",
    max_tokens=1024,
    messages=[{"role": "user", "content": "List files"}],
    metadata={
        "sandbox": "docker",
        "max_budget_usd": 5.0
    }
)
```

### 3. OpenCode Protocol

```python
import requests

response = requests.post(
    "http://localhost:8900/v1/opencode",
    headers={"x-api-key": "your-api-key"},
    json={
        "agent": "coder",
        "inputs": {
            "task": "Write a hello world in Python",
            "workspace": "/tmp/project"
        },
        "allowed_tools": ["edit_file", "run_command"]
    }
)
```

---

## 🛡️ 多租户与 RBAC

### 租户隔离

```python
from xruntime._gateway._extension import create_xruntime_extension

# 每个租户独立的资源命名空间
ext = create_xruntime_extension(
    tenant_id="acme-corp",
    membership_store=my_membership_store
)
```

### 角色权限矩阵

| 角色 | 搜索知识 | 写入文档 | 管理成员 | 删除租户 | 工具执行 |
|------|---------|---------|---------|---------|---------|
| **Viewer** | ✅ | ❌ | ❌ | ❌ | ✅ |
| **Contributor** | ✅ | ✅ | ❌ | ❌ | ✅ |
| **Admin** | ✅ | ✅ | ✅ | ❌ | ✅ |
| **Owner** | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 📚 知识库使用

### 1. 初始化 LLM-Wiki 后端

```python
from xruntime._runtime._knowledge._llm_wiki_adapter import LlmWikiAdapter

adapter = LlmWikiAdapter(
    config=type('Config', (), {
        'raw_dir': './data/raw',
        'compiled_dir': './data/compiled',
        'auto_compile': True
    })()
)
```

### 2. 注入知识中间件

```python
ext = create_xruntime_extension(
    knowledge_enabled=True,
    knowledge_backend="llm_wiki",
    knowledge_kb_ids=["kb-product-docs", "kb-api-reference"],
    knowledge_scope="tenant"
)
```

---

## 🔍 日志与监控

### 异常日志分析

```bash
# 使用内置分析脚本
python scripts/xruntime_log_analyzer.py /var/log/xruntime/gateway.log

# JSON 输出
python scripts/xruntime_log_analyzer.py /var/log/xruntime/gateway.log -f json

# 告警模式（超过阈值非零退出）
python scripts/xruntime_log_analyzer.py /var/log/xruntime/gateway.log --alert --threshold 10
```

### 关键日志标记

| 标记 | 说明 | 级别 |
|------|------|------|
| `[RBAC-DENIED]` | 权限拒绝 | WARNING |
| `[KNOWLEDGE-ERROR]` | 知识检索失败 | ERROR |
| `[QUOTA-EXCEEDED]` | 预算超限 | WARNING |
| `[AUTH-FAILED]` | 认证失败 | WARNING |

---

## 🐳 Docker Swarm 生产部署

### 1. 配置环境变量

```bash
cd deploy/
cp .env.example .env

# 编辑 .env
XRUNTIME_REDIS_PASSWORD=your-secure-redis-password
XRUNTIME_API_KEYS=key1,key2,key3
XRUNTIME_JWT_SECRET=your-jwt-secret-at-least-32-chars
```

### 2. 初始化 Swarm

```bash
# 初始化 manager 节点
docker swarm init

# 添加工作节点（可选）
docker swarm join --token SWMTKN-1-xxx manager-ip:2377
```

### 3. 标记节点

```bash
# 标记 Redis 节点
docker node update --label-add xruntime.redis=true node1

# 标记 Gateway 节点
docker node update --label-add xruntime.gateway=true node1
docker node update --label-add xruntime.gateway=true node2
docker node update --label-add xruntime.gateway=true node3

# 标记 LB/Metrics/Logs 节点
docker node update --label-add xruntime.lb=true node4
docker node update --label-add xruntime.metrics=true node1
docker node update --label-add xruntime.logs=true node1
```

### 4. 部署

```bash
docker stack deploy -c xruntime-swarm-stack.yml xruntime

# 查看服务
docker stack services xruntime

# 查看日志
docker service logs xruntime_gateway -f
```

---

## 📊 快速验证清单

部署后运行下列检查：

```bash
# 0. 部署预检查（推荐）
bash deploy/pre-deploy-check.sh

# 1. 健康检查
curl http://localhost:8900/health

# 2. API Key 认证
curl -H "x-api-key: your-key" http://localhost:8900/ready

# 3. 发送测试请求
curl -X POST http://localhost:8900/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-key" \
  -d '{
    "model": "claude-3-sonnet",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# 4. Admin API（需要 admin/owner 角色）
curl -H "x-api-key: your-admin-key" http://localhost:8900/admin/status

# 5. 运行完整测试套件（655 tests）
pytest tests/xruntime/ -q --tb=short
```

---

## � 安全注意事项

### JWT 认证

JWT 解析需要配置 `XRUNTIME_JWT_SECRET`。未配置 secret 时所有 JWT token 都会被拒绝（fail-closed）：

```bash
# 生成强密钥
openssl rand -hex 32

# 配置
export XRUNTIME_JWT_SECRET="your-generated-secret"
```

### Admin API 认证

所有 `/admin/*` 端点要求 `admin` 或 `owner` 角色的认证 principal。普通 API Key 用户无法访问。

### 路径穿越防护

- Workspace 路径中的 `tenant_id` / `session_id` 禁止包含 `..` `/`
- LLM-Wiki 的 `tenant_id` / `kb_id` 同样受路径穿越防护

### Rate Limiter 内存

RateLimiter 内置主动清理机制（`_MAX_TRACKED_CLIENTS = 10000`），防止一次性客户端导致内存无限增长。

---

## �🔧 常见问题排查

### Q: 启动报错 "Redis connection failed"

**A**: 检查 Redis 地址和密码：
```bash
redis-cli -h localhost -p 6379 -a your-password ping
```

### Q: Agent 无法调用工具，出现 RBAC DENIED

**A**: 检查用户角色是否有权限：
```python
# 查看用户角色
membership_store.resolve_principal(tenant_id, user_id)
```

### Q: Knowledge 检索无结果

**A**: 检查 KB ACL 和租户匹配：
```
[KNOWLEDGE-EMPTY] tenant=acme → KB 中无匹配内容
[KNOWLEDGE-ERROR] tenant=acme → 连接/索引问题
```

### Q: Workspace Docker 模式无法启动

**A**: 检查 Docker socket 权限：
```bash
ls -la /var/run/docker.sock
sudo chmod 666 /var/run/docker.sock  # 开发环境临时方案
```

---

## 📖 下一步

- 📄 [完整验收报告](./XRUNTIME-COMPLETE-ACCEPTANCE-REPORT.md)
- 📐 [架构设计文档](./ARCHITECTURE.md)
- 🛡️ [ADR 决策索引](../adr/)
- 🧪 [测试指南](./TESTING-GUIDE.md)

---

**有问题？** 查看 `docs/xruntime/` 下的其他文档，或提 Issue 到代码仓库。
