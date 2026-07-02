# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-docstring,too-many-public-methods
"""TDD unit tests for DockerWorkspace HostConfig security constraints.

These tests do **not** require a running Docker daemon — they mock the
aiodocker client and capture the ``config`` dict passed to
``containers.create_or_replace`` so we can assert the HostConfig
contains the security hardening expected from a Vercel-Eve-style
sandbox boundary.

Security dimensions covered (mapped to the 6 issues found in audit):

1. ``User`` — non-root user (UID:GID, not 0:0)
2. ``CapDrop`` — drop ALL Linux capabilities
3. ``Memory`` — RAM limit (default 512 MiB)
4. ``NanoCpus`` — CPU quota (default 1 core)
5. ``PidsLimit`` — max processes (default 256)
6. ``SecurityOpt`` — ``no-new-privileges`` flag
7. ``NetworkMode`` — isolated network (not ``bridge`` default)
8. ``ReadonlyRootfs`` — read-only root filesystem
9. ``Tmpfs`` — writable /tmp + /run paired with readonly rootfs
10. ``Ulimits`` — nofile + nproc limits
11. Config-driven overrides (memory/cpu via constructor)
12. Bind mount is preserved alongside security constraints
"""
import asyncio
import os
import tempfile
import unittest
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.workspace._docker._docker_workspace import (
    DockerWorkspace,
)
from agentscope.workspace._docker._make_dockerfile import (
    CONTAINER_WORKDIR,
    DEFAULT_GATEWAY_PORT,
)


# ── helpers ───────────────────────────────────────────────────────


class _MockStream:
    """Mock aiodocker Stream for ``exec_obj.start()`` async context."""

    def __init__(self) -> None:
        self._eof = False

    async def read_out(self) -> Any:
        """Return ``None`` on first call to signal EOF."""
        if self._eof:
            return None
        self._eof = True
        return None

    async def __aenter__(self) -> "_MockStream":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        pass


class _MockExecObj:
    """Mock aiodocker exec object returned by ``container.exec()``."""

    def __init__(self) -> None:
        self.start = MagicMock(return_value=_MockStream())

    async def inspect(self) -> dict[str, Any]:
        return {"ExitCode": 0}


def _build_mock_container() -> MagicMock:
    """Build a mock container object matching the aiodocker surface.

    Returns:
        MagicMock with ``start``, ``show``, ``stop``, ``delete`` as
        AsyncMocks and a realistic ``NetworkSettings.Ports`` shape
        returned by ``show()`` so the port-binding extraction works.
        Also mocks ``exec()`` as an AsyncMock returning a
        :class:`_MockExecObj` so ``_exec`` calls succeed.
    """
    container = MagicMock()
    container.start = AsyncMock()
    container.stop = AsyncMock()
    container.delete = AsyncMock()
    container.show = AsyncMock(
        return_value={
            "NetworkSettings": {
                "Ports": {
                    f"{DEFAULT_GATEWAY_PORT}/tcp": [
                        {"HostIp": "127.0.0.1", "HostPort": "49152"},
                    ],
                },
            },
        },
    )
    container.exec = AsyncMock(return_value=_MockExecObj())
    return container


def _build_mock_client(captured_config: dict[str, Any]) -> MagicMock:
    """Build a mock aiodocker.Docker client.

    Args:
        captured_config: A dict that will be populated with the
            ``config`` passed to ``create_or_replace`` so the test
            can inspect it after the call.

    Returns:
        MagicMock with ``containers.create_or_replace`` as an AsyncMock
        that stores its ``config`` kwarg into ``captured_config`` and
        returns a mock container.
    """
    client = MagicMock()
    client.close = AsyncMock()

    mock_container = _build_mock_container()

    async def _capture(name: str, config: dict[str, Any]) -> Any:
        captured_config.clear()
        captured_config.update(config)
        return mock_container

    client.containers.create_or_replace = _capture
    client.containers.get = AsyncMock(return_value=mock_container)
    return client


def _make_workspace_with_mock_client(
    captured_config: dict[str, Any],
    **ws_kwargs: Any,
) -> DockerWorkspace:
    """Construct a DockerWorkspace whose ``_client`` is mocked.

    Bypasses ``_build_or_reuse_image`` by pre-setting
    ``_image_tag`` and injects a mock aiodocker client so
    ``_create_and_start_container`` can run without a daemon.

    Args:
        captured_config: Dict populated with the config passed to
            ``containers.create_or_replace``.
        **ws_kwargs: Forwarded to ``DockerWorkspace.__init__``.

    Returns:
        A DockerWorkspace instance ready to call
        ``_create_and_start_container`` on.
    """
    ws = DockerWorkspace(**ws_kwargs)
    ws._client = _build_mock_client(captured_config)
    ws._image_tag = "agentscope-workspace:test-mock"
    ws._mcps = []
    ws._gateway_token = "test-token"
    return ws


