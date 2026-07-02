# -*- coding: utf-8 -*-
"""Docker credential injection — writes brokered credentials to containers.

This module implements the per-request credential injection boundary
mandated by the Vercel-Eve-style sandbox architecture. The
:class:`DockerWorkspace` already filters sensitive env vars via
``_SENSITIVE_ENV_PATTERNS`` (API_KEY, SECRET, TOKEN, etc.) so secrets
never enter the container env. This module provides the complementary
*positive* path: writing the brokered credential metadata (no api_key)
to a container-internal file so the sandbox can quote the
``credential_id`` back to the host when it needs to call an API.

Pattern mirrors the existing ``_write_gateway_config`` flow in
:class:`DockerWorkspace`:

1. Host calls :func:`inject_credential_into_workspace` with a
   :class:`ShortLivedCredential`.
2. The helper writes ``credential.to_injection_dict()`` (no api_key!)
   to :data:`BROKER_CREDENTIAL_FILE` inside the container.
3. The sandbox reads the file, quotes the ``credential_id`` back to
   the host's credential broker endpoint when it needs to make an API
   call. The host validates the credential_id (TTL + scopes +
   audience) and proxies the call with the real api_key.

The api_key never crosses the sandbox boundary.
"""
from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentscope.workspace import DockerWorkspace

    from ._short_lived import ShortLivedCredential


# Path inside the container where the brokered credential metadata is
# written. Mirrors the GATEWAY_CONFIG pattern (single file, no host
# disk persistence, regenerated per request).
BROKER_CREDENTIAL_FILE = "/root/.agentscope/broker_credential.json"


async def inject_credential_into_workspace(
    workspace: "DockerWorkspace",
    credential: "ShortLivedCredential",
) -> None:
    """Write a brokered credential's metadata into a Docker workspace.

    Writes :meth:`ShortLivedCredential.to_injection_dict` (which
    excludes ``api_key``) to :data:`BROKER_CREDENTIAL_FILE` inside the
    container. The sandbox can then quote the ``credential_id`` back
    to the host when it needs to make an API call.

    The file is overwritten on each call so per-request credential
    rotation is reflected immediately.

    Args:
        workspace (`DockerWorkspace`):
            The Docker workspace to inject into. Must be initialized.
        credential (`ShortLivedCredential`):
            The short-lived credential to inject. Only its safe
            metadata (credential_id, provider, model, scopes,
            audience, expiry) is written — never the api_key.
    """
    # Ensure the directory exists (mirrors _write_gateway_config).
    mkdir_cmd = f"mkdir -p {shlex.quote(_parent_dir(BROKER_CREDENTIAL_FILE))}"
    await workspace._exec(mkdir_cmd)  # noqa: SLF001
    # _write is the existing private method used by _write_gateway_config.
    # Accessing it here is intentional — the broker injection lives in
    # XRuntime (not AS core) and reuses the existing file-write path
    # rather than adding a new public method to DockerWorkspace.
    payload = json.dumps(
        credential.to_injection_dict(),
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    await workspace._write(BROKER_CREDENTIAL_FILE, payload)  # noqa: SLF001


def _parent_dir(path: str) -> str:
    """Return the parent directory of a posix path.

    Args:
        path (`str`): A posix-style path.

    Returns:
        `str`: The parent directory.
    """
    import posixpath

    return posixpath.dirname(path) or "/"


async def read_credential_from_workspace(
    workspace: "DockerWorkspace",
) -> dict[str, Any] | None:
    """Read the brokered credential metadata from a Docker workspace.

    Used by tests / debug tooling to verify the injection succeeded.
    Returns ``None`` if the file does not exist (e.g. fresh container
    before the first request, or after revocation cleanup).

    ``DockerWorkspace._read`` raises :class:`FileNotFoundError` when
    the path is missing; we swallow it here so callers can treat
    "not injected yet" uniformly as ``None``.

    Args:
        workspace (`DockerWorkspace`): The Docker workspace.

    Returns:
        `dict[str, Any] | None`: The credential metadata, or ``None``.
    """
    try:
        result = await workspace._read(BROKER_CREDENTIAL_FILE)  # noqa: SLF001
    except FileNotFoundError:
        return None
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return None
