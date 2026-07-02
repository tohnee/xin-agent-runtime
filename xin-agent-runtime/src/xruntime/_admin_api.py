# -*- coding: utf-8 -*-
"""Admin API endpoints for XRuntime.

Mounted at ``/admin/*``. Provides read-only management endpoints
for skills, memories, tenants, and system status.

All admin endpoints require authentication — the principal must be
present on ``request.state.principal`` (set by ``AuthMiddleware``)
and must hold the ``admin`` or ``owner`` tenant role.  Endpoints
that accept a ``tenant_id`` parameter additionally enforce tenant
scope: an admin of tenant A cannot list/search memories of
tenant B.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ._runtime._tenant._store import AuthPrincipal

logger = logging.getLogger("xruntime.admin")


def _get_version() -> str:
    """Return the XRuntime package version."""
    try:
        from xruntime._version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


admin_router = APIRouter(prefix="/admin", tags=["admin"])


async def _require_admin(request: Request) -> AuthPrincipal:
    """Dependency that rejects non-admin/owner principals.

    Returns the authenticated principal so downstream endpoints can
    enforce tenant scope (an admin may only query resources within
    their own tenant).

    Args:
        request (`Request`): The incoming request with ``principal``
            set by ``AuthMiddleware``.

    Returns:
        `AuthPrincipal`: The authenticated principal.

    Raises:
        `HTTPException`: 401 if not authenticated, 403 if role is
            insufficient.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required for admin endpoints",
        )
    role = getattr(principal, "role", None)
    role_val = role.value if hasattr(role, "value") else str(role)
    if role_val not in ("admin", "owner"):
        raise HTTPException(
            status_code=403,
            detail=f"Admin role required (current: {role_val})",
        )
    return principal


def _enforce_tenant_scope(
    principal: AuthPrincipal,
    requested_tenant: str,
) -> None:
    """Reject cross-tenant admin queries.

    An admin/owner may only query resources within their own
    tenant.  This is the defense-in-depth check that complements
    ``_require_admin``'s role check.

    Args:
        principal (`AuthPrincipal`): The authenticated principal.
        requested_tenant (`str`): The tenant_id the caller is
            trying to query.

    Raises:
        `HTTPException`: 403 if ``requested_tenant`` does not match
            the principal's tenant.
    """
    if requested_tenant != principal.tenant_id:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Cross-tenant access denied: principal tenant="
                f"{principal.tenant_id}, requested tenant="
                f"{requested_tenant}",
            ),
        )


class SkillInfo(BaseModel):
    """Skill metadata summary."""

    name: str
    description: str
    source_path: str
    has_content: bool


class MemorySearchQuery(BaseModel):
    """Memory search parameters."""

    query: str
    tenant_id: str = "default"
    user_id: str = ""
    limit: int = 10


class SystemStatus(BaseModel):
    """System status summary."""

    total_skills: int
    total_memories: int
    active_sessions: int
    middleware_count: int
    langfuse_enabled: bool
    redis_enabled: bool
    version: str


def _get_app_state(request: Any) -> Any:
    """Extract app.state from request."""
    return request.app.state


@admin_router.get("/status", response_model=SystemStatus)
async def get_system_status(
    request: Request,
    _admin: None = Depends(_require_admin),
) -> dict[str, Any]:
    """Get overall system status and metrics."""
    state = _get_app_state(request)
    skill_registry = getattr(state, "skill_registry", None)
    memory_store = getattr(state, "memory_store", None)
    metrics = getattr(state, "metrics", None)

    total_memories = 0
    if memory_store is not None:
        try:
            total_memories = memory_store.count
        except Exception:
            total_memories = 0

    return {
        "total_skills": (
            len(skill_registry.skill_names) if skill_registry else 0
        ),
        "total_memories": total_memories,
        "active_sessions": (
            int(metrics.active_sessions)
            if metrics and hasattr(metrics, "active_sessions")
            else 0
        ),
        "middleware_count": (
            len(getattr(state, "middleware_chain", []))
            if hasattr(state, "middleware_chain")
            else 0
        ),
        "langfuse_enabled": (getattr(state, "langfuse_enabled", False)),
        "redis_enabled": (
            type(memory_store).__name__.lower().startswith("redis")
            if memory_store
            else False
        ),
        "version": _get_version(),
    }


