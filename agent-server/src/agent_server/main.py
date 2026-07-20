"""FastAPI wrapper around the LangGraph agent.

Endpoints:
  POST /chat   — one chat turn; body: {"message": str, "history": [{role, content}]}
  GET  /health — health endpoint (also probes the MCP server) for load balancers
"""

from __future__ import annotations

import logging
import time

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import AuthenticatedUser, UserDep
from .observability import CorrelationIdMiddleware, setup_logging
from .settings import get_settings, validate_startup

logger = logging.getLogger(__name__)
_started_at = time.time()


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = []


def create_app() -> FastAPI:
    load_dotenv()  # this service's own .env (self-contained per service)
    settings = get_settings()
    setup_logging(settings.log_level)
    validate_startup()  # fail fast on missing secrets

    app = FastAPI(title="Agent Server", description="LangGraph agent over a secure remote MCP server")
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/health")
    async def health() -> dict:
        mcp_status = "unreachable"
        try:
            base = settings.mcp_server_url.rsplit("/mcp", 1)[0]
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{base}/health")
                mcp_status = "ok" if resp.status_code == 200 else f"http {resp.status_code}"
        except httpx.HTTPError:
            pass
        return {
            "status": "ok",
            "service": "agent-server",
            "uptime_seconds": round(time.time() - _started_at, 1),
            "mcp_server": mcp_status,
        }

    @app.post("/chat")
    async def chat(request: ChatRequest, user: AuthenticatedUser = UserDep):
        from .agent import run_agent  # deferred: import cost + env must be loaded

        messages = [{"role": t.role, "content": t.content} for t in request.history]
        messages.append({"role": "user", "content": request.message})
        logger.info("Chat turn", extra={"sub": user.sub, "username": user.username})
        try:
            # Forward the user's own token: the agent acts on their behalf.
            return await run_agent(messages, token=user.token)
        except Exception:
            logger.exception("Agent turn failed")
            return JSONResponse(
                status_code=500,
                content={"error": "agent_error", "detail": "Agent failed to process the request"},
            )

    return app


def run() -> None:
    load_dotenv()
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    run()
