# AGENTS.md

Instructions for AI coding agents working in this repository. Humans should read
`CONTRIBUTING.md`; most of it applies to you too.

## What this project is

AgentMesh is a multi-agent orchestration service: a LangGraph supervisor routes an
objective to specialist agents, FastAPI exposes the run lifecycle, and Redis
provides the queue, the event log and the run documents. It must work with **no
API key, no network and no Redis** in its default configuration.

## Commands

```bash
uv sync --extra dev                 # install (add --extra providers --extra redis-checkpoint for everything)
uv run pytest                       # offline test suite, ~1s
uv run pytest -k routing            # single file / expression
uv run ruff check src tests examples
uv run ruff format src tests examples
uv run mypy
uv run agentmesh doctor             # effective config + connectivity
uv run agentmesh run "task" --watch # needs a running server
uv run agentmesh serve              # http://localhost:8100/docs
```

Always run `ruff check`, `mypy` and `pytest` before declaring work finished.

## Layout

```
src/agentmesh/
  config.py       typed settings, one AGENTMESH_* prefix
  schemas.py      RunRecord, RunEvent, RunRequest, enums
  storage/        RunStore ABC + memory/ and redis_store.py
  events/bus.py   EventBus - the only way nodes publish events
  tools/          registry.py + built-ins
  agents/         spec.py (AgentDefinition), registry.py, runtime.py (the tool loop), builtin.py
  graph/          state.py, builder.py (the graph), prompts.py, routing.py, checkpointer.py
  worker/         executor.py (run one run), service.py (consume the queue)
  api/            deps.py + routes/{runs,agents,health}.py
  llm/            factory.py + mock.py
tests/             mirrors src/ loosely, one file per concern
docs/              ARCHITECTURE.md + knowledge/ (sample retrieval corpus)
examples/          runnable examples
```

## Invariants - do not break these

1. **The offline path stays first-class.** `llm_provider=mock` +
   `state_backend=memory` + `execution_mode=inline` must always work with zero
   external dependencies. Anything that needs credentials must degrade with an
   explanatory message, not an exception.
2. **The run record is the source of truth.** Terminal statuses are written only
   by `RunExecutor._finish`. Do not write `RunStatus.SUCCEEDED` anywhere else.
3. **`EventBus.emit` is best-effort.** It logs and swallows `StoreError`. Do not
   make event emission able to abort a run.
4. **The graph state stays serialisable.** Only plain dicts, lists, strings,
   numbers and LangChain messages. No clients, no emitters, no callables - it has
   to survive a checkpointer round-trip.
5. **No provider SDKs outside `llm/factory.py`.** Nothing in `graph/`, `agents/`
   or `worker/` may import `langchain_openai`, `langchain_anthropic` or
   `langchain_ollama`.
6. **`RunStore` implementations stay in step.** Interface changes need matching
   changes in `memory.py` and `redis_store.py`, and the memory one must stay
   behaviourally correct (real blocking reads, real ordering), not a stub.
7. **The mock model stays deterministic.** No randomness, no clock dependence, no
   network. Tests depend on its exact policy: call the first bound tool when there
   is no observation yet, otherwise answer; as a planner, pick the first candidate
   that has not reported, otherwise FINISH.
8. **`parse_route` fails safe.** An unparseable supervisor response means FINISH.
   Never make it fall back to "pick something".

## Conventions

- Python 3.11+, `from __future__ import annotations` at the top of every module.
- Full type annotations on public functions. `Any` is fine at LangGraph's dynamic
  boundaries - say why in a comment.
- Line length 110, ruff rules `E,F,W,I,UP,B,C4,SIM,RUF,ASYNC`.
- Docstrings explain *why*, not *what*. Module docstrings say what the module owns.
- Comments are for non-obvious decisions (see the wake-up registration in
  `MemoryStore.read_events`); do not narrate code.
- Errors: tool failures become error strings the model can read; node failures are
  captured into `state["errors"]`; API failures use the handlers in `main.py`.
- New modules need an `__all__`.

## Testing expectations

- Every behaviour change needs a test in `tests/`. Use the `settings`, `app`,
  `client` and `wait_run` fixtures from `conftest.py`.
- Tests must not touch the network, require credentials, or sleep for real
  durations longer than ~100ms.
- Redis-specific behaviour belongs in a test that runs against a real Redis and
  is skipped when `AGENTMESH_TEST_REDIS_URL` is unset.

## Documentation to update

| Change | Update |
| --- | --- |
| New setting | `.env.example`, the config table in both READMEs, `Settings` |
| New endpoint | README API table, `docs/ARCHITECTURE.md` if it changes a flow |
| New tool or agent | README "Extending it", and `docs/knowledge/agentmesh-overview.md` |
| New failure mode | the failure table in `docs/ARCHITECTURE.md` |
| User-visible change | `CHANGELOG.md` under `Unreleased` |

## Editing notes

- Prefer editing an existing module over adding a new one; the layout above is
  deliberate and small.
- Keep the public surface tiny: `agentmesh.main:create_app`, `agentmesh.cli:main`,
  the registries, and `build_graph`. Everything else is internal.