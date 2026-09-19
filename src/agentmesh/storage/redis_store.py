"""Redis-backed store: shared run state, a durable event log and a work queue.

Key layout (``{ns}`` = ``AGENTMESH_REDIS_NAMESPACE``)
---------------------------------------------------

==========================================  ==============================================
Key                                         Type / purpose
==========================================  ==============================================
``{ns}:run:{id}``                           STRING  JSON run document (TTL = redis_ttl)
``{ns}:run:{id}:events``                    STREAM  append-only event log (MAXLEN capped)
``{ns}:run:{id}:seq``                       STRING  event sequence counter (INCR)
``{ns}:run:{id}:cancel``                    STRING  cancellation flag (TTL)
``{ns}:runs:index``                         ZSET    score = created_at, member = run id
``{ns}:runs:queue``                         STREAM  work queue + consumer group
==========================================  ==============================================

The event log is a Redis stream rather than pub/sub on purpose: SSE clients can
reconnect with ``Last-Event-ID`` and replay anything they missed.
"""

from __future__ import annotations

import uuid
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError, ResponseError

from agentmesh.config import Settings, get_settings
from agentmesh.errors import StoreError
from agentmesh.logger import get_logger
from agentmesh.schemas import RunEvent, RunRecord, RunStatus, utcnow
from agentmesh.storage.base import QueueItem, RunStore

log = get_logger("storage.redis")

GROUP_ALREADY_EXISTS = "BUSYGROUP"


