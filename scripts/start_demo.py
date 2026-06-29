#!/usr/bin/env python3
"""Minimal XRuntime demo server with mock model provider.

Run this to start a local XRuntime gateway that responds with mock
messages, no real API key required.
"""
import os
import sys
from typing import Any, Literal, Type

os.environ.setdefault("XRUNTIME_PRODUCTION", "false")
os.environ.setdefault("XRUNTIME_WORKSPACE_BACKEND", "local")
os.environ.setdefault("XRUNTIME_STORAGE_REDIS_HOST", "localhost")
os.environ.setdefault("XRUNTIME_STORAGE_REDIS_PASSWORD", "demo-redis-pw")
os.environ.setdefault("XRUNTIME_MESSAGE_BUS_REDIS_HOST", "localhost")
os.environ.setdefault("XRUNTIME_MESSAGE_BUS_REDIS_PASSWORD", "demo-redis-pw")
os.environ.setdefault("XRUNTIME_SERVER_AUTH_ENABLED", "false")
os.environ.setdefault("XRUNTIME_TENANT_ID", "demo-tenant")

# Mock model provider config (must be set before build_xruntime_app)
os.environ["XRUNTIME_MODEL_PROVIDER"] = "mock"
os.environ["XRUNTIME_MODEL_API_KEY"] = "demo-key"
os.environ["XRUNTIME_MODEL_NAME"] = "mock-model"

from pydantic import BaseModel

from agentscope.credential import CredentialBase, CredentialFactory
from agentscope.message import TextBlock
from agentscope.model import ChatModelBase, ChatResponse

from xruntime._runtime._model_resolver import register_provider


class _MockChatCredential(CredentialBase):
    """Mock credential for demo use."""

    model_config = {"title": "Mock Chat API"}

    type: Literal["mock_chat_credential"] = "mock_chat_credential"
    api_key: str = "demo-key"

    @classmethod
    def get_chat_model_class(cls) -> Type[ChatModelBase]:
        """Return the mock model class."""
        return _MockChatModel


class _MockChatModel(ChatModelBase):
    """Mock chat model returning a friendly demo response."""

    class Parameters(BaseModel):
        """Empty parameters."""

    def __init__(
        self,
        credential: CredentialBase,
        model: str = "mock",
        parameters: "BaseModel | None" = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters or _MockChatModel.Parameters(),
            stream=stream,
            context_size=32768,
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Return a demo response."""
        last_user_msg = "Hello!"
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_msg = msg.content
                break
        return ChatResponse(
            content=[
                TextBlock(
                    text=(
                        f"Hello from XRuntime demo! 👋\n\n"
                        f"You said: {last_user_msg}\n\n"
                        f"This is a mock response from the demo model. "
                        f"Configure a real model provider to use live APIs."
                    )
                )
            ],
            is_last=True,
        )


def main() -> None:
    """Register mock provider and start the server."""
    # Register with both CredentialFactory and ModelResolver
    CredentialFactory.register_credential(_MockChatCredential)
    register_provider("mock", _MockChatCredential)

    import uvicorn
    from xruntime._server import build_xruntime_app

    app = build_xruntime_app()

    print("=" * 60)
    print("  XRuntime Demo Server")
    print("=" * 60)
    print("  Model: mock (built-in demo)")
    print("  Auth: disabled")
    print("  Workspace: local")
    print("  Redis: localhost:6379")
    print()
    print("  Endpoints:")
    print("    GET  /health           - Health check")
    print("    GET  /ready            - Ready check")
    print("    POST /v1/messages      - Anthropic Messages API")
    print("    POST /v1/opencode      - OpenCode Protocol")
    print("    POST /v1/claude-code/query - Claude Code SDK")
    print()
    print("  Try it:")
    print('    curl -X POST http://localhost:8900/v1/messages \\')
    print('      -H "Content-Type: application/json" \\')
    print('      -d \'{"model":"claude-3-sonnet","max_tokens":100,')
    print('          "messages":[{"role":"user","content":"Hello!"}]}\'')
    print("=" * 60)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8900, log_level="info")


if __name__ == "__main__":
    main()