# ── security constraint tests ────────────────────────────────────


class TestDockerWorkspaceHostConfigSecurity(IsolatedAsyncioTestCase):
    """Verify HostConfig carries all expected security constraints.

    Each test focuses on one security dimension so failures point
    directly at the missing hardening. The fixture runs
    ``_create_and_start_container`` once and every assertion inspects
    the same captured ``config``.
    """

    async def asyncSetUp(self) -> None:
        """Run ``_create_and_start_container`` with a mocked client."""
        self.captured_config: dict[str, Any] = {}
        self.workspace = _make_workspace_with_mock_client(
            self.captured_config,
            workspace_id="sec-test",
        )
        await self.workspace._create_and_start_container()
        self.host_config: dict[str, Any] = self.captured_config.get(
            "HostConfig",
            {},
        )

    async def asyncTearDown(self) -> None:
        """Release the mocked workspace."""
        # No real container to stop; just clear references.
        self.workspace._container = None
        self.workspace._client = None

    # ── 1. non-root user ──────────────────────────────────────

    def test_host_config_has_user(self) -> None:
        """HostConfig MUST set ``User`` to a non-root UID:GID.

        Root (UID 0) inside the container grants full access to
        host bind-mounts and any capability leak. The default must
        be a non-zero UID like ``1000:1000``.
        """
        self.assertIn("User", self.host_config, "User missing")
        user = self.host_config["User"]
        self.assertIsInstance(user, str)
        self.assertNotEqual(user, "", "User is empty string")
        self.assertNotEqual(user, "0", "User is root (UID 0)")
        self.assertNotEqual(user, "root", "User is 'root'")
        self.assertNotEqual(user, "0:0", "User is 0:0")

    def test_host_config_user_is_uid_gid_format(self) -> None:
        """``User`` should follow ``UID:GID`` numeric format."""
        user = self.host_config.get("User", "")
        parts = user.split(":")
        self.assertEqual(len(parts), 2, f"Expected UID:GID, got {user!r}")
        for part in parts:
            self.assertTrue(part.isdigit(), f"Non-numeric UID/GID: {part!r}")
            self.assertGreater(int(part), 0, f"UID/GID is 0: {part!r}")

    # ── 2. drop all capabilities ──────────────────────────────

    def test_host_config_drops_all_caps(self) -> None:
        """HostConfig MUST ``CapDrop: ["ALL"]`` to remove all caps.

        Without CapDrop the container inherits the full capability
        set of its user namespace, including ``CAP_SYS_ADMIN`` and
        ``CAP_NET_RAW`` which are dangerous in a sandbox.
        """
        self.assertIn("CapDrop", self.host_config)
        cap_drop = self.host_config["CapDrop"]
        self.assertIsInstance(cap_drop, list)
        self.assertIn("ALL", cap_drop)

    def test_host_config_no_privileged_caps_added(self) -> None:
        """``CapAdd`` MUST NOT include dangerous capabilities."""
        cap_add = self.host_config.get("CapAdd", [])
        dangerous = {
            "CAP_SYS_ADMIN",
            "CAP_NET_RAW",
            "CAP_NET_ADMIN",
            "CAP_SYS_PTRACE",
            "CAP_SYS_MODULE",
            "CAP_DAC_READ_SEARCH",
            "CAP_DAC_OVERRIDE",
            "CAP_FOWNER",
            "CAP_SETUID",
            "CAP_SETGID",
        }
        added = set(cap_add or [])
        overlap = dangerous & added
        self.assertFalse(
            overlap,
            f"Dangerous capabilities added: {overlap}",
        )

    # ── 3. memory limit ───────────────────────────────────────

    def test_host_config_has_memory_limit(self) -> None:
        """HostConfig MUST set ``Memory`` to bound RAM usage."""
        self.assertIn("Memory", self.host_config)
        mem = self.host_config["Memory"]
        self.assertIsInstance(mem, int)
        self.assertGreater(mem, 0, "Memory limit is 0 (unlimited)")

    def test_host_config_memory_default_under_1gb(self) -> None:
        """Default memory limit should be ≤ 1 GiB (sane default)."""
        mem = self.host_config.get("Memory", 0)
        one_gib = 1024 * 1024 * 1024
        self.assertLessEqual(
            mem,
            one_gib,
            f"Memory limit {mem} exceeds 1 GiB",
        )

    def test_host_config_memory_at_least_128mb(self) -> None:
        """Memory limit should be ≥ 128 MiB to allow gateway to run."""
        mem = self.host_config.get("Memory", 0)
        min_mem = 128 * 1024 * 1024
        self.assertGreaterEqual(
            mem,
            min_mem,
            f"Memory limit {mem} below 128 MiB",
        )

    # ── 4. CPU limit ──────────────────────────────────────────

    def test_host_config_has_cpu_limit(self) -> None:
        """HostConfig MUST set ``NanoCpus`` to bound CPU usage."""
        self.assertIn("NanoCpus", self.host_config)
        cpus = self.host_config["NanoCpus"]
        self.assertIsInstance(cpus, int)
        self.assertGreater(cpus, 0, "NanoCpus is 0 (unlimited)")

    def test_host_config_cpu_default_under_2_cores(self) -> None:
        """Default CPU quota should be ≤ 2 cores (1e9 nanocpus = 1 core)."""
        cpus = self.host_config.get("NanoCpus", 0)
        two_cores = 2 * 10**9
        self.assertLessEqual(
            cpus,
            two_cores,
            f"NanoCpus {cpus} exceeds 2 cores",
        )

    # ── 5. process count limit ────────────────────────────────

    def test_host_config_has_pids_limit(self) -> None:
        """HostConfig MUST set ``PidsLimit`` to prevent fork bombs."""
        self.assertIn("PidsLimit", self.host_config)
        pids = self.host_config["PidsLimit"]
        self.assertIsInstance(pids, int)
        self.assertGreater(pids, 0, "PidsLimit is 0 (unlimited)")

    def test_host_config_pids_limit_under_1000(self) -> None:
        """PidsLimit should be ≤ 1000 (sane default around 256)."""
        pids = self.host_config.get("PidsLimit", 0)
        self.assertLessEqual(
            pids,
            1000,
            f"PidsLimit {pids} too high",
        )

    # ── 6. no-new-privileges ──────────────────────────────────

    def test_host_config_has_no_new_privileges(self) -> None:
        """HostConfig MUST include ``no-new-privileges`` in SecurityOpt.

        Without this, a setuid binary inside the container could
        grant the sandboxed process elevated privileges.
        """
        self.assertIn("SecurityOpt", self.host_config)
        sec_opts = self.host_config["SecurityOpt"]
        self.assertIsInstance(sec_opts, list)
        self.assertIn(
            "no-new-privileges",
            sec_opts,
            "no-new-privileges missing from SecurityOpt",
        )

    # ── 7. network isolation ──────────────────────────────────

    def test_host_config_network_not_default_bridge(self) -> None:
        """``NetworkMode`` MUST NOT be the default ``bridge``.

        The default bridge network allows container-to-container
        communication on the same host. Sandboxed workspaces must
        use an isolated network or ``none``.
        """
        net = self.host_config.get("NetworkMode", "bridge")
        self.assertNotEqual(
            net,
            "bridge",
            "NetworkMode is default bridge (not isolated)",
        )
        self.assertNotEqual(
            net,
            "",
            "NetworkMode is empty (defaults to bridge)",
        )

    def test_host_config_network_is_none_or_custom(self) -> None:
        """NetworkMode should be ``none`` or a custom network name."""
        net = self.host_config.get("NetworkMode", "")
        # Allow "none" or a custom-named network (must not be bridge/host)
        allowed_patterns = ("none", "as_ws_", "xruntime_")
        is_custom = any(net.startswith(p) for p in allowed_patterns)
        self.assertTrue(
            net == "none" or is_custom,
            f"NetworkMode {net!r} is not 'none' or a custom network",
        )

    # ── 8. read-only root filesystem ──────────────────────────

    def test_host_config_has_readonly_rootfs(self) -> None:
        """HostConfig SHOULD set ``ReadonlyRootfs: True``.

        Combined with tmpfs mounts for /tmp and /run, this prevents
        the sandbox from writing anywhere except the bind-mounted
        workdir.
        """
        self.assertIn("ReadonlyRootfs", self.host_config)
        self.assertTrue(
            self.host_config["ReadonlyRootfs"],
            "ReadonlyRootfs is not True",
        )

    # ── 9. tmpfs mounts for writable dirs ─────────────────────

    def test_host_config_has_tmpfs_mounts(self) -> None:
        """When ReadonlyRootfs is True, tmpfs MUST cover /tmp and /run."""
        if not self.host_config.get("ReadonlyRootfs"):
            self.skipTest("ReadonlyRootfs not enabled")
        tmpfs = self.host_config.get("Tmpfs", {})
        self.assertIsInstance(tmpfs, dict)
        self.assertIn("/tmp", tmpfs, "/tmp not in Tmpfs")
        self.assertIn("/run", tmpfs, "/run not in Tmpfs")

    def test_host_config_tmpfs_has_size_limit(self) -> None:
        """Each tmpfs mount should have a size limit (e.g. ``size=64m``)."""
        if not self.host_config.get("ReadonlyRootfs"):
            self.skipTest("ReadonlyRootfs not enabled")
        tmpfs = self.host_config.get("Tmpfs", {})
        for mount, opts in tmpfs.items():
            if opts:
                self.assertIn(
                    "size=",
                    opts,
                    f"tmpfs {mount} missing size= in {opts!r}",
                )

    # ── 10. ulimits (file descriptors + processes) ────────────

    def test_host_config_has_ulimits(self) -> None:
        """HostConfig SHOULD set ``Ulimits`` for nofile + nproc."""
        ulimits = self.host_config.get("Ulimits")
        if ulimits is None:
            self.skipTest("Ulimits not implemented (optional hardening)")
        self.assertIsInstance(ulimits, list)
        names = {u.get("Name") for u in ulimits}
        self.assertIn("nofile", names, "nofile ulimit missing")
        self.assertIn("nproc", names, "nproc ulimit missing")

    # ── 11. bind mount preserved ──────────────────────────────

    def test_host_config_preserves_bind_mount(self) -> None:
        """When host_workdir is set, Binds must still be present.

        Security hardening must not break the persistence contract:
        the host workdir should still be bind-mounted to
        ``/workspace`` with ``rw`` mode.
        """
        # This test uses a fresh workspace with host_workdir set.
        captured: dict[str, Any] = {}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace_with_mock_client(
                captured,
                workspace_id="sec-test-bind",
                host_workdir=tmp,
            )
            asyncio.run(ws._create_and_start_container())
        hc = captured.get("HostConfig", {})
        self.assertIn("Binds", hc, "Binds missing when host_workdir set")
        binds = hc["Binds"]
        self.assertIsInstance(binds, list)
        self.assertTrue(
            any(CONTAINER_WORKDIR in b for b in binds),
            f"No bind mount to {CONTAINER_WORKDIR}",
        )
        self.assertTrue(
            any(":rw" in b for b in binds),
            "Bind mount not rw",
        )

    # ── 12. config-driven overrides ───────────────────────────

    def test_memory_override_via_constructor(self) -> None:
        """Constructor ``memory_limit`` overrides default."""
        captured: dict[str, Any] = {}
        ws = _make_workspace_with_mock_client(
            captured,
            workspace_id="sec-test-mem",
            memory_limit=256 * 1024 * 1024,
        )
        asyncio.run(ws._create_and_start_container())
        hc = captured.get("HostConfig", {})
        self.assertEqual(hc.get("Memory"), 256 * 1024 * 1024)

    def test_cpu_override_via_constructor(self) -> None:
        """Constructor ``cpu_limit`` overrides default."""
        captured: dict[str, Any] = {}
        ws = _make_workspace_with_mock_client(
            captured,
            workspace_id="sec-test-cpu",
            cpu_limit=int(0.5 * 10**9),
        )
        asyncio.run(ws._create_and_start_container())
        hc = captured.get("HostConfig", {})
        self.assertEqual(hc.get("NanoCpus"), int(0.5 * 10**9))


