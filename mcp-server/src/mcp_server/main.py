"""Application assembly for the secure remote MCP server.

Wires together every layer of the server:

  gateway middlewares              ─┐
  observability (logs/traces/      ─┼─→ FastAPI app ─→ mounted MCP
  metrics/health)                  ─┘    streamable-HTTP transport
                                          (SDK bearer-auth middleware
                                           + RFC 9728 metadata routes
                                           + stateless calculator tools)

Authorization is delegated to an external Keycloak server (see
`keycloak/docker-compose.yml`); the mounted FastMCP app validates tokens via
RFC 7662 introspection (`auth/token_verifier.py`, configured in `server.py`).

Middleware order (outermost → innermost), mirroring an AI-gateway topology
where cross-cutting policy runs before requests reach the MCP server:

  SecurityHeaders → CorrelationId → Metrics → RateLimit → CircuitBreaker
      → mounted MCP app (BearerAuth → AuthContext → RequireAuth → tools)
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .gateway.circuit_breaker import CircuitBreakerMiddleware
from .gateway.rate_limiter import RateLimitMiddleware
from .gateway.security_headers import SecurityHeadersMiddleware
from .observability.correlation import CorrelationIdMiddleware
from .observability.logging import setup_logging
from .observability.metrics import MetricsMiddleware, metrics_endpoint
from .observability.tracing import setup_tracing
from .secrets_manager import secrets_manager
from .server import mcp
from .settings import get_settings
from . import tools  # noqa: F401 — importing registers every tool module on `mcp`

logger = logging.getLogger(__name__)

SERVICE_NAME = "mcp-server"
_started_at = time.time()


def create_app() -> FastAPI:
    load_dotenv()  # this service's own .env (self-contained per service)
    settings = get_settings()

    # Observability first so even startup failures are structured
    setup_logging(SERVICE_NAME, settings.log_level)

    # Startup validation — fail fast if required configuration is missing.
    # The introspection client secret is a real secret (never in source code).
    secrets_manager.validate_startup(["MCP_OAUTH_CLIENT_SECRET"])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The streamable-HTTP session manager must run for the app's lifetime
        async with mcp.session_manager.run():
            logger.info(
                "MCP server started",
                extra={"resource": settings.resource_url, "issuer": settings.issuer},
            )
            yield

    app = FastAPI(
        title="Calculator MCP Server",
        description="Secure, scalable remote MCP server (OAuth 2.1 protected, "
        "Keycloak authorization server)",
        lifespan=lifespan,
    )

    # ---- Routes ------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        """Dedicated health endpoint for load balancers."""
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "uptime_seconds": round(time.time() - _started_at, 1),
        }

    @app.get("/metrics")
    async def metrics():
        """Prometheus metrics: latency, error rates, auth failures, resources."""
        return metrics_endpoint()

    # ---- Middleware (added innermost-first; Starlette wraps in reverse) ----
    app.add_middleware(CircuitBreakerMiddleware, protected_prefix="/mcp")
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # CORS handling (a gateway responsibility)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID", "Mcp-Session-Id"],
        expose_headers=["X-Correlation-ID", "Mcp-Session-Id"],
    )

    # Distributed tracing (OpenTelemetry) over the fully-assembled app
    setup_tracing(app, SERVICE_NAME, console_export=settings.otel_console_export)

    # ---- Mount the MCP app (mounted last so explicit routes above take
    # precedence). Because `auth`/`token_verifier` are configured (server.py),
    # this sub-app carries the SDK's bearer-auth middleware, the RFC 9728
    # metadata route and the 401/403 challenge behaviour ---------------------
    app.mount("/", mcp.streamable_http_app())

    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        log_config=None,  # keep our structured JSON logging
    )


if __name__ == "__main__":
    run()
