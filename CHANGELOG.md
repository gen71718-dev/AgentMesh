# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-01-01

The first public release: a complete, runnable multi-agent service.

### Added

- LangGraph supervisor graph with a specialist roster that hands control back to
  the supervisor after every turn.
- Five built-in agents: `researcher`, `analyst`, `coder`, `writer`, `critic`, each
  with its own prompt, tools and report contract.
- Five built-in tools and a `ToolSpec` registry: `web_search` (Tavily),
  `knowledge_search` (local files), `calculator`, `python_repl` (sandboxed,
  opt-in) and `current_time`.
- Provider factory supporting OpenAI, Anthropic, Ollama and an offline
  deterministic mock model.
- FastAPI service: submit, list, inspect, stream (SSE with `Last-Event-ID`
  resume), fetch the result and cancel runs, plus `/healthz`, `/readyz` and
  `/metrics`.
- `RunStore` abstraction with a Redis backend (stream queue with a consumer group
  and `XAUTOCLAIM` recovery, per-run event streams, `INCR` sequence counters,
  TTL'd run documents) and an in-process memory backend.
- Two execution modes: `inline` (the API runs the graph) and `queue` (a separate
  worker fleet).
- Optional Redis checkpointer for LangGraph.
- `agentmesh` CLI with `serve`, `worker`, `run`, `agents`, `tools` and `doctor`.
- Multi-stage Dockerfile (non-root, healthcheck) and a Compose stack with Redis,
  the API, two workers and an optional Ollama profile.
- Offline test suite covering the graph, the route parser, the tool loop, the
  event log, SSE and the whole HTTP surface.
- Documentation: `README.md`, `README.zh-CN.md`, `docs/ARCHITECTURE.md` and a
  sample knowledge base used by `knowledge_search`.

[Unreleased]: https://github.com/gen71718-dev/AgentMesh/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gen71718-dev/AgentMesh/releases/tag/v0.1.0