from __future__ import annotations

import asyncio

import pytest

from agentmesh.config import Settings
from agentmesh.schemas import EventType, RunEvent, RunRecord, RunStatus
from agentmesh.storage.memory import MemoryStore


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore(Settings(state_backend="memory", redis_stream_maxlen=5, run_history_limit=50))


async def test_run_records_round_trip(store: MemoryStore) -> None:
    record = RunRecord(task="explain redis streams", agents=["researcher"])
    await store.save_run(record)

    fetched = await store.get_run(record.id)
    assert fetched is not None
    assert fetched.task == "explain redis streams"
    assert fetched.status is RunStatus.QUEUED
    assert await store.get_run("run_missing") is None


async def test_saved_records_are_detached_copies(store: MemoryStore) -> None:
    record = RunRecord(task="t", agents=[])
    await store.save_run(record)
    record.status = RunStatus.SUCCEEDED
    assert (await store.get_run(record.id)).status is RunStatus.QUEUED


async def test_list_runs_is_newest_first_and_filterable(store: MemoryStore) -> None:
    first = RunRecord(task="one", agents=[])
    second = RunRecord(task="two", agents=[], status=RunStatus.SUCCEEDED)
    await store.save_run(first)
    await store.save_run(second)

    assert [record.task for record in await store.list_runs()] == ["two", "one"]
    assert [record.task for record in await store.list_runs(status=RunStatus.SUCCEEDED)] == ["two"]


async def test_events_get_monotonic_sequences(store: MemoryStore) -> None:
    first = await store.append_event(RunEvent(run_id="run_x", type=EventType.RUN_QUEUED))
    second = await store.append_event(RunEvent(run_id="run_x", type=EventType.RUN_STARTED))

    assert (first.seq, second.seq) == (1, 2)
    assert (first.id, second.id) == ("1", "2")


async def test_reading_events_after_a_cursor(store: MemoryStore) -> None:
    for index in range(3):
        await store.append_event(RunEvent(run_id="run_x", type=EventType.LOG, message=str(index)))

    events = await store.read_events("run_x", after="1")
    assert [event.message for event in events] == ["1", "2"]


async def test_blocking_read_returns_nothing_on_timeout(store: MemoryStore) -> None:
    assert await store.read_events("run_missing", block_ms=30) == []


async def test_blocking_read_wakes_up_on_append(store: MemoryStore) -> None:
    async def producer() -> None:
        await asyncio.sleep(0.05)
        await store.append_event(RunEvent(run_id="run_x", type=EventType.LOG, message="late"))

    task = asyncio.create_task(producer())
    events = await store.read_events("run_x", block_ms=3_000)
    await task

    assert [event.message for event in events] == ["late"]


async def test_event_log_is_capped_by_maxlen(store: MemoryStore) -> None:
    for index in range(8):
        await store.append_event(RunEvent(run_id="run_x", type=EventType.LOG, message=str(index)))

    events = await store.read_events("run_x")
    assert len(events) == 5
    assert events[-1].message == "7"


async def test_cancellation_flag(store: MemoryStore) -> None:
    record = RunRecord(task="t", agents=[])
    await store.save_run(record)

    assert await store.is_cancelled(record.id) is False
    assert await store.request_cancel(record.id) is True
    assert await store.is_cancelled(record.id) is True
    assert await store.request_cancel("run_missing") is False


async def test_queue_round_trip(store: MemoryStore) -> None:
    await store.enqueue_run("run_a")
    await store.enqueue_run("run_b")
    assert await store.queue_depth() == 2

    item = await store.dequeue_run(timeout_s=0.5)
    assert item is not None
    assert item.run_id == "run_a"
    await store.ack_run(item)
    assert await store.queue_depth() == 1


async def test_dequeue_times_out_on_an_empty_queue(store: MemoryStore) -> None:
    assert await store.dequeue_run(timeout_s=0.02) is None


async def test_recover_stale_is_a_noop_for_the_memory_backend(store: MemoryStore) -> None:
    assert await store.recover_stale(min_idle_ms=10) == []
