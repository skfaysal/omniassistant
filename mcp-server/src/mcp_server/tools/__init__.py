"""Tool registry.

Importing this package registers every tool module on the shared FastMCP
instance (`server.mcp`) — each module's `@mcp.tool()` decorators run at
import time.

To add a new tool:
  1. create a new module in this package (e.g. `weather.py`) that does
     `from ..server import mcp` and decorates functions with `@mcp.tool()`;
  2. import it below.

Nothing else changes — auth, gateway, and observability wrap the whole
server, so every new tool is automatically protected and instrumented.
"""

from . import calculator  # noqa: F401
