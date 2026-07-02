# -*- coding: utf-8 -*-
"""RedisCredentialStore — Redis-backed credential persistence.

Stores :class:`ShortLivedCredential` instances in Redis with TTL,
multi-tenant key isolation, and session-tuple indexing.

Security: the ``api_key`` field is base64-encoded before storage so
the raw Redis value does not contain the plaintext secret.  This is
obfuscation, not encryption — for production hardening, consider
envelope encryption with a KMS-managed key.

Key layout::

    tenant:{tid}:creds:{cred_id}        → JSON credential blob
    tenant:{tid}:creds:session:{sid}:{rid} → credential_id (index)
    tenant:{tid}:creds:index             → SET of all cred_ids (for list/delete)

Requires: ``pip install redis`` (the ``storage`` extra).
"""
from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

if TYPE_CHECKING:
    from ._short_lived import ShortLivedCredential  # noqa: F401

logger = logging.getLogger("xruntime.credential.redis_store")


class RedisCredentialStore:
    """Redis-backed persistent credential store.

    Args:
        redis_url (`str`):
            Redis connection URL.
        key_prefix (`str`):
            Key prefix with ``{tid}`` placeholder for tenant id.
        connect_timeout (`float`):
            Connection timeout in seconds.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "tenant:{tid}:creds:",
        connect_timeout: float = 5.0,
    ) -> None:
        """Initialize the store (lazy connection)."""
        self._url = redis_url
        self._prefix = key_prefix
        self._connect_timeout = connect_timeout
        self._client: Any = None

    # ── public API ───────────────────────────────────────────────

    async def save(
        self,
        cred: "ShortLivedCredential",
        *,
        tenant_id: str,
        session_id: str | None = None,
        request_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Save a credential to Redis.

        Args:
            cred (`ShortLivedCredential`): The credential to save.
            tenant_id (`str`): The tenant that owns this credential.
            session_id (`str | None`): Optional session id for indexing.
            request_id (`str | None`): Optional request id for indexing.
            ttl_seconds (`int | None`):
                Redis TTL in seconds.  ``None`` means persistent.
        """
        client = await self._get_client()
        key = self._cred_key(cred.credential_id, tenant_id=tenant_id)
        data = self._serialize(cred)

        await client.set(key, data)
        if ttl_seconds is not None and ttl_seconds > 0:
            await client.expire(key, ttl_seconds)

        # Add to tenant index
        index_key = self._index_key(tenant_id)
        await client.sadd(index_key, cred.credential_id)

        # Session index
        if session_id is not None and request_id is not None:
            sess_key = self._session_key(
                tenant_id=tenant_id,
                session_id=session_id,
                request_id=request_id,
            )
            await client.set(sess_key, cred.credential_id)
            if ttl_seconds is not None and ttl_seconds > 0:
                await client.expire(sess_key, ttl_seconds)

    async def load(
        self,
        credential_id: str,
        *,
        tenant_id: str,
    ) -> "ShortLivedCredential | None":
        """Load a credential by id.

        Returns ``None`` if not found or expired.

        Args:
            credential_id (`str`): The credential id.
            tenant_id (`str`): The tenant id.

        Returns:
            `ShortLivedCredential | None`: The credential, or ``None``.
        """
        client = await self._get_client()
        key = self._cred_key(credential_id, tenant_id=tenant_id)
        raw = await client.get(key)
        if raw is None:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        cred = self._deserialize(raw)
        if cred is None:
            return None

        # Check application-level expiry
        import time

        if cred.is_expired(time.time()):
            # Clean up expired entry
            await client.delete(key)
            return None

        return cred

    async def delete(
        self,
        credential_id: str,
        *,
        tenant_id: str,
    ) -> bool:
        """Delete a credential.

        Args:
            credential_id (`str`): The credential id.
            tenant_id (`str`): The tenant id.

        Returns:
            `bool`: ``True`` if deleted, ``False`` if not found.
        """
        client = await self._get_client()
        key = self._cred_key(credential_id, tenant_id=tenant_id)
        deleted = await client.delete(key)
        if deleted:
            # Remove from tenant index
            index_key = self._index_key(tenant_id)
            await client.srem(index_key, credential_id)
            return True
        return False

    async def list_by_tenant(
        self,
        tenant_id: str,
    ) -> list["ShortLivedCredential"]:
        """List all credentials for a tenant.

        Args:
            tenant_id (`str`): The tenant id.

        Returns:
            `list[ShortLivedCredential]`: Non-expired credentials.
        """
        client = await self._get_client()
        index_key = self._index_key(tenant_id)
        members = await client.smembers(index_key)
        if not members:
            return []

        result: list[ShortLivedCredential] = []
        for member in members:
            cred_id = (
                member.decode("utf-8") if isinstance(member, bytes) else member
            )
            cred = await self.load(cred_id, tenant_id=tenant_id)
            if cred is not None:
                result.append(cred)
        return result

    async def delete_by_tenant(self, tenant_id: str) -> int:
        """Delete all credentials for a tenant.

        Args:
            tenant_id (`str`): The tenant id.

        Returns:
            `int`: Number of credentials deleted.
        """
        client = await self._get_client()
        index_key = self._index_key(tenant_id)
        members = await client.smembers(index_key)
        if not members:
            return 0

        count = 0
        for member in members:
            cred_id = (
                member.decode("utf-8") if isinstance(member, bytes) else member
            )
            key = self._cred_key(cred_id, tenant_id=tenant_id)
            await client.delete(key)
            count += 1

        await client.delete(index_key)
        return count

    async def find_by_session(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request_id: str,
    ) -> "ShortLivedCredential | None":
        """Find a credential by (tenant, session, request) tuple.

        Args:
            tenant_id (`str`): The tenant id.
            session_id (`str`): The session id.
            request_id (`str`): The request id.

        Returns:
            `ShortLivedCredential | None`: The credential, or ``None``.
        """
        client = await self._get_client()
        sess_key = self._session_key(
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
        )
        cred_id = await client.get(sess_key)
        if cred_id is None:
            return None

        if isinstance(cred_id, bytes):
            cred_id = cred_id.decode("utf-8")

        return await self.load(cred_id, tenant_id=tenant_id)

    # ── key helpers ──────────────────────────────────────────────

    def _cred_key(self, cred_id: str, *, tenant_id: str) -> str:
        """Redis key for a credential."""
        prefix = self._prefix.replace("{tid}", tenant_id)
        return f"{prefix}{cred_id}"

    def _index_key(self, tenant_id: str) -> str:
        """Redis key for the tenant's credential index set."""
        prefix = self._prefix.replace("{tid}", tenant_id)
        return f"{prefix}index"

    def _session_key(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request_id: str,
    ) -> str:
        """Redis key for the session index."""
        prefix = self._prefix.replace("{tid}", tenant_id)
        return f"{prefix}session:{session_id}:{request_id}"

    # ── serialization ────────────────────────────────────────────

    @staticmethod
    def _serialize(cred: "ShortLivedCredential") -> str:
        """Serialize a credential to JSON for Redis storage.

        The ``api_key`` is base64-encoded so the raw Redis value
        does not contain the plaintext secret.

        Args:
            cred (`ShortLivedCredential`): The credential.

        Returns:
            `str`: JSON string.
        """
        from ._short_lived import ShortLivedCredential

        # Use model_dump() then encode the api_key
        data = cred.model_dump()
        # SecretStr serializes as ********** by default; replace
        # with base64-encoded real value for round-trip
        raw_key = cred.api_key.get_secret_value()
        data["api_key"] = base64.b64encode(
            raw_key.encode("utf-8"),
        ).decode("ascii")
        # Also add a marker so deserialization knows it's encoded
        data["_api_key_encoding"] = "base64"
        return json.dumps(data)

    @staticmethod
    def _deserialize(raw: str) -> "ShortLivedCredential | None":
        """Deserialize a credential from JSON.

        Args:
            raw (`str`): The JSON string from Redis.

        Returns:
            `ShortLivedCredential | None`: The credential, or ``None``.
        """
        from ._short_lived import ShortLivedCredential

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        # Decode api_key if base64-encoded
        encoding = data.pop("_api_key_encoding", None)
        if encoding == "base64" and "api_key" in data:
            try:
                decoded = base64.b64decode(data["api_key"]).decode("utf-8")
                data["api_key"] = SecretStr(decoded)
            except Exception:  # noqa: BLE001
                return None
        elif "api_key" in data and isinstance(data["api_key"], str):
            # Backward compat: plain string (legacy entries)
            data["api_key"] = SecretStr(data["api_key"])

        try:
            return ShortLivedCredential(**data)
        except Exception:  # noqa: BLE001
            return None

    # ── connection ───────────────────────────────────────────────

    async def _get_client(self) -> Any:
        """Lazily create the async Redis client.

        Returns:
            The Redis client instance.

        Raises:
            `redis.ConnectionError`: If the connection fails.
        """
        if self._client is not None:
            return self._client

        import redis.asyncio as aioredis

        self._client = aioredis.from_url(
            self._url,
            decode_responses=False,
            socket_connect_timeout=self._connect_timeout,
        )
        return self._client
