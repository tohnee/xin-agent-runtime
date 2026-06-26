# Xin Agent Runtime SDK 使用指南

> 版本: v1.0.0
> 更新日期: 2026-06-25

## 安装

```bash
pip install -e ".[xruntime]"
```

## 快速开始

```python
import asyncio
from xruntime_sdk import create_client

async def main():
    client = create_client(
        "http://localhost:8900",
        tenant_id="acme",
        api_key="your-api-key",
    )

    # Anthropic 协议
    result = await client.query(
        protocol="anthropic",
        prompt="List all files in the project",
        model="claude-sonnet-4-20250514",
    )

    # Claude Code SDK 协议
    result = await client.query(
        protocol="claude_code",
        prompt="Fix the bug in auth.py",
        options={
            "allowed_tools": ["Read", "Edit", "Bash"],
            "permission_mode": "acceptEdits",
            "max_turns": 10,
        },
    )

    # OpenCode SDK 协议
    result = await client.query(
        protocol="opencode",
        prompt="Find all TODO comments",
        agent="coder",
        config={
            "agents": {
                "coder": {"tools": ["read", "glob", "grep"]},
            },
        },
    )

    # 处理结果 (NDJSON 行列表)
    for line in result:
        import json
        event = json.loads(line)
        print(event)

    await client.close()

asyncio.run(main())
```

## 会话管理

```python
# 创建新会话
result = await client.query(
    protocol="claude_code",
    prompt="Read the auth module",
    options={"allowed_tools": ["Read", "Glob"]},
)
# 从第一个事件提取 session_id

# 续接会话
result = await client.query(
    protocol="claude_code",
    prompt="Now find all places that call it",
    session_id="sess-xxx",
)
```

## 管理客户端

```python
from xruntime_sdk import AdminClient

admin = AdminClient("http://localhost:8900")
info = await admin.server_info()
await admin.close()
```

## 插件开发

```python
from xruntime._runtime._plugin import (
    XRuntimePlugin,
    PluginContext,
)

class MyPlugin(XRuntimePlugin):
    name = "my-plugin"
    version = "1.0.0"

    def initialize(self, context: PluginContext) -> None:
        # 注册中间件/工具/适配器
        pass

    def shutdown(self) -> None:
        # 清理资源
        pass
```