# XRuntime 迁移指南

> 版本: v0.1.0
> 日期: 2026-06-23

## 概述

XRuntime v1 **不提供自动迁移**，旧 xruntime 数据 schema 不可用。
本指南描述手动迁移路径，帮助用户从旧系统过渡到 XRuntime。

## 迁移策略

| 数据类型 | 迁移方式 | 说明 |
|---|---|---|
| 会话历史 | 重新创建 | 旧会话上下文无法自动迁移；建议用新系统重建 |
| Agent 配置 | 手动录入 | 通过 Admin API 或 `xruntime.yaml` 重新配置 |
| 凭证 (Credential) | 手动录入 | 通过 `/credential` API 重新注册 |
| MCP 服务 | 手动录入 | 通过 `/workspace/mcp` API 或配置文件重新注册 |
| Skill 文件 | 文件复制 | Skill 目录可直接复制到新 workspace |
| 定时任务 | 手动重建 | 通过 `/schedule` API 重新创建 cron |

## 迁移步骤

### 1. 部署新系统

参考 [部署实践手册](./OPS-GUIDE.md) 完成部署。快速验证：

```bash
# 安装依赖
pip install -e ".[xruntime-dev]"

# 启动 Redis
docker run -d --name xruntime-redis -p 6379:6379 redis:7-alpine

# 启动服务
python -m xruntime._server
# 或通过 Docker Compose:
# docker compose -f deploy/docker-compose.yml up -d
```

### 2. 迁移 Agent 配置

将旧 agent 配置写入 `xruntime.yaml`：

```yaml
agents:
  - name: code-engineer
    system_prompt: "You are a code engineering agent."
    allowed_tools: [Read, Write, Edit, Bash, Glob, Grep]
```

或通过 Admin API 创建：

```bash
curl -X POST http://localhost:8900/agent \
  -H "x-api-key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "code-engineer", "system_prompt": "..."}'
```

### 3. 迁移凭证

```bash
curl -X POST http://localhost:8900/credential \
  -H "x-api-key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"type": "openai_credential", "api_key": "sk-..."}'
```

### 4. 迁移 MCP 服务

将旧 MCP 配置写入 `xruntime.yaml`：

```yaml
mcps:
  - name: github
    transport: stdio
    command: npx
    args: ["@github/mcp"]
```

### 5. 迁移 Skills

将 skill 目录复制到新 workspace：

```bash
cp -r /old/workspace/.skills /new/workspace/.skills
```

### 6. 会话迁移

旧会话**无法自动迁移**。建议：
- 在新系统中重新创建会话
- 使用 SDK 客户端发送旧对话摘要作为上下文

```python
from xruntime_sdk import create_client

client = create_client("http://localhost:8900", tenant_id="acme")
result = await client.query(
    protocol="claude_code",
    prompt="以下是我们之前的对话摘要：... 请基于此继续",
)
```

### 7. 验证迁移

使用 Migrator CLI 验证新会话 schema 版本：

```bash
python -m xruntime._runtime._migrator --dry-run
```

## 回滚预案

1. 旧系统保持只读运行
2. 新系统并行运行（影子流量）
3. 逐步切流验证
4. 如新系统异常，回滚到旧系统

## 迁移检查清单

- [ ] 新系统部署完成
- [ ] Agent 配置迁移完成
- [ ] 凭证迁移完成
- [ ] MCP 服务迁移完成
- [ ] Skill 文件迁移完成
- [ ] 定时任务重建完成
- [ ] 灰度验证通过
- [ ] 旧系统下线