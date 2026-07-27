"""Calculator MCP tools — the "simple tool" served by this remote MCP server.

Security and scalability patterns demonstrated at the tool layer:

* **Identity-aware operations** — the validated `sub` claim from the request
  context is attached to every invocation's audit log, so each action is
  attributable to the authenticated user ("scope every operation to the
  current user").
* **Least privilege** — the tool declares the scope it needs; the registry
  denies callers whose token does not carry it.
* **Input validation** — operands must be finite and bounded; divide-by-zero
  is rejected. Malformed input is a client error, never a crash.
* **Observability** — the registry records a `tool_invoked` security event and
  the `mcp_tool_calls_total` metric for every invocation, so this module holds
  business logic only.
"""

from __future__ import annotations

import logging
import math

from ..auth.context import get_current_user
from .registry import tool

logger = logging.getLogger(__name__)

MAX_OPERAND = 1e12


def _validate_operand(value: float, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if abs(value) > MAX_OPERAND:
        raise ValueError(f"{name} must be within ±{MAX_OPERAND:g}")


@tool(required_scope="calculator:use")
def calculate(operation: str, a: float, b: float) -> str:
    """Perform basic arithmetic. `operation` is one of: add, subtract, multiply, divide."""
    user = get_current_user()  # identity from the introspected token (SDK auth context)
    _validate_operand(a, "a")
    _validate_operand(b, "b")
    match operation:
        case "add":
            result = a + b
        case "subtract":
            result = a - b
        case "multiply":
            result = a * b
        case "divide":
            if b == 0:
                raise ValueError("Division by zero is not allowed")
            result = a / b
        case _:
            raise ValueError(
                f"Unknown operation '{operation}'. "
                "Use add, subtract, multiply or divide."
            )

    logger.info("Calculation performed", extra={"sub": user.sub, "operation": operation})
    return f"{a} {operation} {b} = {result}"
