"""Tools served by this MCP server.

Tool modules declare themselves with the `@tool` decorator from `registry`,
which is independent of any particular server instance; the application binds
them all at startup with `register_all(mcp)` (see `main.py`).

To add a new tool:
  1. create a module in this package (e.g. `weather.py`);
  2. `from .registry import tool` and decorate a function with
     `@tool(required_scope="weather:read")`.

That is the whole procedure — modules are discovered automatically, so there
is no import list to maintain here. Auth, gateway and observability wrap the
whole server, and the decorator adds per-tool scope enforcement, metrics and
audit logging, so every new tool is protected and instrumented by default.
"""

from .registry import ToolSpec, register_all, tool

__all__ = ["ToolSpec", "register_all", "tool"]
