"""Tool registry — how tool modules declare themselves without knowing about
the server they will be served from.

The problem this solves: decorating with `@mcp.tool()` directly binds every
tool module to one fully-configured FastMCP instance, so a tool cannot be
imported (or unit-tested) without constructing the whole OAuth-protected
server, and each new tool has to be hand-imported somewhere.

Here the dependency is inverted. Tool modules import *this* module — which
knows nothing about FastMCP — and declare themselves with `@tool(...)`. The
application later hands the registry a server instance via `register_all(mcp)`
(see `main.py`), which discovers every module in this package and binds the
collected tools to it.

`@tool` also supplies the cross-cutting concerns every tool needs, so tool
modules contain business logic only:

* **Per-tool authorization** — `required_scope` is checked against the
  authenticated user's scopes. The `required_scopes` on `AuthSettings`
  (`server.py`) is the coarse gate on the `/mcp` endpoint as a whole; this is
  the fine-grained gate per tool, so one tool's audience is not automatically
  every tool's audience.
* **Metrics** — `mcp_tool_calls_total{tool,outcome}`.
* **Audit trail** — a `tool_invoked` / `tool_denied` security event per call,
  attributed to the authenticated `sub`.

To add a tool: create a module in this package, decorate a function with
`@tool(...)`. Nothing else — no imports to update, no registration list.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..auth.context import AuthenticatedUser, get_current_user
from ..observability.metrics import TOOL_CALLS
from ..observability.security_events import log_security_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    """A tool declared by a module, not yet bound to any server."""

    fn: Callable[..., Any]
    name: str
    required_scope: str | None


_REGISTRY: list[ToolSpec] = []


def tool(
    *, name: str | None = None, required_scope: str | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a function as an MCP tool.

    `name` defaults to the function name. `required_scope`, when set, must be
    present in the caller's token scopes or the call is denied before the body
    runs.

    Returns the function unchanged, so a decorated tool stays directly
    callable (and unit-testable) outside of a server.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY.append(
            ToolSpec(fn=fn, name=name or fn.__name__, required_scope=required_scope)
        )
        return fn

    return decorator


def _authorize(spec: ToolSpec) -> AuthenticatedUser:
    """Resolve the caller and enforce this tool's scope. Raises on denial."""
    user = get_current_user()
    if spec.required_scope is not None and spec.required_scope not in user.scopes:
        TOOL_CALLS.labels(tool=spec.name, outcome="denied").inc()
        log_security_event(
            "tool_denied",
            tool=spec.name,
            sub=user.sub,
            required_scope=spec.required_scope,
        )
        raise PermissionError(
            f"Tool '{spec.name}' requires the '{spec.required_scope}' scope."
        )
    return user


def _record(spec: ToolSpec, user: AuthenticatedUser, outcome: str) -> None:
    TOOL_CALLS.labels(tool=spec.name, outcome=outcome).inc()
    log_security_event("tool_invoked", tool=spec.name, sub=user.sub, outcome=outcome)


def _outcome_for(exc: BaseException) -> str:
    # ValueError is the convention for rejected input (see `calculator.py`);
    # anything else is an unexpected failure.
    return "invalid_input" if isinstance(exc, ValueError) else "error"


def _wrap(spec: ToolSpec) -> Callable[..., Any]:
    """Add authorization, metrics and audit logging around a tool function.

    `functools.wraps` is load-bearing, not cosmetic: FastMCP builds the tool's
    JSON input schema from the signature and type hints and its description
    from the docstring. `wraps` copies `__doc__`/`__annotations__` and sets
    `__wrapped__`, which `inspect.signature` follows — so the wrapped tool
    presents exactly the same schema to clients as the bare function.
    """
    fn = spec.fn

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            user = _authorize(spec)
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                _record(spec, user, _outcome_for(exc))
                raise
            _record(spec, user, "success")
            return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        user = _authorize(spec)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            _record(spec, user, _outcome_for(exc))
            raise
        _record(spec, user, "success")
        return result

    return sync_wrapper


def _discover() -> None:
    """Import every tool module in this package so its decorators run."""
    package = importlib.import_module(__package__)
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name == "registry" or module_info.name.startswith("_"):
            continue
        importlib.import_module(f"{__package__}.{module_info.name}")


def register_all(mcp: Any) -> list[str]:
    """Discover every tool in this package and bind it to `mcp`.

    Two modules declaring the same tool name is a bug — the duplicate is
    skipped and warned about rather than silently shadowing the first.
    Returns the registered tool names.
    """
    _discover()

    registered: list[str] = []
    seen: set[str] = set()
    for spec in _REGISTRY:
        if spec.name in seen:
            logger.warning("Duplicate tool name, skipping", extra={"tool": spec.name})
            continue
        seen.add(spec.name)
        mcp.tool(name=spec.name)(_wrap(spec))
        registered.append(spec.name)

    logger.info(
        "Tools registered", extra={"count": len(registered), "tools": registered}
    )
    return registered
