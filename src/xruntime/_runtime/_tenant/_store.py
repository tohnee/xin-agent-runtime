# -*- coding: utf-8 -*-
"""Authentication principal and in-memory tenant membership stores."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

from ._policy import Principal, TenantMember, TenantRole


@dataclass(frozen=True)
class AuthPrincipal(Principal):
    """Authenticated request principal with optional KB scope."""

    kb_ids: list[str] = field(default_factory=list)
    api_key_id: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApiKeyRecord:
    """A configured API key bound to tenant/user/role."""

    key: str
    tenant_id: str
    user_id: str
    role: TenantRole = TenantRole.VIEWER
    kb_ids: list[str] = field(default_factory=list)
    key_id: str | None = None
    active: bool = True


class ApiKeyStore:
    """In-memory API key store.

    This store is intentionally simple so tests and lightweight embeds can
    use it directly. Production deployments should replace it with a
    persistent implementation that hashes secrets at rest.
    """

    def __init__(self, records: list[ApiKeyRecord] | None = None) -> None:
        """Initialize the store with optional records."""
        self._records = {record.key: record for record in records or []}

    def authenticate(self, api_key: str) -> AuthPrincipal | None:
        """Resolve an API key to an authenticated principal."""
        record = self._records.get(api_key)
        if record is None or not record.active:
            return None
        return AuthPrincipal(
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            role=record.role,
            kb_ids=list(record.kb_ids),
            api_key_id=record.key_id or record.key,
        )


class JwtClaimsParser:
    """Minimal JWT claims parser.

    Supports unsigned ``alg=none`` tokens for local tests and HS256 tokens
    when ``secret`` is configured. It is not a replacement for a full OIDC
    client; production deployments should validate issuer, audience,
    expiry, key rotation, and algorithms explicitly.
    """

    def __init__(self, secret: str | None = None) -> None:
        """Initialize the parser."""
        self._secret = secret

    def parse(self, token: str) -> AuthPrincipal:
        """Parse a JWT into an authenticated principal."""
        header_b64, payload_b64, signature_b64 = token.split(".", 2)
        header = _json_b64decode(header_b64)
        payload = _json_b64decode(payload_b64)
        alg = header.get("alg", "none")
        if self._secret or alg == "HS256":
            if alg != "HS256":
                raise ValueError("Only HS256 JWTs are supported with a secret")
            expected = hmac.new(
                self._secret.encode() if self._secret else b"",
                f"{header_b64}.{payload_b64}".encode(),
                hashlib.sha256,
            ).digest()
            actual = _b64decode(signature_b64)
            if not hmac.compare_digest(expected, actual):
                raise ValueError("Invalid JWT signature")
        tenant_id = payload.get("tenant_id") or payload.get("tid")
        user_id = payload.get("sub") or payload.get("user_id")
        role_raw = payload.get("role", TenantRole.VIEWER.value)
        if not tenant_id or not user_id:
            raise ValueError("JWT must contain tenant_id and sub/user_id")
        return AuthPrincipal(
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            role=TenantRole(str(role_raw)),
            kb_ids=list(payload.get("kb_ids", [])),
            claims=payload,
        )


class TenantMembershipStore:
    """In-memory tenant membership store."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._members: dict[tuple[str, str], TenantMember] = {}

    def upsert(
        self,
        tenant_id: str,
        user_id: str,
        role: TenantRole,
        status: str = "active",
        invited_by: str | None = None,
    ) -> TenantMember:
        """Create or replace a membership."""
        member = TenantMember(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            status=status,
            invited_by=invited_by,
        )
        self._members[(tenant_id, user_id)] = member
        return member

    def get_member(self, tenant_id: str, user_id: str) -> TenantMember | None:
        """Return a membership or None."""
        return self._members.get((tenant_id, user_id))

    def resolve_principal(
        self,
        tenant_id: str,
        user_id: str,
    ) -> AuthPrincipal | None:
        """Resolve an active member to an auth principal."""
        member = self.get_member(tenant_id, user_id)
        if member is None or member.status != "active":
            return None
        return AuthPrincipal(
            tenant_id=member.tenant_id,
            user_id=member.user_id,
            role=member.role,
        )


def _b64decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def _json_b64decode(data: str) -> dict[str, Any]:
    return json.loads(_b64decode(data).decode())
