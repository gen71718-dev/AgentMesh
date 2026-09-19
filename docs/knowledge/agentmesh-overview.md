# AgentMesh overview

AgentMesh is a multi-agent orchestration service. A supervisor agent routes an
objective to specialist agents, each of which runs its own tool loop and returns a
report. The supervisor keeps dispatching until every specialist has contributed
or the step budget is exhausted, then assembles the final answer.

## Specialist roster

| Agent | Remit | Tools |
| --- | --- | --- |
| researcher | Gathers external and internal evidence | web_search, knowledge_search |
| analyst | Quantitative reasoning, comparisons, estimates | calculator, python_repl, knowledge_search |
| coder | Code, API design, implementation plans | python_repl, calculator, knowledge_search |
| writer | Produces the final deliverable | knowledge_search |
| critic | Adversarial review, finds unsupported claims | knowledge_search |

The default team is researcher, analyst and writer. Add critic before publishing
anything external.

## Event types

The event log records run.queued, run.started, supervisor.route, agent.started,
tool.started, tool.finished, agent.finished, run.completed, run.failed,
run.cancelled and log.

## Configuration quick reference

LLM provider is selected with AGENTMESH_LLM_PROVIDER, which accepts mock, openai,
anthropic or ollama. Storage is selected with AGENTMESH_STATE_BACKEND, which
accepts memory or redis. Execution mode is selected with
AGENTMESH_EXECUTION_MODE, which accepts inline or queue.

The step ceiling for a single run is AGENTMESH_MAX_SUPERVISOR_STEPS. The number of
tool round trips inside one agent turn is AGENTMESH_MAX_TOOL_ITERATIONS. The
concurrency of a worker process is AGENTMESH_WORKER_CONCURRENCY.