"""A safe arithmetic evaluator backed by the ``ast`` module (never ``eval``)."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from agentmesh.tools.registry import ToolSpec, register

MAX_EXPRESSION_LENGTH = 500
MAX_INT_DIGITS = 20

_BINARY_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_CONSTANTS: dict[str, float] = {"pi": math.pi, "tau": math.tau, "e": math.e}
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "degrees": math.degrees,
    "radians": math.radians,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "gcd": math.gcd,
}


class CalculatorError(ValueError):
    """The expression is malformed or uses a disallowed construct."""


def evaluate(expression: str) -> float | int:
    """Evaluate an arithmetic expression and return the numeric result."""
    source = (expression or "").strip()
    if not source:
        raise CalculatorError("empty expression")
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(f"expression is longer than {MAX_EXPRESSION_LENGTH} characters")
    try:
        parsed = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"invalid syntax: {exc.msg}") from exc
    return _eval(parsed.body)


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError(f"only numeric literals are supported, got {node.value!r}")
        return _guard_number(node.value)

    if isinstance(node, ast.BinOp):
        op = _BINARY_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"operator {type(node.op).__name__} is not allowed")
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)) and abs(right) > 1_000:
            raise CalculatorError("exponent is too large")
        try:
            return _guard_number(op(left, right))
        except ZeroDivisionError as exc:
            raise CalculatorError("division by zero") from exc

    if isinstance(node, ast.UnaryOp):
        unary = _UNARY_OPS.get(type(node.op))
        if unary is None:
            raise CalculatorError(f"unary operator {type(node.op).__name__} is not allowed")
        return _guard_number(unary(_eval(node.operand)))

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise CalculatorError(f"unknown name {node.id!r}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise CalculatorError("only whitelisted math functions can be called")
        if node.keywords:
            raise CalculatorError("keyword arguments are not supported")
        args = [_eval(arg) for arg in node.args]
        try:
            return _guard_number(_FUNCTIONS[node.func.id](*args))
        except (ValueError, OverflowError, TypeError) as exc:
            raise CalculatorError(f"{node.func.id}() failed: {exc}") from exc

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(item) for item in node.elts]

    raise CalculatorError(f"unsupported syntax: {type(node).__name__}")


def _guard_number(value: Any) -> Any:
    if isinstance(value, bool):
        raise CalculatorError("boolean results are not supported")
    if isinstance(value, int) and value.bit_length() > MAX_INT_DIGITS * 3.33:
        raise CalculatorError("integer result is too large")
    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        raise CalculatorError("result is not a finite number")
    return value


@tool("calculator")
def calculator(expression: str) -> str:
    """Evaluate a pure arithmetic expression and return the result.

    Supports + - * / // % **, parentheses, the constants pi/tau/e and the
    functions sqrt, exp, log, log2, log10, sin, cos, tan, floor, ceil, abs,
    round, min, max, pow, factorial and gcd. Use it for any numeric question
    instead of computing in your head.
    """
    try:
        value = evaluate(expression)
    except CalculatorError as exc:
        return f"calculator error for {expression!r}: {exc}"
    return f"{expression.strip()} = {value}"


register(
    ToolSpec(
        name="calculator",
        description=calculator.description,
        factory=lambda: calculator,
        tags=("math", "deterministic"),
    )
)

__all__ = ["CalculatorError", "calculator", "evaluate"]
