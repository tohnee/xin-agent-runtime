# -*- coding: utf-8 -*-
"""Whitelist-based path component validation for ``LlmWikiAdapter``.

The previous implementation used a blacklist (``..``, ``/``,
``os.sep``) which can be bypassed by null bytes, URL encoding,
Unicode normalization, control characters, and empty strings.

These tests assert the new whitelist behaviour: only
``[a-zA-Z0-9_-]`` are accepted.
"""
import pytest

from xruntime._runtime._knowledge._llm_wiki_adapter import (
    LlmWikiAdapter,
)


class TestValidatePathComponentRejectsUnsafe:
    """Values that MUST be rejected by the whitelist."""

    def test_rejects_empty_string(self) -> None:
        """Empty string is not a valid path component."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "",
            )

    def test_rejects_null_byte_suffix(self) -> None:
        """Null byte appended to a valid id must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "valid\x00",
            )

    def test_rejects_null_byte_with_path_traversal(self) -> None:
        """Null byte followed by path traversal must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "kb_id",
                "valid\x00/../../etc/passwd",
            )

    def test_rejects_url_encoded_dot_dot(self) -> None:
        """URL-encoded ``%2e%2e`` must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "%2e%2e",
            )

    def test_rejects_url_encoded_slash(self) -> None:
        """URL-encoded ``%2f`` must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "%2f",
            )

    def test_rejects_unicode_fullwidth_dot(self) -> None:
        """Unicode fullwidth ``．．`` must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "\uff0e\uff0e",
            )

    def test_rejects_unicode_fullwidth_slash(self) -> None:
        """Unicode fullwidth ``／`` must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "\uff0f",
            )

    def test_rejects_single_dot(self) -> None:
        """A single ``.`` is not in the whitelist."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                ".",
            )

    def test_rejects_backslash(self) -> None:
        """Backslash must be rejected even on POSIX."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "valid\\",
            )

    def test_rejects_control_character_newline(self) -> None:
        """Control characters like newline must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "evil\n",
            )

    def test_rejects_space(self) -> None:
        """Spaces are not allowed in path components."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "ev il",
            )

    def test_rejects_absolute_path_fragment(self) -> None:
        """``etc/passwd`` contains ``/`` and must be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "etc/passwd",
            )

    def test_rejects_dot_dot_regression(self) -> None:
        """The original blacklist item ``..`` must still be rejected."""
        with pytest.raises(ValueError):
            LlmWikiAdapter._validate_path_component(
                "tenant_id",
                "..",
            )


class TestValidatePathComponentAcceptsSafe:
    """Values that MUST be accepted by the whitelist."""

    def test_accepts_pure_letters(self) -> None:
        """Pure alphabetic ids are valid."""
        LlmWikiAdapter._validate_path_component("tenant_id", "acme")

    def test_accepts_alphanumeric(self) -> None:
        """Mixed letters and digits are valid."""
        LlmWikiAdapter._validate_path_component("tenant_id", "acme123")

    def test_accepts_underscore(self) -> None:
        """Underscore is in the whitelist."""
        LlmWikiAdapter._validate_path_component(
            "tenant_id",
            "my_tenant",
        )

    def test_accepts_hyphen(self) -> None:
        """Hyphen is in the whitelist."""
        LlmWikiAdapter._validate_path_component(
            "tenant_id",
            "my-tenant",
        )

    def test_accepts_uuid(self) -> None:
        """A canonical UUID string is valid (hyphens allowed)."""
        LlmWikiAdapter._validate_path_component(
            "kb_id",
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        )
