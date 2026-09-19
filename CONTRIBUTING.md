# Contributing to AgentMesh

Thanks for taking the time to contribute. This document is short on ceremony and
long on the things that will actually get your pull request merged.

## Getting set up

```bash
git clone https://github.com/gen71718-dev/AgentMesh.git
cd agentmesh
uv sync --extra dev --extra providers --extra redis-checkpoint
uv run pytest
```

No API key, no network and no Redis are needed for the test suite. If you want to
exercise the Redis backend:

```bash
docker run -d -p 6379:6379 redis:7.4-alpine
AGENTMESH_STATE_BACKEND=redis uv run agentmesh serve
```

## Before you push

```bash
uv run ruff check src tests examples
uv run ruff format src tests examples
uv run mypy
uv run pytest
```

`make check` runs all four. CI runs them on Python 3.11, 3.12 and 3.13 plus a
Docker build with a smoke test, so a green local run is a good predictor.

## What we look for

**Behaviour changes come with tests.** The suite is offline by design; use the
mock model and the memory store and your test will run in milliseconds. If you
add a tool, test it directly and test the registry entry. If you add an agent,
test that the supervisor routes to it - `tests/test_graph.py` shows the shape.

**Keep the offline path first-class.** Anything that only works with a real
provider or a live Redis must degrade explicitly rather than crash. The mock model
and the memory store are not test fixtures; they are a supported configuration.

**Errors over exceptions at the edges.** Tools return error strings so the model
can react. `EventBus.emit` swallows storage failures so telemetry cannot kill a
run. The run record is the single source of truth. If you are adding a new failure
mode, decide which of those three it belongs to and document it in
`docs/ARCHITECTURE.md`.

**No vendor SDKs in the orchestration.** `graph/`, `agents/` and `worker/` must
not import a provider package. Providers are constructed in `llm/factory.py` and
nowhere else.

**Types on public functions.** The codebase is typed and `mypy` is expected to
stay quiet. `Any` is acceptable where LangGraph's dynamic surfaces make precise
types impractical - comment why when you use it.

**Two implementations stay in step.** `RunStore` has a memory and a Redis
backend. A change to the interface means a change to both, and the memory
implementation must remain genuinely correct (real blocking, real ordering), not a
stub that makes tests pass.

## Adding a tool or an agent

Both are a single file plus a registration call. The README walks through each
with a complete example, and `examples/custom_agent.py` is a runnable end-to-end
version.

## Commit messages

Conventional-ish, imperative, one line:

```
feat(tools): add a weather tool
fix(worker): reclaim queue items older than the idle threshold
docs(architecture): describe the run state machine
```

Add a body when the *why* is not obvious from the diff.

## Pull requests

- One logical change per PR.
- Describe the failure mode you are fixing, or the behaviour you are adding.
- Say how you tested it, including the command.
- Update `README.md` and `docs/ARCHITECTURE.md` when you change the API surface,
  the configuration or the design invariants.
- Add an entry under `Unreleased` in `CHANGELOG.md` for user-visible changes.

## Reporting bugs

Include the version, your `agentmesh doctor` output (redact secrets), and the
smallest reproduction you can manage. If it involves routing, attach the run's
event log (`GET /api/v1/runs/{id}/events`) - it contains the supervisor's actual
decisions.

## License

By contributing you agree that your contributions are licensed under the MIT
License.