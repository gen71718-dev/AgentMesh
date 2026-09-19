from __future__ import annotations

from agentmesh.graph.prompts import build_supervisor_prompt, compose_final_answer
from agentmesh.graph.routing import parse_route

CANDIDATES = ["researcher", "analyst", "writer"]


def test_explicit_next_label() -> None:
    decision = parse_route("NEXT: analyst\nREASON: numbers are needed", CANDIDATES)
    assert decision.agent == "analyst"
    assert decision.reason == "numbers are needed"


def test_explicit_finish_label() -> None:
    decision = parse_route("NEXT: FINISH\nREASON: done", CANDIDATES)
    assert decision.is_finish
    assert decision.reason == "done"


def test_json_payload_is_preferred() -> None:
    decision = parse_route('{"next": "writer", "reason": "needs prose"}', CANDIDATES)
    assert decision.agent == "writer"
    assert decision.reason == "needs prose"


def test_json_finish_is_understood() -> None:
    assert parse_route('{"next_agent": "FINISH"}', CANDIDATES).is_finish


def test_json_aliases_are_understood() -> None:
    assert parse_route('{"agent": "analyst"}', CANDIDATES).agent == "analyst"
    assert parse_route('{"route": "writer"}', CANDIDATES).agent == "writer"


def test_prose_mention_falls_back_to_the_candidate_name() -> None:
    decision = parse_route("The analyst should compute the totals.", CANDIDATES)
    assert decision.agent == "analyst"


def test_prose_with_an_unknown_name_finishes() -> None:
    assert parse_route("I am not sure, maybe a human should look.", CANDIDATES).is_finish


def test_empty_response_finishes() -> None:
    decision = parse_route("", CANDIDATES)
    assert decision.is_finish
    assert "empty" in decision.reason


def test_backticked_name_is_resolved() -> None:
    assert parse_route("NEXT: `writer`", CANDIDATES).agent == "writer"


def test_unknown_label_does_not_match_a_candidate() -> None:
    decision = parse_route("NEXT: scientist", CANDIDATES)
    assert decision.is_finish


def test_first_uncompleted_candidate_wins_on_ambiguity() -> None:
    decision = parse_route("Either researcher or analyst would do", CANDIDATES)
    assert decision.agent == "researcher"


def test_finish_word_without_a_candidate() -> None:
    assert parse_route("The work is complete.", CANDIDATES).is_finish


def test_supervisor_prompt_exposes_the_machine_readable_header() -> None:
    prompt = build_supervisor_prompt(
        task="Write a brief",
        candidates=["writer"],
        completed=["researcher"],
        results={"researcher": "some findings"},
        errors={},
        steps=2,
        max_steps=8,
    )
    assert "CANDIDATES: writer" in prompt
    assert "COMPLETED: researcher" in prompt
    assert "### researcher" in prompt
    assert "Steps used: 2 of 8." in prompt


def test_supervisor_prompt_marks_empty_lists() -> None:
    prompt = build_supervisor_prompt(
        task="t", candidates=[], completed=[], results={}, errors={}, steps=1, max_steps=3
    )
    assert "CANDIDATES: -" in prompt
    assert "COMPLETED: -" in prompt
    assert "(no specialist has reported yet)" in prompt


def test_supervisor_prompt_reports_failures() -> None:
    prompt = build_supervisor_prompt(
        task="t",
        candidates=["analyst"],
        completed=[],
        results={},
        errors={"researcher": "boom"},
        steps=1,
        max_steps=3,
    )
    assert "FAILED SPECIALISTS" in prompt
    assert "- researcher: boom" in prompt


def test_the_writer_output_wins_when_it_is_on_the_team() -> None:
    answer = compose_final_answer("objective", {"researcher": "raw", "writer": "polished"}, {})
    assert answer == "polished"


def test_a_single_report_is_returned_as_is() -> None:
    assert compose_final_answer("objective", {"researcher": "raw"}, {}) == "raw"


def test_multiple_reports_are_joined_with_headers_and_the_objective() -> None:
    answer = compose_final_answer("objective", {"researcher": "a", "analyst": "b"}, {})
    assert "## Objective" in answer
    assert "## researcher" in answer
    assert "## analyst" in answer


def test_errors_are_surfaced_when_nothing_succeeded() -> None:
    answer = compose_final_answer("objective", {}, {"researcher": "boom"})
    assert "Failures" in answer
    assert "researcher: boom" in answer


def test_no_reports_at_all() -> None:
    assert compose_final_answer("objective", {}, {}) == "No specialist produced a report for this objective."