class RedisStore(RunStore):
    """Redis implementation of :class:`~agentmesh.storage.base.RunStore`."""

    name = "redis"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        namespace = self._settings.redis_namespace
        self._ns = namespace
        self._queue_key = f"{namespace}:runs:queue"
        self._index_key = f"{namespace}:runs:index"
        self._group = f"{namespace}:workers"
        self._consumer = f"{namespace}-{uuid.uuid4().hex[:8]}"
        self._redis: aioredis.Redis | None = None

    # --------------------------------------------------------------- lifecycle
    async def startup(self) -> None:
        if self._redis is not None:
            return
        self._redis = aioredis.from_url(
            self._settings.redis_url,
            decode_responses=True,
            socket_timeout=self._settings.redis_socket_timeout,
            health_check_interval=30,
        )
        await self.ping_or_raise()
        try:
            await self._client.xgroup_create(self._queue_key, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if GROUP_ALREADY_EXISTS not in str(exc).upper():
                raise StoreError(f"cannot create consumer group {self._group!r}: {exc}") from exc
        log.info(
            "redis store ready",
            extra={"url": self._settings.masked_redis_url, "group": self._group, "consumer": self._consumer},
        )

    async def shutdown(self) -> None:
        if self._redis is None:
            return
        closer = getattr(self._redis, "aclose", None) or self._redis.close
        await closer()
        self._redis = None

    @property
    def _client(self) -> aioredis.Redis:
        if self._redis is None:
            raise StoreError("RedisStore.startup() must be awaited before use")
        return self._redis

    async def ping_or_raise(self) -> None:
        try:
            await self._client.ping()
        except RedisError as exc:
            raise StoreError(f"cannot reach Redis at {self._settings.masked_redis_url}: {exc}") from exc

    async def ping(self) -> bool:
        try:
            await self._client.ping()
        except (RedisError, StoreError) as exc:
            log.warning("redis ping failed", extra={"error": str(exc)})
            return False
        return True

    # --------------------------------------------------------------- key helpers
    def _run_key(self, run_id: str) -> str:
        return f"{self._ns}:run:{run_id}"

    def _events_key(self, run_id: str) -> str:
        return f"{self._ns}:run:{run_id}:events"

    def _seq_key(self, run_id: str) -> str:
        return f"{self._ns}:run:{run_id}:seq"

    def _cancel_key(self, run_id: str) -> str:
        return f"{self._ns}:run:{run_id}:cancel"

    # ---------------------------------------------------------- run records
    async def save_run(self, record: RunRecord) -> None:
        ttl = self._settings.redis_ttl_seconds
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                pipe.set(self._run_key(record.id), record.model_dump_json(), ex=ttl)
                pipe.zadd(self._index_key, {record.id: record.created_at.timestamp()})
                pipe.zremrangebyrank(self._index_key, 0, -(self._settings.run_history_limit + 1))
                pipe.expire(self._index_key, ttl * 30)
                await pipe.execute()
        except RedisError as exc:
            raise StoreError(f"cannot persist run {record.id}: {exc}") from exc

    async def get_run(self, run_id: str) -> RunRecord | None:
        try:
            payload = await self._client.get(self._run_key(run_id))
        except RedisError as exc:
            raise StoreError(f"cannot read run {run_id}: {exc}") from exc
        if not payload:
            return None
        return _parse_record(_text(payload))

    async def list_runs(self, limit: int = 50, status: RunStatus | None = None) -> list[RunRecord]:
        try:
            fetch = limit * 5 if status is not None else limit
            run_ids = await self._client.zrevrange(self._index_key, 0, max(fetch - 1, 0))
            if not run_ids:
                return []
            payloads = await self._client.mget([self._run_key(_text(run_id)) for run_id in run_ids])
        except RedisError as exc:
            raise StoreError(f"cannot list runs: {exc}") from exc

        records: list[RunRecord] = []
        for payload in payloads:
            if not payload:
                continue
            record = _parse_record(_text(payload))
            if record is not None:
                records.append(record)
        if status is not None:
            records = [record for record in records if record.status is status]
        return records[:limit]

    # ------------------------------------------------------------ event log
    async def append_event(self, event: RunEvent) -> RunEvent:
        run_id = event.run_id
        try:
            seq = await self._client.incr(self._seq_key(run_id))
            await self._client.expire(self._seq_key(run_id), self._settings.redis_ttl_seconds)
            event.seq = int(seq)
            payload = event.model_dump_json(exclude={"id"})
            stream_id = await self._client.xadd(
                self._events_key(run_id),
                {"data": payload},
                maxlen=self._settings.redis_stream_maxlen,
                approximate=True,
            )
            await self._client.expire(self._events_key(run_id), self._settings.redis_ttl_seconds)
        except RedisError as exc:
            raise StoreError(f"cannot append event {event.type} for {run_id}: {exc}") from exc
        event.id = _text(stream_id)
        return event

    async def read_events(
        self,
        run_id: str,
        *,
        after: str | None = None,
        block_ms: int | None = None,
        limit: int = 500,
    ) -> list[RunEvent]:
        key = self._events_key(run_id)
        try:
            if block_ms:
                response = await self._client.xread({key: after or "0"}, count=limit, block=int(block_ms))
                entries = _xread_entries(response)
            else:
                entries = _stream_entries(
                    await self._client.xrange(key, min=f"({after}" if after else "-", max="+", count=limit)
                )
        except RedisError as exc:
            raise StoreError(f"cannot read events for {run_id}: {exc}") from exc
        return [_parse_event(stream_id, fields) for stream_id, fields in entries if fields.get("data")]

    # ---------------------------------------------------------- cancellation
    async def request_cancel(self, run_id: str) -> bool:
        try:
            if not await self._client.exists(self._run_key(run_id)):
                return False
            await self._client.set(self._cancel_key(run_id), "1", ex=self._settings.redis_ttl_seconds)
        except RedisError as exc:
            raise StoreError(f"cannot cancel run {run_id}: {exc}") from exc
        return True

    async def is_cancelled(self, run_id: str) -> bool:
        try:
            return bool(await self._client.exists(self._cancel_key(run_id)))
        except RedisError as exc:
            raise StoreError(f"cannot inspect cancellation for {run_id}: {exc}") from exc

    # ----------------------------------------------------------------- queue
    async def enqueue_run(self, run_id: str) -> None:
        try:
            await self._client.xadd(
                self._queue_key,
                {"run_id": run_id, "enqueued_at": utcnow().isoformat()},
                maxlen=self._settings.redis_stream_maxlen,
                approximate=True,
            )
        except RedisError as exc:
            raise StoreError(f"cannot enqueue run {run_id}: {exc}") from exc

    async def dequeue_run(self, *, timeout_s: float = 5.0) -> QueueItem | None:
        try:
            response = await self._client.xreadgroup(
                self._group,
                self._consumer,
                {self._queue_key: ">"},
                count=1,
                block=int(timeout_s * 1000),
            )
        except RedisError as exc:
            raise StoreError(f"cannot read from the run queue: {exc}") from exc
        if not response:
            return None
        for stream_id, fields in _xread_entries(response):
            run_id = fields.get("run_id")
            if run_id:
                return QueueItem(run_id=run_id, token=stream_id)
        return None

    async def ack_run(self, item: QueueItem) -> None:
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                pipe.xack(self._queue_key, self._group, item.token)
                pipe.xdel(self._queue_key, item.token)
                await pipe.execute()
        except RedisError as exc:
            raise StoreError(f"cannot acknowledge {item.run_id}: {exc}") from exc

    async def queue_depth(self) -> int:
        try:
            return int(await self._client.xlen(self._queue_key))
        except RedisError as exc:
            raise StoreError(f"cannot read the queue depth: {exc}") from exc

    async def recover_stale(self, *, min_idle_ms: int, limit: int = 32) -> list[QueueItem]:
        """Reclaim messages from workers that crashed before acknowledging."""
        try:
            result: Any = await self._client.xautoclaim(
                self._queue_key,
                self._group,
                self._consumer,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=limit,
            )
        except (RedisError, ResponseError) as exc:
            log.warning("XAUTOCLAIM failed", extra={"error": str(exc)})
            return []

        entries = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
        reclaimed: list[QueueItem] = []
        for entry in entries or []:
            stream_id, fields = entry
            run_id = (fields or {}).get("run_id")
            if run_id:
                reclaimed.append(QueueItem(run_id=run_id, token=stream_id))
        if reclaimed:
            log.info("reclaimed stale queue items", extra={"count": len(reclaimed)})
        return reclaimed


def _text(value: Any) -> str:
    """redis-py types every reply as ``bytes | str``: ``decode_responses`` is a
    runtime flag, so the decoding lives here instead of on each call site."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def _stream_entries(reply: Any) -> list[tuple[str, dict[str, str]]]:
    """Normalise an ``XRANGE``/``XREAD`` batch into ``(stream_id, fields)`` pairs."""
    pairs: list[tuple[str, dict[str, str]]] = []
    for stream_id, fields in reply or []:
        pairs.append((_text(stream_id), {_text(key): _text(item) for key, item in (fields or {}).items()}))
    return pairs


def _xread_entries(reply: Any) -> list[tuple[str, dict[str, str]]]:
    """Flatten an ``XREAD``/``XREADGROUP`` reply: ``[[stream, batch], ...]``."""
    entries: list[tuple[str, dict[str, str]]] = []
    for _stream, batch in reply or []:
        entries.extend(_stream_entries(batch))
    return entries


def _parse_record(payload: str) -> RunRecord | None:
    try:
        return RunRecord.model_validate_json(payload)
    except ValueError as exc:
        log.warning("discarding unreadable run document", extra={"error": str(exc)})
        return None


def _parse_event(stream_id: str, fields: dict[str, str]) -> RunEvent:
    event = RunEvent.model_validate_json(fields["data"])
    event.id = stream_id
    return event


__all__ = ["RedisStore"]
