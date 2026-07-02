# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the atomic lock-release path of :class:`RedisMessageBus`.

These tests pin down the fix for the non-atomic GET+DEL race window in
:meth:`RedisMessageBus.acquire_lock`. The release path must use a Lua
``eval`` (compare-and-delete) instead of separate ``get`` and
``delete`` calls, so a concurrent acquirer cannot have its lock
deleted by a slow predecessor.
"""
from typing import Any

from agentscope.app.message_bus import RedisMessageBus


class _FakeRedisClient:
    """Minimal async Redis client mock.

    Records ``eval`` / ``get`` / ``delete`` invocations and lets each
    test configure return values and an optional ``eval`` exception.
    """

    def __init__(self) -> None:
        self.set_calls: list[tuple[Any, ...]] = []
        self.get_calls: list[Any] = []
        self.delete_calls: list[Any] = []
        self.eval_calls: list[tuple[Any, ...]] = []
        self.expire_calls: list[tuple[Any, ...]] = []

        self.set_return: Any = True
        self.get_return: Any = None
        self.delete_return: int = 0
        self.eval_return: int = 1
        self.eval_exception: BaseException | None = None

    async def set(  # noqa: A003  # pylint: disable=redefined-builtin
        self, key: str, token: str, *, nx: bool = False, ex: int | None = None
    ) -> Any:
        self.set_calls.append((key, token, nx, ex))
        return self.set_return

    async def get(self, key: str) -> Any:
        self.get_calls.append(key)
        return self.get_return

    async def delete(self, key: str) -> int:
        self.delete_calls.append(key)
        return self.delete_return

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True

    async def eval(  # noqa: A003  # pylint: disable=redefined-builtin
        self, script: str, numkeys: int, *args: Any
    ) -> Any:
        self.eval_calls.append((script, numkeys, *args))
        if self.eval_exception is not None:
            raise self.eval_exception
        return self.eval_return


def _make_bus(client: _FakeRedisClient) -> RedisMessageBus:
    """Build a :class:`RedisMessageBus` wired to *client*.

    Mirrors the ``_FakeBus`` pattern in ``service_message_bus_test``:
    ``__aenter__`` installs the fake client instead of opening a real
    connection pool.
    """

    class _FakeBus(RedisMessageBus):
        async def __aenter__(self) -> "RedisMessageBus":
            self._client = client
            return self

        async def aclose(self) -> None:
            self._client = None

    return _FakeBus()


async def test_release_uses_eval_not_get_delete() -> None:
    """Release path must call ``eval`` (Lua), not ``get`` + ``delete``."""
    client = _FakeRedisClient()
    async with _make_bus(client) as bus:
        async with bus.acquire_lock("k", ttl_secs=10):
            pass
    assert client.eval_calls, "release must call eval"
    assert (
        not client.delete_calls
    ), "release must not call delete directly; the Lua script owns it"
    assert (
        not client.get_calls
    ), "release must not call get directly; the Lua script owns it"


async def test_release_eval_called_with_correct_script() -> None:
    """The Lua script passed to ``eval`` must compare-and-delete."""
    client = _FakeRedisClient()
    async with _make_bus(client) as bus:
        async with bus.acquire_lock("k", ttl_secs=10):
            pass
    assert client.eval_calls, "release must call eval"
    script = client.eval_calls[0][0]
    assert (
        'redis.call("get"' in script
    ), "script must read the current value with redis.call('get', ...)"
    assert (
        'redis.call("del"' in script
    ), "script must delete on match with redis.call('del', ...)"


async def test_release_eval_passes_key_and_token() -> None:
    """``eval`` must receive one key and the acquire token as args."""
    client = _FakeRedisClient()
    async with _make_bus(client) as bus:
        async with bus.acquire_lock("my-lock", ttl_secs=10):
            pass
    assert len(client.eval_calls) == 1
    _script, numkeys, *rest = client.eval_calls[0]
    assert numkeys == 1, "exactly one Redis key is touched"
    assert rest == [
        "my-lock",
        client.set_calls[0][1],
    ], "eval args must be (key, token) where token matches acquire"


async def test_release_swallows_exception() -> None:
    """An exception from ``eval`` during release must not propagate."""
    client = _FakeRedisClient()
    client.eval_exception = RuntimeError("redis eval blew up")
    # Should not raise: release failures are swallowed.
    async with _make_bus(client) as bus:
        async with bus.acquire_lock("k", ttl_secs=10):
            pass


async def test_acquire_then_release_full_flow() -> None:
    """Acquire stores the token via ``SET NX``; release ``eval``s it."""
    client = _FakeRedisClient()
    async with _make_bus(client) as bus:
        async with bus.acquire_lock("flow", ttl_secs=30):
            # While inside: SET NX must have claimed the lock with a
            # random token.
            assert len(client.set_calls) == 1
            key, token, nx, ex = client.set_calls[0]
            assert key == "flow"
            assert nx is True
            assert ex == 30
        # After exit: release must have eval'd the Lua script with the
        # same key+token.
        assert len(client.eval_calls) == 1
        _script, numkeys, *args = client.eval_calls[0]
        assert numkeys == 1
        assert args == [key, token]


async def test_release_with_wrong_token_does_not_delete() -> None:
    """Token mismatch must be handled inside the Lua script's else branch.

    We cannot execute Lua against a fake client, so we assert the
    script shipped to ``eval`` contains an ``else`` branch that
    returns ``0`` without deleting — i.e. the compare-and-delete logic
    is owned by the script, not by separate ``get``/``delete`` calls
    that would race.
    """
    client = _FakeRedisClient()
    async with _make_bus(client) as bus:
        async with bus.acquire_lock("k", ttl_secs=10):
            pass
    assert client.eval_calls, "release must delegate to eval"
    script = client.eval_calls[0][0]
    assert "else" in script, "script must have an else branch"
    assert (
        "return 0" in script
    ), "script must return 0 (no-op) on token mismatch"
    # And the bus must never have called delete itself — only the
    # script may, conditionally.
    assert not client.delete_calls
