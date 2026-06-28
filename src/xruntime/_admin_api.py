# -*- coding: utf-8 -*-
"""Admin API endpoints for XRuntime.

Mounted at ``/admin/*``. Provides read-only management endpoints
for skills, memories, tenants, and system status.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("xruntime.admin")

admin_router = APIRouter(prefix="/admin", tags=["admin"])


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
    request: Any,
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
            else 9
        ),
        "langfuse_enabled": (
            getattr(state, "langfuse_enabled", False)
        ),
        "redis_enabled": (
            "redis" in str(type(memory_store)).lower()
            if memory_store
            else False
        ),
        "version": "1.0.0",
    }


@admin_router.get("/skills")
async def list_skills(
    request: Any,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List all registered skills."""
    state = _get_app_state(request)
    skill_registry = getattr(state, "skill_registry", None)

    if skill_registry is None:
        raise HTTPException(status_code=503, detail="Skill registry not available")

    skills: list[dict[str, Any]] = []
    for name in skill_registry.skill_names:
        try:
            meta = skill_registry.get_metadata(name)
            skills.append({
                "name": meta.get("name", name),
                "description": meta.get("description", ""),
                "source_path": meta.get("source_path", ""),
                "has_content": True,
            })
        except Exception as e:
            logger.debug("Failed to get metadata for %s: %s", name, e)

    return {
        "total": len(skills),
        "skills": skills[:limit],
    }


@admin_router.get("/skills/{skill_name}")
async def get_skill_detail(
    request: Any,
    skill_name: str,
) -> dict[str, Any]:
    """Get detailed information for a specific skill."""
    state = _get_app_state(request)
    skill_registry = getattr(state, "skill_registry", None)

    if skill_registry is None:
        raise HTTPException(status_code=503, detail="Skill registry not available")

    try:
        content = skill_registry.load_content(skill_name)
        return {
            "name": content.name,
            "instructions": content.instructions[:2000],
            "instruction_length": len(content.instructions),
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Skill not found: {e}")


@admin_router.post("/memories/search")
async def search_memories(
    request: Any,
    query: MemorySearchQuery,
) -> dict[str, Any]:
    """Search memories by text query."""
    state = _get_app_state(request)
    memory_store = getattr(state, "memory_store", None)

    if memory_store is None:
        raise HTTPException(status_code=503, detail="Memory store not available")

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
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


@admin_router.get("/memories")
async def list_memories(
    request: Any,
    user_id: str = "",
    tenant_id: str = "default",
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List memories filtered by user/tenant."""
    state = _get_app_state(request)
    memory_store = getattr(state, "memory_store", None)

    if memory_store is None:
        raise HTTPException(status_code=503, detail="Memory store not available")

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
        raise HTTPException(status_code=500, detail=f"List failed: {e}")


@admin_router.get("/models")
async def list_available_models(
    request: Any,
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

    return {
        "total": 3,
        "models": ["glm-4-flash", "glm-4", "glm-5.2"],
        "note": "Using default model tiers (MultiModelRouter not initialized)",
    }


@admin_router.get("/metrics/summary")
async def get_metrics_summary(
    request: Any,
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
