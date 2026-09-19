"""Prompts owned by the graph (specialists own theirs in ``agents.builtin``)."""

from __future__ import annotations

REPORT_CLIP = 1_200
FINAL_CLIP = 20_000

SUPERVISOR_SYSTEM_PROMPT = """You are the supervisor of a multi-agent team.

You do not do the work yourself. You decide which specialist acts next, based on
what has already been reported. A specialist report is a contribution, not the
end of the run: keep dispatching until the objective is genuinely satisfied.

Rules
-----
1. Pick the specialist whose remit best fits the *remaining* work.
2. Prefer a specialist that has not reported yet. You cannot ask an agent to
   revise its work, so route to the one with the widest coverage instead.
3. If a specialist failed, either route to another one that can cover the gap,
   or answer FINISH and let the failure be reported.
4. If the reports already satisfy the objective, answer FINISH immediately. Do
   not invent extra work.

Reply with exactly two lines and nothing else:

NEXT: <one of the candidate agent names>
REASON: <one sentence>

or, when the objective is satisfied:

NEXT: FINISH
REASON: <one sentence>
"""


def build_supervisor_prompt(
    *,
    task: str,
    candidates: list[str],
    completed: list[str],
    results: dict[str, str],
    errors: dict[str, str],
    steps: int,
    max_steps: int,
) -> str:
    """The supervisor's single user turn (machine-readable header first)."""
    lines: list[str] = [
        f"OBJECTIVE: {task}",
        "",
        f"CANDIDATES: {', '.join(candidates) if candidates else '-'}",
        f"COMPLETED: {', '.join(completed) if completed else '-'}",
        "",
        "REPORTS SO FAR",
        "--------------",
    ]
    if results:
        for name, text in results.items():
            lines.append(f"### {name}")
            lines.append(clip(text, REPORT_CLIP))
    else:
        lines.append("(no specialist has reported yet)")

    if errors:
        lines.append("")
        lines.append("FAILED SPECIALISTS")
        lines.append("------------------")
        lines.extend(f"- {name}: {error}" for name, error in errors.items())

    lines.extend(
        [
            "",
            f"Steps used: {steps} of {max_steps}.",
            "",
            "Reply with NEXT: <candidate> or NEXT: FINISH.",
        ]
    )
    return "\n".join(lines)


def compose_final_answer(task: str, results: dict[str, str], errors: dict[str, str]) -> str:
    """Assemble the run's answer from the specialists' reports.

    When a writer is on the team it has already seen every other report in its
    context block, so its output *is* the synthesis and is returned unchanged.
    """
    if not results:
        if errors:
            details = "\n".join(f"- {name}: {error}" for name, error in errors.items())
            return f"No specialist produced a report for this objective.\n\nFailures:\n{details}"
        return "No specialist produced a report for this objective."

    if "writer" in results and len(results) > 1:
        return clip(results["writer"], FINAL_CLIP)

    if len(results) == 1:
        return clip(next(iter(results.values())), FINAL_CLIP)

    body = "\n\n".join(f"## {name}\n{clip(text, FINAL_CLIP)}" for name, text in results.items())
    return f"## Objective\n{task}\n\n{body}"


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit].rstrip()}\n[... truncated, {omitted} characters omitted]"


__all__ = [
    "FINAL_CLIP",
    "REPORT_CLIP",
    "SUPERVISOR_SYSTEM_PROMPT",
    "build_supervisor_prompt",
    "clip",
    "compose_final_answer",
]
