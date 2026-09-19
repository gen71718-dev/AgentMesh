from __future__ import annotations

from httpx import AsyncClient


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_reports_the_backend(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["store"]["backend"] == "memory"
    assert checks["store"]["reachable"] is True
    assert checks["execution_mode"] == "inline"


async def test_metrics_are_exposed(client: AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "agentmesh_queue_depth" in response.text
    assert "agentmesh_info" in response.text


async def test_root_exposes_pointers(client: AsyncClient) -> None:
    payload = (await client.get("/")).json()
    assert payload["docs"] == "/docs"
    assert payload["api"] == "/api/v1"


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["x-request-id"]
    assert response.headers["x-process-time-ms"]


async def test_catalog_endpoints(client: AsyncClient) -> None:
    agents = (await client.get("/api/v1/agents")).json()
    assert {agent["name"] for agent in agents} >= {"researcher", "analyst", "coder", "writer", "critic"}

    tools = (await client.get("/api/v1/tools")).json()
    assert {tool["name"] for tool in tools} >= {"calculator", "web_search", "knowledge_search"}

    single = await client.get("/api/v1/agents/writer")
    assert single.status_code == 200
    assert single.json()["title"] == "Technical Writer"
    assert (await client.get("/api/v1/agents/ghost")).status_code == 404


async def test_a_run_completes_end_to_end(client: AsyncClient, wait_run) -> None:
    response = await client.post("/api/v1/runs", json={"task": "Write a brief about Redis streams"})
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["status"] == "queued"

    record = await wait_run(accepted["run_id"])
    assert record["status"] == "succeeded"
    assert record["agents"] == ["researcher", "writer"]
    assert "[writer]" in record["result"]
    assert record["duration_ms"] is not None
    assert record["finished_at"] is not None
    assert record["error"] is None


async def test_the_event_log_is_ordered_and_complete(client: AsyncClient, wait_run) -> None:
    accepted = (await client.post("/api/v1/runs", json={"task": "Trace the event log"})).json()
    await wait_run(accepted["run_id"])

    events = (await client.get(f"/api/v1/runs/{accepted['run_id']}/events")).json()
    types = [event["type"] for event in events]
    assert types[0] == "run.queued"
    assert types[-1] == "run.completed"
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert "supervisor.route" in types
    assert "agent.started" in types
    assert "agent.finished" in types
    assert "tool.finished" in types


async def test_the_sse_stream_ends_with_a_terminal_event(client: AsyncClient, wait_run) -> None:
    accepted = (await client.post("/api/v1/runs", json={"task": "Stream this run"})).json()
    await wait_run(accepted["run_id"])

    names: list[str] = []
    async with client.stream("GET", f"/api/v1/runs/{accepted['run_id']}/stream", timeout=30.0) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                names.append(line.removeprefix("event: ").strip())

    assert names[-1] == "run.completed"
    assert "supervisor.route" in names


async def test_the_result_endpoint_returns_just_the_answer(client: AsyncClient, wait_run) -> None:
    accepted = (await client.post("/api/v1/runs", json={"task": "Give me a result"})).json()
    await wait_run(accepted["run_id"])

    payload = (await client.get(f"/api/v1/runs/{accepted['run_id']}/result")).json()
    assert payload["status"] == "succeeded"
    assert payload["result"]
    assert payload["error"] is None


async def test_listing_and_filtering_runs(client: AsyncClient, wait_run) -> None:
    accepted = (await client.post("/api/v1/runs", json={"task": "List me"})).json()
    await wait_run(accepted["run_id"])

    listing = (await client.get("/api/v1/runs", params={"limit": 10})).json()
    assert listing["total"] >= 1
    assert any(item["id"] == accepted["run_id"] for item in listing["items"])

    succeeded = (await client.get("/api/v1/runs", params={"status": "succeeded"})).json()
    assert all(item["status"] == "succeeded" for item in succeeded["items"])


async def test_cancelling_a_finished_run_is_a_no_op(client: AsyncClient, wait_run) -> None:
    accepted = (await client.post("/api/v1/runs", json={"task": "Too late to cancel"})).json()
    record = await wait_run(accepted["run_id"])

    response = await client.post(f"/api/v1/runs/{accepted['run_id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == record["status"] == "succeeded"


async def test_unknown_runs_return_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/runs/run_missing")).status_code == 404
    assert (await client.post("/api/v1/runs/run_missing/cancel")).status_code == 404
    assert (await client.get("/api/v1/runs/run_missing/events")).status_code == 404
    assert (await client.get("/api/v1/runs/run_missing/stream")).status_code == 404


async def test_an_unknown_agent_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/runs", json={"task": "t", "agents": ["ghost"]})
    assert response.status_code == 422
    assert "ghost" in response.text


async def test_a_blank_task_is_rejected(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/runs", json={"task": "   "})).status_code == 422


async def test_a_single_agent_team_is_allowed(client: AsyncClient, wait_run) -> None:
    response = await client.post(
        "/api/v1/runs",
        json={"task": "Solo run", "agents": ["researcher"], "max_steps": 2},
    )
    assert response.status_code == 202

    record = await wait_run(response.json()["run_id"])
    assert record["agents"] == ["researcher"]
    assert record["status"] == "succeeded"
