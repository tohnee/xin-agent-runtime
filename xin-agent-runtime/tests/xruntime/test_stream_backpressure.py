# -*- coding: utf-8 -*-
"""Tests for stream backpressure — bounded queue + slow-client handling.

This is the TDD test file for the P0 issue:
"asyncio.Queue 默认无界，高并发大输出慢客户端时内存压力大"
"""
import asyncio
import pytest


class TestStreamBackpressure:
    """Tests for bounded streaming queue and backpressure."""

    def test_bounded_queue_constant_exists(self) -> None:
        """A STREAM_QUEUE_MAXSIZE constant should be defined."""
        from xruntime._gateway._extension import STREAM_QUEUE_MAXSIZE

        assert isinstance(STREAM_QUEUE_MAXSIZE, int)
        assert STREAM_QUEUE_MAXSIZE > 0

    def test_serialize_stream_uses_bounded_queue(self) -> None:
        """_serialize_stream should create a bounded queue with maxsize."""
        import inspect
        from xruntime._gateway import _extension

        source = inspect.getsource(_extension._serialize_stream)
        # Should reference the maxsize constant or a numeric bound
        assert "maxsize" in source or "STREAM_QUEUE_MAXSIZE" in source

    @pytest.mark.asyncio
    async def test_bounded_queue_blocks_when_full(self) -> None:
        """A bounded queue should block put() when full (backpressure)."""
        from xruntime._gateway._extension import STREAM_QUEUE_MAXSIZE

        q: asyncio.Queue[int] = asyncio.Queue(maxsize=STREAM_QUEUE_MAXSIZE)

        # Fill the queue to capacity
        for i in range(STREAM_QUEUE_MAXSIZE):
            q.put_nowait(i)

        assert q.full()

        # A put_nowait should raise QueueFull
        with pytest.raises(asyncio.QueueFull):
            q.put_nowait(999)

    @pytest.mark.asyncio
    async def test_slow_client_triggers_backpressure(self) -> None:
        """When the consumer (client) is slow, the queue should fill up.

        This simulates a slow client: producer adds events faster than
        the consumer reads them. The bounded queue should provide
        backpressure instead of growing unboundedly.
        """
        from xruntime._gateway._extension import STREAM_QUEUE_MAXSIZE

        max_size = STREAM_QUEUE_MAXSIZE
        q: asyncio.Queue[int] = asyncio.Queue(maxsize=max_size)
        produced = 0
        consumed = 0

        async def fast_producer() -> None:
            nonlocal produced
            for i in range(max_size * 3):
                try:
                    q.put_nowait(i)
                    produced += 1
                except asyncio.QueueFull:
                    # Backpressure: can't put more, producer is blocked
                    break

        async def slow_consumer() -> None:
            nonlocal consumed
            # Consumer reads slower than producer
            while consumed < max_size:
                await asyncio.sleep(0.001)
                try:
                    q.get_nowait()
                    consumed += 1
                except asyncio.QueueEmpty:
                    break

        await fast_producer()
        # Producer should have hit the bound, not produced all items
        assert produced <= max_size

        # Now let consumer catch up
        await slow_consumer()
        assert consumed == max_size

    def test_backpressure_constant_is_reasonable(self) -> None:
        """The max size should be a reasonable value (100-10000 range)."""
        from xruntime._gateway._extension import STREAM_QUEUE_MAXSIZE

        # Not too small (would cause unnecessary blocking),
        # not too large (would defeat the purpose).
        assert 100 <= STREAM_QUEUE_MAXSIZE <= 10000
