"""A very simple LangGraph ReAct agent that uses the calculator tool exposed
by the secure remote MCP server.

The agent acts **on behalf of the logged-in user**: `run_agent` receives the
user's Bearer token (validated at `/chat` — see `auth.py`) and forwards that
same token to the MCP server. Because Keycloak stamps the token with the MCP
server's audience too, one user login authorizes the whole chain.

The MCP connection uses the **streamable HTTP transport** and attaches:

* the user's OAuth 2.1 Bearer token,
* the current `X-Correlation-ID`, so the MCP server's logs join up with this
  request's logs across service boundaries.
"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from .observability import CORRELATION_HEADER, get_correlation_id
from .settings import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to a remote calculator service. "
    "Use the `calculate` tool for any arithmetic the user asks for. "
    "Answer concisely."
)


async def run_agent(messages: list[dict], token: str) -> dict:
    """Run one agent turn over the given chat messages, acting as the user
    whose Bearer `token` this is.

    Returns {"reply": str, "tool_calls": [{"tool", "args", "result"}, ...]}.
    """
    s = get_settings()

    mcp_client = MultiServerMCPClient(
        {
            "calculator": {
                "transport": "streamable_http",
                "url": s.mcp_server_url,
                "headers": {
                    "Authorization": f"Bearer {token}",
                    CORRELATION_HEADER: get_correlation_id(),
                },
            }
        }
    )
    tools = await mcp_client.get_tools()
    model = init_chat_model(s.llm_model)
    agent = create_react_agent(model, tools, prompt=SYSTEM_PROMPT)

    result = await agent.ainvoke({"messages": messages})
    out_messages = result["messages"]

    tool_calls: list[dict] = []
    tool_results: dict[str, str] = {}
    for msg in out_messages:
        if msg.type == "tool":
            tool_results[msg.tool_call_id] = str(msg.content)
    for msg in out_messages:
        if msg.type == "ai" and getattr(msg, "tool_calls", None):
            for call in msg.tool_calls:
                tool_calls.append(
                    {
                        "tool": call["name"],
                        "args": call["args"],
                        "result": tool_results.get(call["id"], ""),
                    }
                )

    reply = out_messages[-1].content if out_messages else ""
    if isinstance(reply, list):  # content blocks → plain text
        reply = " ".join(
            block.get("text", "") for block in reply if isinstance(block, dict)
        )
    logger.info(
        "Agent turn complete",
        extra={"tool_calls": [c["tool"] for c in tool_calls]},
    )
    return {"reply": reply, "tool_calls": tool_calls}
