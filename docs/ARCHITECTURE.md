# Architecture

This document explains how AgentMesh is put together and, more usefully, why.

## Components

| Package | Responsibility |
| --- | --- |
| `agentmesh.config` | One typed `Settings` object, read from `AGENTMESH_*` env vars and `.env`. |
| `agentmesh.schemas` | Wire + storage models: `RunRecord`, `RunEvent`, `RunRequest`, enums. |
| `agentmesh.storage` | The `RunStore` interface and its `memory` / `redis` implementations. |
| `agentmesh.events` | `EventBus`, the single object every node publishes through. |
| `agentmesh.tools` | Tool registry (`ToolSpec`) plus the built-in tools. |
| `agentmesh.agents` | `AgentDefinition`, the registry, and `AgentRuntime` (the tool loop). |
| `agentmesh.graph` | Graph state, the supervisor builder, prompt, route parser, checkpointer. |
| `agentmesh.worker` | `GraphRegistry`, `RunExecutor` and the queue-consuming `WorkerService`. |
| `agentmesh.api` | FastAPI dependencies and routes. |
| `agentmesh.llm` | Provider factory and the offline `MockChatModel`. |

Dependencies point in one direction: `api` and `worker` depend on `graph`,
`agents`, `storage` and `events`; nothing in `graph`/`agents` depends on FastAPI.
That is what lets the test suite drive the graph directly, without HTTP.

## The orchestration contract

```
START -> supervisor -> {specialist, specialist, ...} -> supervisor -> END
```

`AgentState` is a `TypedDict` whose only reduced channel is `messages`
(`add_messages`); everything else is replaced wholesale by the writing node. The
state deliberately contains nothing but plain dicts, lists, strings and
LangChain messages, because the checkpointer has to serialise it (msgpack) on
every step. No clients, no emitters, no callables.

The supervisor node:

1. increments `steps`, following the step budget in state
2. checks the cancellation flag through the store
3. stops if there is nothing left to do or the budget is exhausted
4. otherwise asks the model for a route and parses the answer
5. emits `supervisor.route` and returns either a next agent or a final answer

A specialist node:

1. runs the think -> act -> observe loop through `AgentRuntime`
2. merges its report into `results` (or its error into `errors`)
3. appends itself to `completed` and hands control back to the supervisor

Termination is therefore driven by three independent mechanisms: every candidate
has reported, the step budget is exhausted, or the supervisor returned an
unparseable answer (which is treated as FINISH). A run can fail to terminate
only by exceeding `max_steps`, which is bounded.

### Why the routing parser is defensive

The supervisor prompt asks for exactly two lines. Real models comply most of the
time. `parse_route` accepts, in order of preference: a JSON object, a `NEXT:`
label, an explicit finish token, a bare mention of a candidate name, a bare
finish word - and otherwise stops the run. Stopping is the safe failure: a
mis-parse that picks the wrong specialist wastes a step, while a mis-parse that
never terminates burns money.

## Run state machine

```
                    +--------------------------+
   POST /runs       |                          |
        |           v                          |
        +------> queued --cancel--> cancelled  |
                   |                           |
                   | (worker claims / inline)  |
                   v                           |
                running --cancel--> cancelled  |
                   |                           |
        +----------+-----------+               |
        v                      v               |
    succeeded              failed -------------+
```

* `queued -> cancelled` is handled synchronously by the cancel endpoint.
* `running -> cancelled` sets a flag; the supervisor checks it at each step, so
  cancellation is cooperative and takes effect at a node boundary - never
  mid-tool-call.
* Every terminal transition goes through `RunExecutor._finish`, which is the
  only place that writes a terminal status and appends the matching event. One
  writer, one truth.

## Event model

Events are appended to one log per run and are the only thing a client needs in
order to render progress:

| Type | Emitted by | Notes |
| --- | --- | --- |
| `run.queued` | API | includes the team and the execution mode |
| `run.started` | executor | includes the worker name and attempt number |
| `supervisor.route` | supervisor node | `data.next` and `data.reason` |
| `agent.started` / `agent.finished` | `AgentRuntime` | duration, iterations, tool names |
| `tool.started` / `tool.finished` | `AgentRuntime` | args, ok flag, duration, output preview |
| `run.completed` / `run.failed` / `run.cancelled` | executor | includes the routing trace |
| `log` | anywhere | free-form, e.g. cancellation requests |

