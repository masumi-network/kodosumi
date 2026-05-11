"""Event transport abstraction layer (PR 2: Redis Streams support).

Provides EventProducer/EventConsumer protocols that abstract the
event delivery mechanism between Runner actors and the Spooler.

Default transport is 'ray' (ray.util.queue.Queue) — zero change for
existing deployments. Set KODO_EVENT_TRANSPORT=redis to use Redis Streams.
"""
import os
from typing import Any, Dict, List, Protocol, Tuple, runtime_checkable


@runtime_checkable
class EventProducer(Protocol):
    """Produces execution events from Runner/Tracer to the transport."""

    async def put_async(self, event: dict) -> None: ...
    def put_sync(self, event: dict) -> None: ...
    def shutdown(self) -> None: ...


@runtime_checkable
class EventConsumer(Protocol):
    """Consumes execution events from the transport (Spooler side)."""

    async def read_batch(self, count: int,
                         block_ms: int = 1000) -> List[Tuple[Any, dict]]: ...
    async def ack(self, message_ids: List[Any]) -> None: ...
    async def shutdown(self) -> None: ...


# ---------------------------------------------------------------------------
# Ray Queue transport (default — wraps existing behavior)
# ---------------------------------------------------------------------------

class RayQueueProducer:
    """Wraps ray.util.queue.Queue as an EventProducer."""

    def __init__(self, queue):
        self._queue = queue

    def __reduce__(self):
        return (RayQueueProducer, (self._queue,))

    async def put_async(self, event: dict) -> None:
        await self._queue.put_async(event)

    def put_sync(self, event: dict) -> None:
        self._queue.actor.put.remote(event)  # type: ignore

    def shutdown(self) -> None:
        try:
            self._queue.shutdown()
        except Exception:
            pass

    @property
    def queue(self):
        """Access the underlying ray queue (for Spooler compatibility)."""
        return self._queue


class RayQueueConsumer:
    """Wraps ray.util.queue.Queue as an EventConsumer."""

    def __init__(self, queue):
        self._queue = queue

    async def read_batch(self, count: int,
                         block_ms: int = 1000) -> List[Tuple[Any, dict]]:
        batch = self._queue.get_nowait_batch(
            min(count, self._queue.size()))
        return [(i, item) for i, item in enumerate(batch)]

    async def ack(self, message_ids: List[Any]) -> None:
        pass  # Ray queue doesn't need acknowledgement

    async def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Redis Streams transport
# ---------------------------------------------------------------------------

class RedisStreamProducer:
    """Produces events to a Redis Stream (one stream per execution fid).

    Pickle-safe: the Redis client is created lazily on first use,
    so the producer can be serialized by Ray across actor boundaries.
    """

    def __init__(self, redis_url: str, stream_prefix: str, fid: str,
                 max_stream_len: int = 10000):
        self._redis_url = redis_url
        self._stream_prefix = stream_prefix
        self._fid = fid
        self._max_stream_len = max_stream_len
        self._client = None

    def __reduce__(self):
        return (RedisStreamProducer,
                (self._redis_url, self._stream_prefix, self._fid,
                 self._max_stream_len))

    @property
    def stream_key(self) -> str:
        return f"{self._stream_prefix}{self._fid}"

    def _get_client(self):
        if self._client is None:
            import redis
            self._client = redis.Redis.from_url(
                self._redis_url, decode_responses=False)
        return self._client

    def _xadd(self, event: dict) -> None:
        client = self._get_client()
        data = {
            b"timestamp": str(event.get("timestamp", "")).encode(),
            b"kind": str(event.get("kind", "")).encode(),
            b"payload": str(event.get("payload", "")).encode(),
        }
        client.xadd(self.stream_key, data,
                     maxlen=self._max_stream_len, approximate=True)

    async def put_async(self, event: dict) -> None:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._xadd, event)

    def put_sync(self, event: dict) -> None:
        self._xadd(event)

    def shutdown(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


class RedisStreamConsumer:
    """Consumes events from a Redis Stream using XREADGROUP.

    Uses consumer groups so multiple spooler instances can share the
    work (each message delivered to exactly one consumer in the group).
    """

    def __init__(self, redis_url: str, stream_prefix: str, fid: str,
                 consumer_group: str, consumer_name: str = None):
        self._redis_url = redis_url
        self._stream_prefix = stream_prefix
        self._fid = fid
        self._consumer_group = consumer_group
        self._consumer_name = consumer_name or f"spooler-{os.getpid()}"
        self._client = None

    @property
    def stream_key(self) -> str:
        return f"{self._stream_prefix}{self._fid}"

    def _get_client(self):
        if self._client is None:
            import redis
            self._client = redis.Redis.from_url(
                self._redis_url, decode_responses=False)
            try:
                self._client.xgroup_create(
                    self.stream_key, self._consumer_group,
                    id='0', mkstream=True)
            except Exception:
                pass  # group already exists
        return self._client

    def _xreadgroup(self, count: int, block_ms: int) -> List[Tuple[bytes, dict]]:
        client = self._get_client()
        results = client.xreadgroup(
            self._consumer_group, self._consumer_name,
            {self.stream_key: '>'},
            count=count, block=block_ms)
        if not results:
            return []
        batch = []
        for _stream_name, messages in results:
            for msg_id, data in messages:
                event = {
                    "timestamp": float(data.get(b"timestamp", b"0")),
                    "kind": data.get(b"kind", b"").decode(),
                    "payload": data.get(b"payload", b"").decode(),
                }
                batch.append((msg_id, event))
        return batch

    async def read_batch(self, count: int,
                         block_ms: int = 1000) -> List[Tuple[Any, dict]]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._xreadgroup, count, block_ms)

    def _xack(self, message_ids: list) -> None:
        client = self._get_client()
        client.xack(self.stream_key, self._consumer_group, *message_ids)

    async def ack(self, message_ids: List[Any]) -> None:
        if not message_ids:
            return
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._xack, message_ids)

    async def shutdown(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def create_producer(fid: str, event_transport: str = "ray",
                    redis_url: str = None,
                    redis_stream_prefix: str = "kodo:events:"):
    """Create an EventProducer for a given execution fid.

    Returns (producer, raw_queue_or_None). The raw queue is only set
    for 'ray' transport so the Runner can expose it via get_queue().
    """
    if event_transport == "redis":
        producer = RedisStreamProducer(redis_url, redis_stream_prefix, fid)
        return producer, None
    else:
        import ray.util.queue
        queue = ray.util.queue.Queue()
        producer = RayQueueProducer(queue)
        return producer, queue


def create_consumer(fid: str, event_transport: str = "ray",
                    redis_url: str = None,
                    redis_stream_prefix: str = "kodo:events:",
                    redis_consumer_group: str = "kodo-spooler"):
    """Create an EventConsumer for a given execution fid.

    For 'ray' transport, returns None (the Spooler reads from the
    Runner's queue directly). For 'redis', returns a RedisStreamConsumer.
    """
    if event_transport == "redis":
        return RedisStreamConsumer(
            redis_url=redis_url,
            stream_prefix=redis_stream_prefix,
            fid=fid,
            consumer_group=redis_consumer_group)
    return None
