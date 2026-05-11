"""Tests for the event transport abstraction layer (PR 2: Redis Streams).

These tests validate the transport protocols, Ray queue wrappers,
and Redis stream producer/consumer without requiring Ray or Redis.
Run with: pytest tests/test_transport.py -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    """Verify that concrete classes satisfy the Protocol contracts."""

    def test_ray_queue_producer_is_event_producer(self):
        from kodosumi.transport import EventProducer, RayQueueProducer
        mock_queue = MagicMock()
        producer = RayQueueProducer(mock_queue)
        assert isinstance(producer, EventProducer)

    def test_ray_queue_consumer_is_event_consumer(self):
        from kodosumi.transport import EventConsumer, RayQueueConsumer
        mock_queue = MagicMock()
        consumer = RayQueueConsumer(mock_queue)
        assert isinstance(consumer, EventConsumer)

    def test_redis_stream_producer_is_event_producer(self):
        from kodosumi.transport import EventProducer, RedisStreamProducer
        producer = RedisStreamProducer(
            "redis://localhost:6379", "kodo:events:", "fid123")
        assert isinstance(producer, EventProducer)

    def test_redis_stream_consumer_is_event_consumer(self):
        from kodosumi.transport import EventConsumer, RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost:6379", "kodo:events:", "fid123",
            "kodo-spooler")
        assert isinstance(consumer, EventConsumer)


# ---------------------------------------------------------------------------
# RayQueueProducer tests
# ---------------------------------------------------------------------------

class TestRayQueueProducer:

    def test_put_sync_calls_actor_remote(self):
        from kodosumi.transport import RayQueueProducer
        mock_queue = MagicMock()
        producer = RayQueueProducer(mock_queue)
        event = {"timestamp": 1.0, "kind": "status", "payload": "running"}
        producer.put_sync(event)
        mock_queue.actor.put.remote.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_put_async_calls_queue_put_async(self):
        from kodosumi.transport import RayQueueProducer
        mock_queue = MagicMock()
        mock_queue.put_async = MagicMock(
            return_value=self._make_awaitable(None))
        producer = RayQueueProducer(mock_queue)
        event = {"timestamp": 1.0, "kind": "status", "payload": "running"}
        await producer.put_async(event)
        mock_queue.put_async.assert_called_once_with(event)

    def test_queue_property_returns_underlying_queue(self):
        from kodosumi.transport import RayQueueProducer
        mock_queue = MagicMock()
        producer = RayQueueProducer(mock_queue)
        assert producer.queue is mock_queue

    def test_shutdown_calls_queue_shutdown(self):
        from kodosumi.transport import RayQueueProducer
        mock_queue = MagicMock()
        producer = RayQueueProducer(mock_queue)
        producer.shutdown()
        mock_queue.shutdown.assert_called_once()

    def test_shutdown_swallows_exceptions(self):
        from kodosumi.transport import RayQueueProducer
        mock_queue = MagicMock()
        mock_queue.shutdown.side_effect = RuntimeError("already dead")
        producer = RayQueueProducer(mock_queue)
        producer.shutdown()  # should not raise

    @staticmethod
    async def _make_awaitable(value):
        return value


# ---------------------------------------------------------------------------
# RayQueueConsumer tests
# ---------------------------------------------------------------------------

class TestRayQueueConsumer:

    @pytest.mark.asyncio
    async def test_read_batch_returns_indexed_tuples(self):
        from kodosumi.transport import RayQueueConsumer
        mock_queue = MagicMock()
        events = [
            {"timestamp": 1.0, "kind": "status", "payload": "a"},
            {"timestamp": 2.0, "kind": "result", "payload": "b"},
        ]
        mock_queue.size.return_value = 2
        mock_queue.get_nowait_batch.return_value = events
        consumer = RayQueueConsumer(mock_queue)
        batch = await consumer.read_batch(10)
        assert len(batch) == 2
        assert batch[0] == (0, events[0])
        assert batch[1] == (1, events[1])

    @pytest.mark.asyncio
    async def test_read_batch_empty_queue(self):
        from kodosumi.transport import RayQueueConsumer
        mock_queue = MagicMock()
        mock_queue.size.return_value = 0
        mock_queue.get_nowait_batch.return_value = []
        consumer = RayQueueConsumer(mock_queue)
        batch = await consumer.read_batch(10)
        assert batch == []

    @pytest.mark.asyncio
    async def test_ack_is_noop(self):
        from kodosumi.transport import RayQueueConsumer
        consumer = RayQueueConsumer(MagicMock())
        await consumer.ack([1, 2, 3])  # should not raise


# ---------------------------------------------------------------------------
# RedisStreamProducer tests (mocked Redis client)
# ---------------------------------------------------------------------------

class TestRedisStreamProducer:

    def test_init_stores_config(self):
        from kodosumi.transport import RedisStreamProducer
        producer = RedisStreamProducer(
            "redis://localhost:6379", "kodo:events:", "fid-abc")
        assert producer.stream_key == "kodo:events:fid-abc"
        assert producer._client is None

    def test_stream_key_format(self):
        from kodosumi.transport import RedisStreamProducer
        producer = RedisStreamProducer(
            "redis://localhost", "prefix:", "my-fid")
        assert producer.stream_key == "prefix:my-fid"

    def test_put_sync_calls_xadd(self):
        from kodosumi.transport import RedisStreamProducer
        producer = RedisStreamProducer(
            "redis://localhost", "kodo:", "fid1")
        mock_client = MagicMock()
        producer._client = mock_client
        event = {"timestamp": 1.5, "kind": "status", "payload": "running"}
        producer.put_sync(event)
        mock_client.xadd.assert_called_once()
        call_args = mock_client.xadd.call_args
        assert call_args[0][0] == "kodo:fid1"
        data = call_args[0][1]
        assert data[b"kind"] == b"status"
        assert data[b"payload"] == b"running"

    def test_shutdown_closes_client(self):
        from kodosumi.transport import RedisStreamProducer
        producer = RedisStreamProducer(
            "redis://localhost", "kodo:", "fid1")
        mock_client = MagicMock()
        producer._client = mock_client
        producer.shutdown()
        mock_client.close.assert_called_once()
        assert producer._client is None

    def test_shutdown_noop_without_client(self):
        from kodosumi.transport import RedisStreamProducer
        producer = RedisStreamProducer(
            "redis://localhost", "kodo:", "fid1")
        producer.shutdown()  # should not raise

    def test_reduce_is_pickle_safe(self):
        from kodosumi.transport import RedisStreamProducer
        producer = RedisStreamProducer(
            "redis://localhost:6379", "kodo:events:", "fid-abc", 5000)
        cls, args = producer.__reduce__()
        assert cls is RedisStreamProducer
        assert args == ("redis://localhost:6379", "kodo:events:",
                        "fid-abc", 5000)
        reconstructed = cls(*args)
        assert reconstructed.stream_key == "kodo:events:fid-abc"
        assert reconstructed._client is None


# ---------------------------------------------------------------------------
# RedisStreamConsumer tests (mocked Redis client)
# ---------------------------------------------------------------------------

class TestRedisStreamConsumer:

    def test_init_stores_config(self):
        from kodosumi.transport import RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost", "kodo:", "fid1",
            "spooler-group", "consumer-1")
        assert consumer.stream_key == "kodo:fid1"
        assert consumer._consumer_group == "spooler-group"
        assert consumer._consumer_name == "consumer-1"

    def test_default_consumer_name_uses_pid(self):
        import os
        from kodosumi.transport import RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost", "kodo:", "fid1", "group")
        assert consumer._consumer_name == f"spooler-{os.getpid()}"

    @pytest.mark.asyncio
    async def test_read_batch_parses_redis_messages(self):
        from kodosumi.transport import RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost", "kodo:", "fid1", "group", "c1")
        mock_client = MagicMock()
        consumer._client = mock_client
        mock_client.xreadgroup.return_value = [
            (b"kodo:fid1", [
                (b"1-0", {
                    b"timestamp": b"1.5",
                    b"kind": b"status",
                    b"payload": b"running",
                }),
                (b"2-0", {
                    b"timestamp": b"2.0",
                    b"kind": b"result",
                    b"payload": b'{"data": 42}',
                }),
            ])
        ]
        batch = await consumer.read_batch(10, block_ms=100)
        assert len(batch) == 2
        msg_id, event = batch[0]
        assert msg_id == b"1-0"
        assert event["kind"] == "status"
        assert event["payload"] == "running"
        assert event["timestamp"] == 1.5

    @pytest.mark.asyncio
    async def test_read_batch_empty_returns_empty(self):
        from kodosumi.transport import RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost", "kodo:", "fid1", "group", "c1")
        mock_client = MagicMock()
        consumer._client = mock_client
        mock_client.xreadgroup.return_value = None
        batch = await consumer.read_batch(10, block_ms=100)
        assert batch == []

    @pytest.mark.asyncio
    async def test_ack_calls_xack(self):
        from kodosumi.transport import RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost", "kodo:", "fid1", "group", "c1")
        mock_client = MagicMock()
        consumer._client = mock_client
        await consumer.ack([b"1-0", b"2-0"])
        mock_client.xack.assert_called_once_with(
            "kodo:fid1", "group", b"1-0", b"2-0")

    @pytest.mark.asyncio
    async def test_ack_empty_is_noop(self):
        from kodosumi.transport import RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost", "kodo:", "fid1", "group", "c1")
        mock_client = MagicMock()
        consumer._client = mock_client
        await consumer.ack([])
        mock_client.xack.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_closes_client(self):
        from kodosumi.transport import RedisStreamConsumer
        consumer = RedisStreamConsumer(
            "redis://localhost", "kodo:", "fid1", "group")
        mock_client = MagicMock()
        consumer._client = mock_client
        await consumer.shutdown()
        mock_client.close.assert_called_once()
        assert consumer._client is None


# ---------------------------------------------------------------------------
# Factory function tests
# ---------------------------------------------------------------------------

class TestFactoryFunctions:

    def test_create_consumer_ray_returns_none(self):
        from kodosumi.transport import create_consumer
        consumer = create_consumer("fid1", event_transport="ray")
        assert consumer is None

    def test_create_consumer_redis_returns_consumer(self):
        from kodosumi.transport import RedisStreamConsumer, create_consumer
        consumer = create_consumer(
            "fid1", event_transport="redis",
            redis_url="redis://localhost:6379",
            redis_stream_prefix="kodo:events:",
            redis_consumer_group="my-group")
        assert isinstance(consumer, RedisStreamConsumer)
        assert consumer.stream_key == "kodo:events:fid1"
        assert consumer._consumer_group == "my-group"


# ---------------------------------------------------------------------------
# Config integration tests
# ---------------------------------------------------------------------------

class TestConfigIntegration:
    """Verify that Settings has the transport fields."""

    def test_default_transport_is_ray(self):
        from kodosumi.config import Settings
        s = Settings()
        assert s.EVENT_TRANSPORT == "ray"
        assert s.REDIS_URL is None
        assert s.REDIS_STREAM_PREFIX == "kodo:events:"
        assert s.REDIS_CONSUMER_GROUP == "kodo-spooler"
        assert s.REDIS_BLOCK_MS == 1000
        assert s.REDIS_MAX_STREAM_LEN == 10000