Two rules make this robust:

* **Sequence numbers come from the store.** The memory backend uses a counter
  under a lock; Redis uses `INCR`. The append returns the event with `seq` and
  the storage cursor (`id`) filled in, so the caller never invents ordering.
* **Emission is best-effort.** `EventBus.emit` logs and swallows storage errors.
  A telemetry failure must not abort a healthy run. The SSE endpoint compensates
  by also polling the run's status, so a client can never hang on a lost
  terminal event.

## Redis key layout

`{ns}` is `AGENTMESH_REDIS_NAMESPACE` (default `agentmesh`).

| Key | Type | Purpose |
| --- | --- | --- |
| `{ns}:run:{id}` | STRING | JSON `RunRecord`, TTL `AGENTMESH_REDIS_TTL_SECONDS` |
| `{ns}:run:{id}:events` | STREAM | append-only event log, `MAXLEN ~` capped |
| `{ns}:run:{id}:seq` | STRING | `INCR` counter for `seq` |
| `{ns}:run:{id}:cancel` | STRING | cancellation flag, TTL'd |
| `{ns}:runs:index` | ZSET | score = `created_at`, member = run id (for listing) |
| `{ns}:runs:queue` | STREAM | work queue, consumer group `{ns}:workers` |

Design notes:

* **Streams, not pub/sub, for events.** Pub/sub is fire-and-forget: a client that
  reconnects has permanently lost the events it missed. A stream gives `XREAD`
  from an arbitrary id, which is exactly `Last-Event-ID` resume.
* **Streams, not a list, for the queue.** A consumer group gives at-least-once
  delivery plus the pending-entries list, so `XAUTOCLAIM` can recover work from a
  worker that died - `BLPOP` cannot.
* **The index is trimmed, not unbounded.** `ZREMRANGEBYRANK` keeps it aligned
  with `AGENTMESH_RUN_HISTORY_LIMIT`.
* **TTLs everywhere.** Run documents and event logs expire; nothing grows forever
  in a demo cluster left running.

## Storage backends

`RunStore` is a small abstract class: run documents, the event log, cancellation
flags and the queue. Two implementations ship:

| | `MemoryStore` | `RedisStore` |
| --- | --- | --- |
| Processes | one | many |
| Durable | no | yes (AOF) |
| Event replay after restart | no | yes |
| Queue recovery | n/a | `XAUTOCLAIM` |
| Use it for | tests, local dev, a single-container demo | anything else |

The memory backend is not a toy stub: it implements blocking reads with real
wake-ups (`asyncio.Event`, registered before the read so no notification can be
lost) and a genuine bounded event log. That is what makes the offline test suite
meaningful instead of decorative.

`AGENTMESH_EXECUTION_MODE=queue` requires `state_backend=redis`, because a queue
that only exists inside one process cannot be read by another process.
`Settings.validate_runtime()` reports that combination as a problem and
`agentmesh doctor` surfaces it.
## Execution modes

### `inline` (default)

The API stores the run, then `asyncio.create_task(executor.execute(run_id))`. The
task handle is kept on `app.state.tasks` so it cannot be garbage collected, and a
done-callback logs any exception that escaped. On shutdown the lifespan cancels
the outstanding tasks and awaits them.

Use it for local development, single-container demos and small deployments. The
API process is the worker, so an API restart loses in-flight runs (the
checkpointer keeps their state, if you configured Redis).

### `queue`

The API stores the run and `XADD`s its id to `{ns}:runs:queue`. One or more
`agentmesh worker` processes (the same image, a different entrypoint argument)
claim runs with `XREADGROUP`, execute them with a concurrency semaphore of
`AGENTMESH_WORKER_CONCURRENCY`, and `XACK` + `XDEL` when done.

Scaling is therefore horizontal and independent: `docker compose up -d --scale
worker=4` adds four concurrent runs' worth of capacity without touching the API.

