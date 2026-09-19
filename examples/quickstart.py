"""Submit a task to a running AgentMesh API and print the answer.

    python examples/quickstart.py "Explain Redis consumer groups"
"""

from __future__ import annotations

import asyncio
import sys

import httpx

BASE_URL = "http://localhost:8000"
API = f"{BASE_URL}/api/v1"
TERMINAL = {"succeeded", "failed", "cancelled"}


async def main(task: str) -> int:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{API}/runs", json={"task": task})
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print(f"run {run_id} accepted")

        record: dict = {}
        while True:
            record = (await client.get(f"{API}/runs/{run_id}")).json()
            print(f"  status: {record['status']}")
            if record["status"] in TERMINAL:
                break
            await asyncio.sleep(0.5)

        if record.get("result"):
            print("\n" + record["result"])
        if record.get("error"):
            print(f"\nerror: {record['error']}", file=sys.stderr)
        return 0 if record["status"] == "succeeded" else 1


if __name__ == "__main__":
    objective = " ".join(sys.argv[1:]) or "Explain Redis consumer groups to a new backend engineer"
    raise SystemExit(asyncio.run(main(objective)))
