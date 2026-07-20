"""Metrics collection: request latency, error rates — especially
authentication and authorization failures — and resource utilization.

Exposed in Prometheus format at `/metrics`; a Prometheus + Alertmanager +
Grafana stack points at that endpoint for automated alerting and dashboards.
`prometheus_client` also exports process CPU/memory gauges automatically
(resource utilization).
"""

from __future__ import annotations

import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "mcp_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "mcp_http_request_duration_seconds",
    "HTTP request latency (critical for AI agent responsiveness)",
    ["method", "path"],
)
AUTH_FAILURES = Counter(
    "mcp_auth_failures_total",
    "Authentication/authorization failures by reason",
    ["reason"],
)
RATE_LIMITED = Counter(
    "mcp_rate_limited_total",
    "Requests rejected by the rate limiter",
)
TOOL_CALLS = Counter(
    "mcp_tool_calls_total",
    "MCP tool invocations",
    ["tool", "outcome"],
)
CIRCUIT_STATE = Gauge(
    "mcp_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
)

# Bounded label cardinality: unknown paths (scanners, typos, 404s) collapse
# into a single "/other" series instead of creating one time series per
# arbitrary URL an attacker sends.
_KNOWN_PATHS = frozenset(
    {
        "/mcp",
        "/health",
        "/metrics",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    }
)


def _normalize_path(path: str) -> str:
    return path if path in _KNOWN_PATHS else "/other"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = _normalize_path(request.url.path)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            REQUEST_COUNT.labels(request.method, path, 500).inc()
            raise
        REQUEST_COUNT.labels(request.method, path, status).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(
            time.perf_counter() - start
        )
        return response


def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
