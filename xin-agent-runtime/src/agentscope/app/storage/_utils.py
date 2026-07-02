# -*- coding: utf-8 -*-
"""The utils for storage."""
from typing import Callable, Optional

from pydantic import BaseModel, SecretStr


def _dump_with_secrets(
    model: BaseModel,
    encryptor: Optional[Callable[[str], str]] = None,
) -> dict:
    """Dump the BaseModel instance with SecretStr fields. Used for
    storage.

    Fail-closed by default: when ``encryptor`` is not provided (or is
    ``None``), SecretStr fields are kept in their masked form
    (``'**********'``) as produced by ``model_dump(mode='json')``.
    The plaintext secret is NEVER exposed unless the caller explicitly
    supplies an ``encryptor`` callable that transforms the secret
    value (e.g. via a KMS-backed encryption routine).

    Args:
        model (`BaseModel`):
            The model instance to dump.
        encryptor (`Callable[[str], str] | None`, optional):
            Optional callable used to transform SecretStr plaintext
            before it is written into the dumped dict. When ``None``
            (default), the masked form is retained and no plaintext
            is exposed. When provided, the callable receives the
            raw secret string and must return a string to store.

    Returns:
        `dict`:
            The dumped JSON. SecretStr fields are either masked
            (default) or transformed via ``encryptor``.
    """
    # Use mode='json' so that Pydantic converts non-JSON-native types
    # (e.g. datetime, UUID) to their JSON-compatible representations.
    # SecretStr fields will be masked at this step.
    result = model.model_dump(mode="json")

    for field_name, _ in model.__class__.model_fields.items():
        value = getattr(model, field_name)
        if isinstance(value, SecretStr):
            if encryptor is not None:
                result[field_name] = encryptor(
                    value.get_secret_value(),
                )
            # When encryptor is None, keep the masked form produced by
            # model_dump(mode='json'). Do NOT call get_secret_value()
            # — that would leak plaintext to storage.

    return result