@admin_router.get("/skills")
async def list_skills(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    _admin: None = Depends(_require_admin),
) -> dict[str, Any]:
    """List all registered skills."""
    state = _get_app_state(request)
    skill_registry = getattr(state, "skill_registry", None)

    if skill_registry is None:
        raise HTTPException(
            status_code=503, detail="Skill registry not available"
        )

    skills: list[dict[str, Any]] = []
    for name in skill_registry.skill_names:
        try:
            meta = skill_registry.get_metadata(name)
            skills.append(
                {
                    "name": meta.get("name", name),
                    "description": meta.get("description", ""),
                    "source_path": meta.get("source_path", ""),
                    "has_content": True,
                }
            )
        except Exception as e:
            logger.debug("Failed to get metadata for %s: %s", name, e)

    return {
        "total": len(skills),
        "skills": skills[:limit],
    }


@admin_router.get("/skills/{skill_name}")
async def get_skill_detail(
    request: Request,
    skill_name: str,
    _admin: None = Depends(_require_admin),
) -> dict[str, Any]:
    """Get detailed information for a specific skill."""
    state = _get_app_state(request)
    skill_registry = getattr(state, "skill_registry", None)

    if skill_registry is None:
        raise HTTPException(
            status_code=503, detail="Skill registry not available"
        )

    try:
        content = skill_registry.load_content(skill_name)
        return {
            "name": content.name,
            "instructions": content.instructions[:2000],
            "instruction_length": len(content.instructions),
        }
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Skill not found: {skill_name}"
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to load skill '%s'",
            skill_name,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal error while loading skill.",
        )


@admin_router.post("/memories/search")
async def search_memories(
    request: Request,
    query: MemorySearchQuery,
    principal: AuthPrincipal = Depends(_require_admin),
) -> dict[str, Any]:
    """Search memories by text query."""
    _enforce_tenant_scope(principal, query.tenant_id)
    state = _get_app_state(request)
    memory_store = getattr(state, "memory_store", None)

    if memory_store is None:
        raise HTTPException(
            status_code=503, detail="Memory store not available"
        )

    try:
        memories = memory_store.search(
            query=query.query,
            user_id=query.user_id,
            tenant_id=query.tenant_id,
            top_k=query.limit,
        )
        return {
            "total": len(memories),
            "memories": [
                {
                    "id": m.id,
                    "content": m.content[:200],
                    "type": m.type,
                    "user_id": m.user_id,
                    "tenant_id": m.tenant_id,
                    "confidence": m.confidence,
                }
                for m in memories
            ],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Memory search failed. Check server logs.",
        )


@admin_router.get("/memories")
async def list_memories(
    request: Request,
    user_id: str = "",
    tenant_id: str = "default",
    limit: int = Query(50, ge=1, le=200),
    principal: AuthPrincipal = Depends(_require_admin),
) -> dict[str, Any]:
    """List memories filtered by user/tenant."""
    _enforce_tenant_scope(principal, tenant_id)
    state = _get_app_state(request)
    memory_store = getattr(state, "memory_store", None)

    if memory_store is None:
        raise HTTPException(
            status_code=503, detail="Memory store not available"
        )

    try:
        memories = memory_store.list_all(
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return {
            "total": len(memories),
            "memories": [
                {
                    "id": m.id,
                    "content": m.content[:200],
                    "type": m.type,
                    "user_id": m.user_id,
                    "tenant_id": m.tenant_id,
                    "confidence": m.confidence,
                }
                for m in memories[:limit]
            ],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Memory list failed. Check server logs.",
        )


@admin_router.get("/models")
async def list_available_models(
    request: Request,
    _admin: None = Depends(_require_admin),
) -> dict[str, Any]:
    """List available model tiers and routing config."""
    state = _get_app_state(request)
    model_router = getattr(state, "model_router", None)

    if model_router is not None:
        models = model_router.get_available_models()
        return {
            "total": len(models),
            "models": models,
        }

    # Fallback: derive from MultiModelRouter defaults rather than
    # maintaining a separate hardcoded list that could drift.
    from ._runtime._model_router import MultiModelRouter

    models = MultiModelRouter().get_available_models()
    return {
        "total": len(models),
        "models": models,
        "note": "Using default model tiers (MultiModelRouter not initialized)",
    }


@admin_router.get("/metrics/summary")
async def get_metrics_summary(
    request: Request,
    _admin: None = Depends(_require_admin),
) -> dict[str, Any]:
    """Get aggregated performance metrics summary."""
    state = _get_app_state(request)
    metrics = getattr(state, "metrics", None)

    if metrics is None:
        return {
            "note": "Metrics collector not initialized",
            "tool_calls_total": 0,
            "active_sessions": 0,
        }

    return {
        "active_sessions": int(getattr(metrics, "active_sessions", 0)),
        "tool_calls_total": int(getattr(metrics, "tool_calls_total", 0)),
        "model_calls_total": int(getattr(metrics, "model_calls_total", 0)),
        "note": "Prometheus /metrics has full metrics",
    }
