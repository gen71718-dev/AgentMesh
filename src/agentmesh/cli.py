"""The ``agentmesh`` command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from typing import Any

import httpx

from agentmesh import __version__
from agentmesh.agents.registry import all_agents
from agentmesh.config import Settings, reload_settings
from agentmesh.logger import configure_logging, get_logger
from agentmesh.tools.registry import list_tools

log = get_logger("cli")

TEXT_COMMANDS = frozenset({"agents", "tools", "doctor", "run"})
TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``agentmesh`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = reload_settings()
    configure_logging(
        settings.log_level,
        json_output=settings.log_json and args.command not in TEXT_COMMANDS,
        service="agentmesh",
    )
    return int(args.handler(args, settings))


# --------------------------------------------------------------------------- serve
def _serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    uvicorn.run(
        "agentmesh.main:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
        log_config=None,
        access_log=not args.quiet,
    )
    return 0


# -------------------------------------------------------------------------- worker
def _worker(args: argparse.Namespace, settings: Settings) -> int:
    try:
        return asyncio.run(_worker_async(once=args.once))
    except KeyboardInterrupt:
        return 130


async def _worker_async(*, once: bool) -> int:
    from agentmesh.events.bus import init_bus
    from agentmesh.graph.checkpointer import open_checkpointer
    from agentmesh.storage.factory import create_store
    from agentmesh.worker.executor import GraphRegistry, RunExecutor
    from agentmesh.worker.service import WorkerService

    settings = reload_settings()
    store = await create_store(settings)
    bus = init_bus(store)
    async with open_checkpointer(settings) as checkpointer:
        graphs = GraphRegistry(settings, checkpointer)
        executor = RunExecutor(store=store, graphs=graphs, bus=bus, settings=settings)
        service = WorkerService(store=store, executor=executor, settings=settings)
        try:
            if once:
                processed = await service.run_once()
                print(f"processed={processed}")
                return 0
            await service.run_forever(_stop_event())
        finally:
            await store.shutdown()
    return 0


def _stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, AttributeError):  # pragma: no cover - Windows
            continue
    return stop


# ----------------------------------------------------------------------------- run
def _run(args: argparse.Namespace, settings: Settings) -> int:
    try:
        return asyncio.run(_run_async(args, settings))
    except KeyboardInterrupt:
        return 130


async def _run_async(args: argparse.Namespace, settings: Settings) -> int:
    base = args.url.rstrip("/")
    api = f"{base}{settings.api_prefix}"
    payload: dict[str, Any] = {"task": args.task}
    if args.agents:
        payload["agents"] = [name.strip() for name in args.agents.split(",") if name.strip()]
    if args.max_steps:
        payload["max_steps"] = args.max_steps

    timeout = httpx.Timeout(args.timeout, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(f"{api}/runs", json=payload)
        except httpx.HTTPError as exc:
            print(f"could not reach the AgentMesh API at {base}: {exc}", file=sys.stderr)
            print("start one with: agentmesh serve", file=sys.stderr)
            return 1
        if response.status_code >= 400:
            print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
            return 1

        accepted = response.json()
        run_id = accepted["run_id"]
        print(f"run {run_id} accepted ({', '.join(payload.get('agents') or ['default team'])})")
        if args.watch:
            return await _watch(client, api, run_id)
        return await _poll(client, api, run_id, args.timeout)


async def _watch(client: httpx.AsyncClient, api: str, run_id: str) -> int:
    exit_code = 0
    async with client.stream("GET", f"{api}/runs/{run_id}/stream", timeout=None) as response:
        if response.status_code >= 400:
            print(f"HTTP {response.status_code}: {await response.aread()!r}", file=sys.stderr)
            return 1
        event_name = ""
        async for line in response.aiter_lines():
            if line.startswith(":"):
                continue
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                _print_event(payload)
                if event_name in TERMINAL_EVENTS:
                    exit_code = 0 if event_name == "run.completed" else 1
    return exit_code


async def _poll(client: httpx.AsyncClient, api: str, run_id: str, timeout: float) -> int:  # noqa: ASYNC109
    deadline = asyncio.get_running_loop().time() + timeout
    seen = ""
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"{api}/runs/{run_id}")
        if response.status_code >= 400:
            print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
            return 1
        record = response.json()
        if record["status"] != seen:
            seen = record["status"]
            print(f"status: {seen}")
        if seen in {"succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.5)
    else:
        print("timed out waiting for the run", file=sys.stderr)
        return 1

    payload = (await client.get(f"{api}/runs/{run_id}/result")).json()
    if payload.get("result"):
        print("\n" + payload["result"])
    if payload.get("error"):
        print(f"\nerror: {payload['error']}", file=sys.stderr)
    return 0 if payload["status"] == "succeeded" else 1


def _print_event(payload: dict[str, Any]) -> None:
    marker = {
        "supervisor.route": "->",
        "agent.started": "++",
        "agent.finished": "--",
        "tool.started": ">>",
        "tool.finished": "<<",
    }.get(payload.get("type", ""), " *")
    agent = payload.get("agent") or ""
    message = payload.get("message") or ""
    print(f"{marker} {agent:<12} {message}".rstrip())


# -------------------------------------------------------------------------- catalog
def _agents(args: argparse.Namespace, settings: Settings) -> int:
    for definition in all_agents():
        tools = ", ".join(definition.tools) or "-"
        print(f"{definition.name:<12} {definition.title:<24} tools: {tools}")
        print(f"{'':<12} {definition.description}")
    print(f"\ndefault team: {', '.join(settings.default_agent_list)}")
    return 0


def _tools(args: argparse.Namespace, settings: Settings) -> int:
    for info in list_tools():
        state = "ready" if info.available else "unavailable (no credentials/configuration)"
        print(f"{info.name:<18} {state}")
        print(f"{'':<18} {info.description.splitlines()[0]}")
    return 0


# --------------------------------------------------------------------------- doctor
def _doctor(args: argparse.Namespace, settings: Settings) -> int:
    print(f"AgentMesh {__version__}  ({settings.environment})")
    print(f"  api prefix        : {settings.api_prefix}")
    print(f"  llm provider      : {settings.llm_provider} / {settings.llm_model}")
    print(f"  state backend     : {settings.state_backend}")
    print(f"  checkpoint backend: {settings.checkpoint_backend}")
    print(f"  execution mode    : {settings.execution_mode}")
    print(f"  redis             : {settings.masked_redis_url}")
    print(f"  default team      : {', '.join(settings.default_agent_list)}")

    print("\ntools")
    for info in list_tools():
        print(f"  {'ok ' if info.available else 'off'} {info.name}")

    problems = settings.validate_runtime()
    if problems:
        print("\nconfiguration warnings")
        for problem in problems:
            print(f"  ! {problem}")

    reachable = asyncio.run(_probe_store(settings))
    print(f"\nstore reachable: {reachable}")
    return 0 if reachable and not problems else 1


async def _probe_store(settings: Settings) -> bool:
    from agentmesh.storage.factory import create_store

    try:
        store = await create_store(settings)
    except Exception as exc:
        print(f"  ! {type(exc).__name__}: {exc}")
        return False
    try:
        return await store.ping()
    finally:
        await store.shutdown()


# ---------------------------------------------------------------------------- parser
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentmesh",
        description="Multi-agent orchestration on LangGraph, FastAPI and Redis.",
    )
    parser.add_argument("--version", action="version", version=f"agentmesh {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true", help="autoreload on file changes")
    serve.add_argument("--quiet", action="store_true", help="disable the access log")
    serve.set_defaults(handler=_serve)

    worker = sub.add_parser("worker", help="run a queue worker")
    worker.add_argument("--once", action="store_true", help="process at most one run and exit")
    worker.set_defaults(handler=_worker)

    run = sub.add_parser("run", help="submit a task and wait for the answer")
    run.add_argument("task", help="the objective for the agent team")
    run.add_argument("--agents", default=None, help="comma separated agent names")
    run.add_argument("--max-steps", dest="max_steps", type=int, default=None)
    run.add_argument("--url", default="http://localhost:8000", help="base URL of the API")
    run.add_argument("--timeout", type=float, default=300.0)
    run.add_argument("--watch", action="store_true", help="follow the SSE event stream")
    run.set_defaults(handler=_run)

    agents = sub.add_parser("agents", help="list the registered agents")
    agents.set_defaults(handler=_agents)

    tools = sub.add_parser("tools", help="list the registered tools")
    tools.set_defaults(handler=_tools)

    doctor = sub.add_parser("doctor", help="check configuration and connectivity")
    doctor.set_defaults(handler=_doctor)

    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
