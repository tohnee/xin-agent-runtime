# XRuntime 部署安装测试手册

> 以构建「企业级问答助手」为例，从零到上线的完整步骤指南。

---

## 目录

1. [项目概述](#1-项目概述)
2. [环境准备](#2-环境准备)
3. [安装 XRuntime](#3-安装-xruntime)
4. [配置企业问答助手](#4-配置企业问答助手)
5. [启动服务](#5-启动服务)
6. [测试验证](#6-测试验证)
7. [构建知识库](#7-构建知识库)
8. [接入客户端](#8-接入客户端)
9. [生产部署](#9-生产部署)
10. [运维监控](#10-运维监控)
11. [故障排查](#11-故障排查)

---

## 1. 项目概述

### 我们要构建什么

一个企业级 AI 问答助手，具备以下能力：
- 员工通过 API 调用 AI 助手回答问题
- AI 能查询公司内部知识库（产品文档、FAQ、规章制度）
- 不同部门有不同的知识库访问权限
- 所有操作有审计日志
- 有每日预算限制，防止滥用

### 技术架构

```
员工客户端 (curl/SDK)
       ↓
XRuntime Gateway (:8900)
  ├── Auth (API Key 认证)
  ├── RBAC (角色权限检查)
  ├── Knowledge (知识库检索)
  └── Quota (预算控制)
       ↓
AgentScope Agent (执行 AI 对话)
       ↓
LLM (Claude/GPT/GLM)
```

---

## 2. 环境准备

### 2.1 系统要求

| 组件 | 最低要求 | 推荐 |
|------|---------|------|
| 操作系统 | macOS / Linux | Ubuntu 22.04 |
| Python | 3.11+ | 3.11.7 |
| Redis | 7.0+ | 7.2 |
| Docker | 24+ | 29+ |
| 磁盘 | 10GB | 50GB+ |
| 内存 | 2GB | 4GB+ |

### 2.2 安装基础工具

```bash
# macOS
brew install python@3.11 redis docker
brew install uv

# Ubuntu
sudo apt update
sudo apt install python3.11 python3.11-venv redis-server docker.io
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2.3 验证安装

```bash
python3 --version       # 3.11.x
redis-server --version  # Redis 7.x
docker --version        # Docker 24+
```

---

## 3. 安装 XRuntime

### 3.1 克隆代码

```bash
git clone https://github.com/tohnee/xin-agent-runtime.git
cd xin-agent-runtime
```

### 3.2 安装依赖

```bash
python3.11 -m venv .venv
source .venv/bin/activate

# 使用 uv (推荐，更快)
uv pip install -e ".[xruntime-dev]"

# 或使用 pip
pip install -e ".[xruntime-dev]"
```

### 3.3 验证安装

```bash
# 导入检查
python3 -c "import xruntime; print(xruntime.__version__)"

# 基础测试
pytest tests/xruntime/test_config.py tests/xruntime/test_extension.py -v
# 预期: 21 passed
```

---

## 4. 配置企业问答助手

### 4.1 创建 YAML 配置

```bash
mkdir -p ~/.xruntime
cat > ~/.xruntime/qa-assistant.yaml << 'EOF'
server:
  host: "0.0.0.0"
  port: 8900
  auth_enabled: true

storage:
  backend: redis
  redis_host: localhost
  redis_port: 6379
  tenant_prefix: "tenant:{tid}:"

message_bus:
  backend: redis
  redis_host: localhost
  redis_port: 6379

tenants:
  - id: acme
    name: "ACME 公司"

agents:
  - name: qa-assistant
    system_prompt: |
      你是 ACME 公司的智能问答助手。
      请基于提供的知识库内容回答员工问题。
    model_config_name: default
    allowed_tools:
      - search_knowledge
    max_iters: 10

permission:
  mode: default
  default_role: viewer

knowledge:
  enabled: true
  backend: llm_wiki
  mode: static_control
  raw_dir: ./data/kb-raw
  compiled_dir: ./data/kb-compiled
  retrieval_top_k: 5
  auto_compile: true
  extra:
    scoped_layout: true

observability:
  audit_enabled: true
  audit_storage: file
EOF
```

### 4.2 配置环境变量

```bash
# 生成随机密钥
export XRUNTIME_API_KEYS="xrk-$(openssl rand -hex 16)"
export XRUNTIME_JWT_SECRET="jsk-$(openssl rand -hex 24)"
export XRUNTIME_CONFIG_PATH="$HOME/.xruntime/qa-assistant.yaml"

# 模型配置 (替换为你的真实 API Key)
export XRUNTIME_MODEL_PROVIDER=anthropic
export XRUNTIME_MODEL_API_KEY=sk-ant-xxxxx
export XRUNTIME_MODEL_NAME=claude-3-sonnet

# 保存到 .env
cat > .env << EOF
XRUNTIME_API_KEYS=$XRUNTIME_API_KEYS
XRUNTIME_JWT_SECRET=$XRUNTIME_JWT_SECRET
XRUNTIME_CONFIG_PATH=$XRUNTIME_CONFIG_PATH
XRUNTIME_MODEL_PROVIDER=$XRUNTIME_MODEL_PROVIDER
XRUNTIME_MODEL_API_KEY=$XRUNTIME_MODEL_API_KEY
XRUNTIME_MODEL_NAME=$XRUNTIME_MODEL_NAME
EOF

echo "你的 API Key: $XRUNTIME_API_KEYS"
```

---

## 5. 启动服务

### 5.1 启动 Redis

```bash
# 方式1: 直接启动
redis-server --daemonize yes

# 方式2: Docker
docker run -d -p 6379:6379 --name xruntime-redis redis:7-alpine

# 验证
redis-cli ping  # PONG
```

### 5.2 启动 XRuntime

```bash
source .env
python -m xruntime._server
```

预期输出：
```
INFO:     Uvicorn running on http://0.0.0.0:8900
```

### 5.3 验证服务

```bash
# 健康检查 (无需认证)
curl http://localhost:8900/health
# {"status":"ok"}

# 就绪检查 (需要 API Key)
curl -H "x-api-key: $XRUNTIME_API_KEYS" http://localhost:8900/ready
# {"status":"ready"}
```

---

## 6. 测试验证

### 6.1 运行完整测试套件

```bash
# 全部 655 个测试
pytest tests/xruntime/ -q --tb=short
# 预期: 655 passed, 18 skipped
```

### 6.2 分模块测试

```bash
# RBAC 权限测试 (22 个)
pytest tests/xruntime/test_rbac_policy.py -v

# 知识库测试
pytest tests/xruntime/test_knowledge.py tests/xruntime/test_knowledge_acl.py -v

# 协议适配器测试
pytest tests/xruntime/test_anthropic_adapter.py -v

# 集成测试
pytest tests/xruntime/integration/ -v
```

### 6.3 手动发送请求

```bash
curl -X POST http://localhost:8900/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $XRUNTIME_API_KEYS" \
  -d '{
    "model": "claude-3-sonnet",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

---

## 7. 构建知识库

### 7.1 准备文档

```bash
mkdir -p ./data/sources

cat > ./data/sources/product-faq.md << 'EOF'
# ACME 产品常见问题

## 退货政策
我们提供 30 天无理由退货服务。
- 商品需保持原包装完好
- 退货时请携带购买凭证
- 退款将在 3-5 个工作日内原路退回

## 保修服务
所有 ACME 产品享受 1 年免费保修。
- 保修期内非人为损坏免费维修
- 联系电话: 400-123-4567
EOF

cat > ./data/sources/company-rules.md << 'EOF'
# ACME 公司规章制度

## 考勤制度
- 工作时间: 9:00-18:00
- 弹性打卡: 8:30-9:30 之间到岗即可

## 请假流程
1. 在 OA 系统提交请假申请
2. 直属主管审批
3. 年假每年 10 天
EOF
```

### 7.2 导入脚本

```python
#!/usr/bin/env python3
"""导入知识库文档。"""
import asyncio
from xruntime._runtime._knowledge._llm_wiki_adapter import LlmWikiAdapter
from xruntime._runtime._knowledge._base import KnowledgeBaseConfig

async def main():
    config = KnowledgeBaseConfig(
        raw_dir="./data/kb-raw",
        compiled_dir="./data/kb-compiled",
        auto_compile=True,
        extra={"scoped_layout": True},
    )
    adapter = LlmWikiAdapter(config)
    await adapter.initialize()

    for filename, title in [
        ("product-faq.md", "产品常见问题"),
        ("company-rules.md", "公司规章制度"),
    ]:
        with open(f"./data/sources/{filename}", "r") as f:
            content = f.read()
        await adapter.ingest(
            source_id=filename.replace(".md", ""),
            content=content,
            title=title,
            source_type="markdown",
            metadata={"tenant_id": "acme", "kb_id": "product-docs"},
        )
        print(f"✓ {title} 已导入")

    count = await adapter.compile()
    print(f"✓ 编译完成，生成 {count} 个知识页面")

asyncio.run(main())
```

运行：
```bash
python3 import_knowledge.py
```

### 7.3 验证知识库

```bash
ls ./data/kb-raw/tenants/acme/kbs/product-docs/raw/
ls ./data/kb-compiled/tenants/acme/kbs/product-docs/wiki/
```

---

## 8. 接入客户端

### 8.1 curl 调用

```bash
curl -X POST http://localhost:8900/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $XRUNTIME_API_KEYS" \
  -d '{
    "model": "claude-3-sonnet",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "退货政策是什么？"}]
  }'
```

### 8.2 Python 客户端

```python
import requests

def ask(question: str) -> str:
    response = requests.post(
        "http://localhost:8900/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": "your-api-key",
        },
        json={
            "model": "claude-3-sonnet",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": question}],
        },
    )
    return response.json()

print(ask("请假流程是什么？"))
```

### 8.3 Anthropic SDK

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://localhost:8900",
    api_key="your-api-key",
)

response = client.messages.create(
    model="claude-3-sonnet",
    max_tokens=1024,
    messages=[{"role": "user", "content": "保修期多久？"}],
)
print(response.content[0].text)
```

---

## 9. 生产部署

### 9.1 一键部署

```bash
cd deploy/

# 1. 预检查
bash pre-deploy-check.sh

# 2. 准备配置
cp .env.production-example .env
# 编辑 .env 填入真实值

# 3. 部署
./deploy.sh --swarm
```

### 9.2 Docker Swarm 部署

```bash
docker swarm init
docker stack deploy -c deploy/xruntime-swarm-stack.yml xruntime
docker stack services xruntime
```

### 9.3 部署后验证

```bash
# 健康检查
curl http://localhost:8900/health

# Admin 状态 (需 admin 角色 API Key)
curl -H "x-api-key: $ADMIN_KEY" http://localhost:8900/admin/status

# Prometheus 指标
curl http://localhost:8900/metrics
```

---

## 10. 运维监控

### 10.1 系统状态

```bash
ADMIN_KEY="your-admin-key"

# 系统概览
curl -H "x-api-key: $ADMIN_KEY" http://localhost:8900/admin/status

# 模型列表
curl -H "x-api-key: $ADMIN_KEY" http://localhost:8900/admin/models

# 指标摘要
curl -H "x-api-key: $ADMIN_KEY" http://localhost:8900/admin/metrics/summary
```

### 10.2 日志分析

```bash
# 提取 RBAC 和 Knowledge 异常
python3 scripts/xruntime_log_analyzer.py /var/log/xruntime/xruntime.log

# JSON 格式
python3 scripts/xruntime_log_analyzer.py /var/log/xruntime/xruntime.log -f json
```

### 10.3 关键日志标记

| 标记 | 含义 | 级别 |
|---------|------|------|
| `[RBAC-DENIED]` | 权限拒绝 | WARNING |
| `[KNOWLEDGE-ERROR]` | 检索失败 | ERROR |
| `[QUOTA-EXCEEDED]` | 预算超限 | WARNING |

---

## 11. 故障排查

### Redis 连接失败

```bash
redis-cli ping  # 检查 Redis
docker start xruntime-redis  # 重启 Redis
```

### RBAC DENIED

检查用户角色权限。Viewer 不能写入文档，Contributor 不能管理成员。

### 知识检索无结果

```
[KNOWLEDGE-EMPTY] → 知识库中没有匹配内容，需要导入更多文档
[KNOWLEDGE-ERROR] → 连接/索引问题，检查目录权限
```

### 认证失败 (401)

```bash
# 检查 API Key 是否正确
echo $XRUNTIME_API_KEYS

# 确认请求 Header
curl -H "x-api-key: your-key" http://localhost:8900/ready
```

### Admin API 403

Admin 端点要求 `admin` 或 `owner` 角色。普通 API Key 用户无法访问。

---

## 附录：快速命令速查

| 操作 | 命令 |
|------|------|
| 启动开发服务 | `python -m xruntime._server` |
| 运行全部测试 | `pytest tests/xruntime/ -q` |
| 健康检查 | `curl localhost:8900/health` |
| 部署预检查 | `bash deploy/pre-deploy-check.sh` |
| 一键部署 | `./deploy/deploy.sh --swarm` |
| 查看服务状态 | `./deploy/deploy.sh --status` |
| 查看日志 | `./deploy/deploy.sh --logs` |
| 停止服务 | `./deploy/deploy.sh --down` |
| 日志分析 | `python3 scripts/xruntime_log_analyzer.py <logfile>` |
