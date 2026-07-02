# -*- coding: utf-8 -*-
"""Tests for storage._utils._dump_with_secrets.

Verifies that ``_dump_with_secrets`` does NOT expand ``SecretStr``
fields to plaintext by default (fail-closed). Plaintext expansion
is only permitted when the caller explicitly supplies an
``encryptor`` callable.
"""
# pylint: disable=protected-access
from pydantic import BaseModel, SecretStr

from agentscope.app.storage._utils import _dump_with_secrets


class _SampleModel(BaseModel):
    """A model with a SecretStr field for testing."""

    name: str
    api_key: SecretStr = SecretStr("")


class _NoSecretsModel(BaseModel):
    """A model without any SecretStr fields."""

    name: str
    value: int


class TestDumpWithSecretsFailClosed:
    """F1: _dump_with_secrets must not expand SecretStr to plaintext
    unless an explicit encryptor is provided."""

    def test_default_does_not_expose_plaintext(self) -> None:
        """Without an encryptor, the SecretStr value must NOT appear
        in the dumped dict as plaintext."""
        model = _SampleModel(
            name="test",
            api_key=SecretStr("super-secret-key"),
        )
        result = _dump_with_secrets(model)
        assert result["api_key"] != "super-secret-key", (
            "SecretStr was expanded to plaintext without an encryptor "
            "— this is a CRITICAL secret-leak vulnerability"
        )

    def test_default_keeps_masked_form(self) -> None:
        """Without an encryptor, the SecretStr is kept as the masked
        form produced by ``model_dump(mode='json')`` (i.e.
        ``'**********'``)."""
        model = _SampleModel(
            name="test",
            api_key=SecretStr("super-secret-key"),
        )
        result = _dump_with_secrets(model)
        assert result["api_key"] == "**********"

    def test_encryptor_encrypts_secret(self) -> None:
        """When an encryptor callable is provided, the secret value
        is passed through the encryptor and the result replaces the
        masked form."""
        model = _SampleModel(
            name="test",
            api_key=SecretStr("super-secret-key"),
        )
        result = _dump_with_secrets(
            model,
            encryptor=lambda s: f"ENC:{s}",
        )
        assert result["api_key"] == "ENC:super-secret-key"

    def test_non_secret_fields_unchanged(self) -> None:
        """Non-SecretStr fields are unaffected by the encryptor
        parameter."""
        model = _SampleModel(
            name="test",
            api_key=SecretStr("secret"),
        )
        result = _dump_with_secrets(model)
        assert result["name"] == "test"

    def test_no_secretstr_fields(self) -> None:
        """Models without SecretStr fields work normally with or
        without an encryptor."""
        model = _NoSecretsModel(name="test", value=42)
        assert _dump_with_secrets(model) == {"name": "test", "value": 42}
        assert _dump_with_secrets(
            model,
            encryptor=lambda s: f"ENC:{s}",
        ) == {"name": "test", "value": 42}

    def test_encryptor_none_explicit_does_not_leak(self) -> None:
        """Passing encryptor=None explicitly is the same as the
        default — no plaintext leak."""
        model = _SampleModel(
            name="test",
            api_key=SecretStr("do-not-leak"),
        )
        result = _dump_with_secrets(model, encryptor=None)
        assert result["api_key"] != "do-not-leak"
        assert result["api_key"] == "**********"
