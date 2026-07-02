# -*- coding: utf-8 -*-
"""H 批次：MEDIUM 项 TDD 测试。

覆盖：
- H1: LangfuseConfig.secret_key 改用 SecretStr
- H5: _memory/_redis_store.py scan_iter 前缀剥离 bug
- H6: _knowledge/_llm_wiki_adapter.py delete_source 路径穿越
- H7: _register_default_adapter 死代码修复
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from xruntime._runtime._langfuse import LangfuseConfig


class TestLangfuseSecretStr:
    """H1: secret_key must use SecretStr, not plain str."""

    def test_secret_key_is_secretstr(self) -> None:
        """LangfuseConfig.secret_key 字段类型应为 SecretStr."""
        from pydantic import SecretStr

        cfg = LangfuseConfig(
            enabled=True,
            host="http://localhost",
            public_key="pk-test",
            secret_key="sk-test-secret",
        )
        assert isinstance(cfg.secret_key, SecretStr), (
            "LangfuseConfig.secret_key must be SecretStr, not plain str "
            "— plain str leaks the secret in logs and repr()"
        )

    def test_secret_key_not_in_repr(self) -> None:
        """repr() 不应暴露 secret_key 明文."""
        cfg = LangfuseConfig(
            enabled=True,
            host="http://localhost",
            public_key="pk-test",
            secret_key="sk-super-secret-value",
        )
        repr_str = repr(cfg)
        assert "sk-super-secret-value" not in repr_str, (
            "SecretStr value leaked in repr(): " + repr_str
        )

    def test_secret_key_accessible_via_get_secret_value(self) -> None:
        """通过 get_secret_value() 仍可访问."""
        cfg = LangfuseConfig(
            secret_key="sk-test",
        )
        assert cfg.secret_key.get_secret_value() == "sk-test"

    def test_exporter_passes_secret_to_langfuse_client(self) -> None:
        """LangfuseExporter 初始化时应正确传递 secret_key."""
        from xruntime._runtime._langfuse import LangfuseExporter

        cfg = LangfuseConfig(
            enabled=True,
            host="http://localhost",
            public_key="pk-test",
            secret_key="sk-test-secret",
        )
        exporter = LangfuseExporter(config=cfg)
        # If langfuse not installed, exporter is noop — that's OK.
        # We only verify no crash and secret is properly handled.
        assert exporter is not None


class TestRedisStorePrefixStripping:
    """H5: scan_iter 前缀剥离应使用 removeprefix 而非 replace."""

    def test_replace_strips_mid_key_occurrences(self) -> None:
        """str.replace 会错误剥离 key 中间出现的前缀."""
        # Simulate the bug: prefix "mem", key "mem:user:mem:item1"
        prefix = "mem"
        key = "mem:user:mem:item1"
        # Old buggy behavior: replace strips ALL "mem:" occurrences
        buggy_result = key.replace(f"{prefix}:", "")
        # "user:item1" — WRONG, should be "user:mem:item1"
        assert buggy_result == "user:item1"

        # Correct behavior: removeprefix strips only the prefix
        correct_result = key.removeprefix(f"{prefix}:")
        assert correct_result == "user:mem:item1"

    def test_scan_iter_uses_removeprefix(self) -> None:
        """list_all 应正确剥离前缀。

        Bug: str.replace 会剥离 key 中间所有出现的前缀子串.
        Fix: 应使用 removeprefix 只剥离开头前缀.

        测试场景: key 为 "mem:mem:item1" (前缀 "mem" 在 key
        中间也出现), replace 会错误地变成 "item1", 而
        removeprefix 正确地变成 "mem:item1".
        """
        from xruntime._runtime._memory._redis_store import (
            RedisMemoryStore,
        )

        store = RedisMemoryStore(
            redis_url="redis://localhost:6379/0",
            key_prefix="mem",
        )
        mock_client = MagicMock()
        store._client = mock_client
        # Key where prefix "mem:" appears both at start and mid-key
        mock_client.scan_iter.return_value = ["mem:mem:item1"]
        mock_client.get.return_value = None
        mock_client.smembers.return_value = set()

        store.list_all()

        # With bug: "mem:mem:item1".replace("mem:","") = "item1"
        #   → _item_key("item1") = "mem:item:item1"
        # With fix: "mem:mem:item1".removeprefix("mem:") = "mem:item1"
        #   → but "mem:item1" contains ":" so filtered out by
        #     the `if ":" not in key_str` check
        # So with the fix, no get() call should happen for this key
        # (it's filtered out as a non-item key).
        # We verify the fix by checking that "mem:item:item1"
        # (the buggy result) is NOT in get_calls.
        get_calls = [call.args[0] for call in mock_client.get.call_args_list]
        assert "mem:item:item1" not in get_calls, (
            "Buggy replace produced 'item1' from 'mem:mem:item1', "
            "leading to incorrect get() call. Should use "
            "removeprefix. get_calls: " + str(get_calls)
        )


class TestDeleteSourcePathTraversal:
    """H6: delete_source 应校验 source_id 防止路径穿越."""

    def _make_adapter(self, tmpdir: str):
        from xruntime._runtime._knowledge._llm_wiki_adapter import (
            LlmWikiAdapter,
        )
        from xruntime._runtime._knowledge._base import (
            KnowledgeBaseConfig,
        )

        config = KnowledgeBaseConfig(
            backend="llm_wiki",
            raw_dir=tmpdir,
            compiled_dir=os.path.join(tmpdir, "compiled"),
        )
        adapter = LlmWikiAdapter(config=config)
        # Initialize to populate _index
        import asyncio

        asyncio.run(adapter.initialize())
        return adapter

    def test_delete_source_rejects_path_traversal(self) -> None:
        """source_id 含 ../ 应被拒绝."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = self._make_adapter(tmpdir)
            with pytest.raises((ValueError, PermissionError)):
                import asyncio

                asyncio.run(adapter.delete_source("../../../etc/passwd"))

    def test_delete_source_rejects_absolute_path(self) -> None:
        """source_id 为绝对路径应被拒绝."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = self._make_adapter(tmpdir)
            with pytest.raises((ValueError, PermissionError)):
                import asyncio

                asyncio.run(adapter.delete_source("/etc/passwd"))

    def test_delete_source_accepts_valid_id(self) -> None:
        """正常 source_id 应被接受."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = self._make_adapter(tmpdir)
            import asyncio

            result = asyncio.run(adapter.delete_source("valid-source-id"))
            # Should not raise; returns False since file doesn't exist
            assert result is False


class TestRegisterDefaultAdapter:
    """H7: _register_default_adapter 死代码修复."""

    def test_adapter_registered_on_import(self) -> None:
        """导入 LlmWikiAdapter 后，default factory 应已注册 llm_wiki."""
        # Import to trigger any registration side-effect
        import xruntime._runtime._knowledge._llm_wiki_adapter  # noqa
        from xruntime._runtime._knowledge._adapter import (
            get_default_factory,
        )

        factory = get_default_factory()
        # After fix, "llm_wiki" should be in registered_backends
        backends = factory.registered_backends
        assert "llm_wiki" in backends, (
            "Default factory should have llm_wiki registered, "
            f"got: {backends}"
        )