# ── env var secrets not leaked into container ──────────────────


class TestDockerWorkspaceEnvSanitization(IsolatedAsyncioTestCase):
    """Verify sensitive env vars are not passed into the container.

    Mirrors Vercel Eve's credential-brokering boundary: API keys
    should be filtered out of the container's Env list so the
    sandboxed process cannot exfiltrate them.
    """

    SENSITIVE_PATTERNS = (
        "API_KEY",
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "CREDENTIAL",
    )

    async def test_no_sensitive_env_in_container_config(self) -> None:
        """Env keys matching sensitive patterns must be filtered out."""
        captured: dict[str, Any] = {}
        ws = _make_workspace_with_mock_client(
            captured,
            workspace_id="sec-test-env",
            env={
                "PATH": "/usr/bin",
                "MODEL_API_KEY": "sk-leak",
                "DB_PASSWORD": "p455w0rd",
                "DEBUG": "true",
            },
        )
        await ws._create_and_start_container()
        env_list = captured.get("Env", [])
        for entry in env_list:
            key = entry.split("=", 1)[0]
            for pat in self.SENSITIVE_PATTERNS:
                self.assertNotIn(
                    pat,
                    key.upper(),
                    f"Sensitive env var {key!r} leaked into container",
                )


if __name__ == "__main__":
    unittest.main()
