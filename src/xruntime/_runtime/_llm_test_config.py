# -*- coding: utf-8 -*-
"""Ark API model configuration for E2E testing.

Ark (火山方舟) API is compatible with both OpenAI and Anthropic
protocols. This module provides factory functions to create
configured model instances for E2E testing.

API Info:
    - OpenAI compatible: https://ark.cn-beijing.volces.com/api/plan/v3
    - Anthropic compatible: https://ark.cn-beijing.volces.com/api/plan
    - Models: glm-5.2, minimax-m3, kimi-k2.7-code

Usage::

    from xruntime._runtime._llm_test_config import (
        create_ark_openai_model,
        ARK_API_KEY,
        ARK_MODELS,
    )

    model = create_ark_openai_model("glm-5.2")
    response = await model(messages=[...])
"""
from __future__ import annotations

import os
from typing import Any

# Ark API configuration
ARK_API_KEY = os.environ.get(
    "ARK_API_KEY",
    "ark-1300f8d7-0482-41df-bc77-c8a58eaa1240-89be3",
)
ARK_OPENAI_BASE_URL = os.environ.get(
    "ARK_OPENAI_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/plan/v3",
)
ARK_ANTHROPIC_BASE_URL = os.environ.get(
    "ARK_ANTHROPIC_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/plan",
)

# Available models
ARK_MODELS: list[str] = [
    "glm-5.2",
    "minimax-m3",
    "kimi-k2.7-code",
]

# Default model for tests
ARK_DEFAULT_MODEL = "glm-5.2"


def create_ark_openai_model(
    model_name: str = ARK_DEFAULT_MODEL,
    stream: bool = False,
) -> Any:
    """Create an OpenAI-compatible model backed by Ark API.

    Args:
        model_name: Model name (e.g. ``"glm-5.2"``).
        stream: Whether to enable streaming.

    Returns:
        `OpenAIChatModel`: Configured model instance.
    """
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel

    credential = OpenAICredential(
        api_key=ARK_API_KEY,
        base_url=ARK_OPENAI_BASE_URL,
    )

    return OpenAIChatModel(
        credential=credential,
        model=model_name,
        parameters=OpenAIChatModel.Parameters(),
        stream=stream,
        max_retries=2,
        retry_delay=1.0,
        context_size=32768,
    )


def create_ark_anthropic_model(
    model_name: str = ARK_DEFAULT_MODEL,
    stream: bool = False,
) -> Any:
    """Create an Anthropic-compatible model backed by Ark API.

    Args:
        model_name: Model name (e.g. ``"glm-5.2"``).
        stream: Whether to enable streaming.

    Returns:
        `AnthropicChatModel`: Configured model instance.
    """
    from agentscope.credential._anthropic import AnthropicCredential
    from agentscope.model import AnthropicChatModel

    credential = AnthropicCredential(
        api_key=ARK_API_KEY,
        base_url=ARK_ANTHROPIC_BASE_URL,
    )

    return AnthropicChatModel(
        credential=credential,
        model=model_name,
        parameters=AnthropicChatModel.Parameters(),
        stream=stream,
        max_retries=2,
        retry_delay=1.0,
        context_size=32768,
    )


def is_ark_available() -> bool:
    """Check if Ark API is reachable (quick connectivity test).

    Returns:
        `bool`: True if API key is set.
    """
    return bool(ARK_API_KEY) and ARK_API_KEY.startswith("ark-")
