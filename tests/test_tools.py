from __future__ import annotations

import pytest

from agentmesh.config import Settings
from agentmesh.tools import get_tool, list_tools, tool_names
from agentmesh.tools.calculator import CalculatorError, calculator, evaluate
from agentmesh.tools.knowledge import knowledge_search
from agentmesh.tools.python_repl import python_repl, run_snippet


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+2*3", 8),
        ("(2+2)*3", 12),
        ("10/4", 2.5),
        ("2 ** 10", 1024),
        ("-5 + 1", -4),
        ("max(1, 5, 3)", 5),
        ("round(pi, 2)", 3.14),
        ("sqrt(16)", 4.0),
        ("gcd(12, 18)", 6),
        ("7 % 3", 1),
    ],
)
def test_calculator_evaluates(expression: str, expected: float) -> None:
    assert evaluate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "1/0",
        "__import__('os').system('ls')",
        "open('/etc/passwd')",
        "True",
        "[x for x in range(3)]",
        "1 +",
        "unknown_name",
    ],
)
def test_calculator_refuses_unsafe_or_invalid_input(expression: str) -> None:
    with pytest.raises(CalculatorError):
        evaluate(expression)


def test_calculator_tool_reports_errors_instead_of_raising() -> None:
    assert "division by zero" in calculator.invoke({"expression": "1/0"})
    assert calculator.invoke({"expression": "6*7"}) == "6*7 = 42"


def test_builtin_tools_are_registered() -> None:
    assert {"calculator", "current_time", "knowledge_search", "web_search", "python_repl"} <= set(
        tool_names()
    )
    assert get_tool("calculator").name == "calculator"


def test_tool_catalog_reports_availability() -> None:
    by_name = {info.name: info for info in list_tools()}
    assert by_name["calculator"].available is True
    assert by_name["web_search"].available is False
    assert by_name["web_search"].tags == ["search", "network"]


def test_unknown_tool_raises() -> None:
    from agentmesh.errors import ToolNotFound

    with pytest.raises(ToolNotFound):
        get_tool("nope")


def test_python_repl_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agentmesh.tools.python_repl.get_settings", lambda: Settings(enable_python_tool=False)
    )
    assert "disabled" in python_repl.invoke({"code": "print(1)"})


def test_python_repl_runs_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentmesh.tools.python_repl.get_settings", lambda: Settings(enable_python_tool=True))
    assert python_repl.invoke({"code": "print(sum(range(10)))"}).strip() == "45"


def test_python_repl_reports_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentmesh.tools.python_repl.get_settings", lambda: Settings(enable_python_tool=True))
    result = python_repl.invoke({"code": "print(1/0)"})
    assert "ZeroDivisionError" in result


def test_python_repl_has_no_imports() -> None:
    assert "NameError" in run_snippet("__import__('os')")


def test_knowledge_search_without_a_configured_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentmesh.tools.knowledge.get_settings", lambda: Settings(knowledge_dir=None))
    assert "unavailable" in knowledge_search.invoke({"query": "anything"})


def test_knowledge_search_finds_matching_lines(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "runbook.md").write_text(
        "The redis queue is drained by the worker pool.\n"
        "Unrelated line about lunch.\n"
        "Workers reclaim stale queue entries with XAUTOCLAIM.\n",
        encoding="utf-8",
    )
    settings = Settings(knowledge_dir=str(tmp_path))
    monkeypatch.setattr("agentmesh.tools.knowledge.get_settings", lambda: settings)

    result = knowledge_search.invoke({"query": "reclaim stale queue", "top_k": 2})
    assert "runbook.md" in result
    assert "XAUTOCLAIM" in result
    assert "lunch" not in result