At startup a worker calls `XAUTOCLAIM` to reclaim entries that have been pending
for longer than `AGENTMESH_QUEUE_RECLAIM_AFTER_SECONDS`. An entry is pending
exactly when a worker died holding it, so a crashed run is retried rather than
silently dropped. Combined with `RunExecutor`'s early exit for terminal records,
retries are idempotent.

## Failure handling

Every layer is expected to fail, and each failure has one defined home:

| Failure | Handling |
| --- | --- |
| Model call fails or times out | `AgentRuntime` catches it, returns `ok=False`, records the error in `state.errors`. The run continues with the other specialists. |
| A tool raises | `AgentRuntime` turns it into an error `ToolMessage`, so the model can react or give up. Tools never abort a run. |
| A tool is not registered | Skipped with a warning at resolution time; an unknown name requested by the model becomes an error observation. |
| The supervisor returns junk | `parse_route` falls back to FINISH and the run completes with whatever reports exist. |
| A run exceeds `AGENTMESH_RUN_TIMEOUT_SECONDS` | `asyncio.wait_for` cancels the graph; the record is marked `failed` with the budget in the error. |
| A run exceeds `max_steps` | The supervisor finalises normally; the trace shows why. |
| The worker process is killed | The queue entry stays pending and is reclaimed by `XAUTOCLAIM`. |
| Redis is unreachable | `/readyz` returns 503; workers back off and retry; `agentmesh doctor` reports it. |
| An SSE client disconnects | The generator notices via `request.is_disconnected()` and returns; nothing leaks. |

The invariant behind all of this: **the run record is the source of truth**. The
event log is how you observe it, the queue is how you schedule it, and the graph
is how you compute it - but a client that only polls `GET /runs/{id}` must always
be able to tell what happened.

## Checkpointing

`AGENTMESH_CHECKPOINT_BACKEND` selects the LangGraph checkpointer:

* `memory` - an `InMemorySaver`, process-local, sufficient for `inline` mode.
* `redis` - `AsyncRedisSaver`, so a run's state survives a restart and can be
  resumed by replaying the same `thread_id`. Needs `langgraph-checkpoint-redis`;
  if the import fails, AgentMesh logs a warning and falls back to memory rather
  than refusing to start.

The run's `thread_id` is generated with the run record and returned by
`POST /runs`, so a client always holds the handle it needs to resume or inspect
the graph state.

## Configuration philosophy

* One `Settings` class, one prefix, no scattered `os.getenv`.
* Every field has a safe default, so `git clone && agentmesh serve` works with no
  `.env` at all.
* List-shaped settings are comma-separated strings. JSON-in-env is a footgun in
  Docker Compose and shell scripts.
* Inconsistent combinations are *warnings*, not startup failures
  (`Settings.validate_runtime()`), so a misconfiguration is diagnosable with
  `agentmesh doctor` instead of being a container that will not boot.

## Testing strategy

The suite is offline by construction:

* `AGENTMESH_LLM_PROVIDER=mock` - `MockChatModel` follows a deterministic policy
  (call the first bound tool, then answer; or pick the first candidate that has
  not reported). Tests that need a specific path pass a `script` of canned
  replies.
* `AGENTMESH_STATE_BACKEND=memory` - the same `RunStore` interface the Redis
  backend implements.
* `httpx.ASGITransport` - the real ASGI app, driven through its real lifespan, so
  routes, dependencies, exception handlers and background tasks are all
  exercised.

What this buys: routing edge cases, the tool loop, the event log, SSE resume and
the HTTP contract are all covered by tests that run in about a second and cost
nothing. What it does not cover is the Redis backend - test that against a real
Redis before you rely on it. `tests/test_memory_store.py` shows the shape those
tests should take.

## Deliberate non-goals

* **No agent framework inside an agent framework.** The supervisor topology is
  ~150 lines of explicit nodes. If you want prebuilt ReAct agents, use them; the
  graph does not care what a node does internally.
* **No message bus beyond Redis.** Adding Kafka or RabbitMQ to a project this size
  buys complexity, not capability. The `RunStore` interface is where a new
  backend would plug in.
* **No auth.** Authentication is deployment-specific. Every route depends on
  `api/deps.py`, which is the single place to add it.