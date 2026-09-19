"""Follow a run's progress over Server-Sent Events.

python examples/stream_events.py "Summarise our incident runbook"
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx

BASE_URL = "http://localhost:8000"
API = f"{BASE_URL}/api/v1"

LABELS = {
    "run.queued": "queued  ",
    "run.started": "started ",
    "supervisor.route": "route   ",
    "agent.started": "agent + ",
    "agent.finished": "agent - ",
    "tool.started": "tool  + ",
    "tool.finished": "tool  - ",
    "run.completed": "done    ",
    "run.failed": "failed  ",
    "run.cancelled": "cancel  ",
    "log": "log     ",
}


def render(event: str, payload: dict[str, Any]) -> None:
    label = LABELS.get(event, f"{event:<8}")
    agent = payload.get("agent") or "-"
    print(f"  {label} {agent:<12} {payload.get('message') or ''}")


async def main(task: str) -> int:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None)) as client:
        response = await client.post(f"{API}/runs", json={"task": task})
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print(f"run {run_id} accepted\n")

        async with client.stream("GET", f"{API}/runs/{run_id}/stream") as stream:
            event = ""
            async for line in stream.aiter_lines():
                if line.startswith(":"):
                    continue
                if line.startswith("event: "):
                    event = line.removeprefix("event: ").strip()
                elif line.startswith("data: "):
                    render(event, json.loads(line.removeprefix("data: ")))

        result = (await client.get(f"{API}/runs/{run_id}/result")).json()
        print("\n" + (result.get("result") or result.get("error") or "(no answer)"))
        return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    objective = " ".join(sys.argv[1:]) or "Summarise how the run queue works"
    raise SystemExit(asyncio.run(main(objective)))
