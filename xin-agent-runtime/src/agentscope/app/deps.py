# -*- coding: utf-8 -*-
"""Shared FastAPI dependencies for the agentscope app."""
from fastapi import Header, HTTPException, Request, status

from .workspace_manager import WorkspaceManagerBase
from ._manager import (
    BackgroundTaskManager,
    ChatRunRegistry,
    SchedulerManager,
)
from ._service import ChatService, SessionService
from ._types import AgentMiddlewareFactory, AgentToolFactory
from .message_bus import MessageBus
from .storage import StorageBase


async def get_current_user_id(
    request: Request,
    x_user_id: str = Header(
        default="",
        description="Caller's user ID. "
        "DEPRECATED dev-mode fallback; superseded by JWT/API-key "
        "auth via AuthMiddleware. Ignored when request.state.principal "
        "is set by the gateway auth middleware.",
    ),
) -> str:
    """Return the caller's authenticated user ID.

    Identity resolution is fail-closed:

    1. **Primary**: When ``AuthMiddleware`` (or any compatible auth
       middleware) has attached ``request.state.principal`` to the
       request, the principal's ``user_id`` is authoritative and the
       client-supplied ``X-User-ID`` header is IGNORED. This prevents
       identity spoofing via header injection.
    2. **Dev-mode fallback**: When no principal is present (e.g.
       ``AuthMiddleware`` is not mounted — typical in local
       development), the ``X-User-ID`` header is accepted as a
       fallback only when it is non-empty. This preserves backward
       compatibility for local embeds.
    3. **Fail-closed**: When neither principal nor a non-empty
       ``X-User-ID`` header is present, the request is rejected with
       ``401 Unauthorized``.

    Args:
        request (`Request`): The incoming FastAPI request. Used to
            read ``request.state.principal`` set by auth middleware.
        x_user_id (`str`): Value of the ``X-User-ID`` header. Used
            only as a dev-mode fallback when no principal is set.

    Returns:
        `str`: The authenticated user ID.

    Raises:
        `HTTPException`: 401 if neither a principal nor a non-empty
            ``X-User-ID`` header is available.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        # Authenticated principal is authoritative. The X-User-ID
        # header is intentionally ignored to prevent identity spoofing.
        return str(principal.user_id)

    # Dev-mode fallback: no auth middleware mounted, trust the header.
    if x_user_id:
        return x_user_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Authentication required: no authenticated principal on "
            "request.state and no X-User-ID header (dev-mode fallback). "
            "Mount AuthMiddleware or supply X-User-ID for local dev."
        ),
    )


async def get_storage(request: Request) -> StorageBase:
    """Return the application-wide storage backend.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `StorageBase`: The storage instance stored in ``app.state``.
    """
    return request.app.state.storage


async def get_message_bus(request: Request) -> MessageBus:
    """Return the application-wide message bus.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `MessageBus`: The message bus instance stored in ``app.state``.
    """
    return request.app.state.message_bus


async def get_chat_service(request: Request) -> ChatService:
    """Return the application-wide chat service.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `ChatService`: The chat service instance stored in ``app.state``.
    """
    return request.app.state.chat_service


async def get_session_service(request: Request) -> SessionService:
    """Return the application-wide session service.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `SessionService`: The session service instance stored in
        ``app.state``.
    """
    return request.app.state.session_service


async def get_chat_run_registry(request: Request) -> ChatRunRegistry:
    """Return the per-process chat-run registry.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `ChatRunRegistry`: The registry stored in ``app.state``.
    """
    return request.app.state.chat_run_registry


async def get_scheduler_manager(request: Request) -> SchedulerManager:
    """Return the application-wide scheduler manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `SchedulerManager`: The scheduler manager stored in ``app.state``.
    """
    return request.app.state.scheduler_manager


async def get_background_task_manager(
    request: Request,
) -> BackgroundTaskManager:
    """Return the application-wide background task manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `BackgroundTaskManager`: The background task manager stored in
        ``app.state``.
    """
    return request.app.state.background_task_manager


async def get_workspace_manager(request: Request) -> WorkspaceManagerBase:
    """Return the application-wide workspace manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `WorkspaceManagerBase`: The workspace manager stored in ``app.state``.
    """
    return request.app.state.workspace_manager


async def get_extra_agent_middlewares(
    request: Request,
) -> AgentMiddlewareFactory | None:
    """Return the caller-supplied agent middleware factory, if any.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `AgentMiddlewareFactory | None`: The factory passed to
        :func:`~agentscope.app.create_app`, or ``None`` if not configured.
    """
    return request.app.state.extra_agent_middlewares


async def get_extra_agent_tools(
    request: Request,
) -> AgentToolFactory | None:
    """Return the caller-supplied agent tool factory, if any.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `AgentToolFactory | None`: The factory passed to
        :func:`~agentscope.app.create_app`, or ``None`` if not configured.
    """
    return request.app.state.extra_agent_tools
