"""Circuit breaker middleware — fail fast when the backend is struggling.

Tracks consecutive failures (unhandled exceptions / 5xx responses) on the MCP
endpoint. After `failure_threshold` consecutive failures the circuit OPENs
and requests fail fast with HTTP 503 instead of piling onto a struggling
backend. After `recovery_seconds` the circuit goes HALF-OPEN: one probe
request is allowed through; success closes the circuit, failure re-opens it.
"""

from __future__ import annotations

import enum
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..observability.metrics import CIRCUIT_STATE
from ..settings import get_settings

logger = logging.getLogger(__name__)


class State(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.state = State.CLOSED
        self.consecutive_failures = 0
        self.opened_at = 0.0

    def _set_state(self, state: State) -> None:
        if state is not self.state:
            logger.warning(
                "Circuit breaker state change", extra={"from": self.state.value, "to": state.value}
            )
        self.state = state
        CIRCUIT_STATE.set({State.CLOSED: 0, State.OPEN: 1, State.HALF_OPEN: 2}[state])

    def allow_request(self) -> bool:
        if self.state is State.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_seconds:
                self._set_state(State.HALF_OPEN)  # allow one probe through
                return True
            return False
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        if self.state is not State.CLOSED:
            self._set_state(State.CLOSED)

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if (
            self.state is State.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            self.opened_at = time.monotonic()
            self._set_state(State.OPEN)


class CircuitBreakerMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, protected_prefix: str = "/mcp") -> None:
        super().__init__(app)
        self._prefix = protected_prefix
        s = get_settings()
        self.breaker = CircuitBreaker(
            failure_threshold=s.circuit_failure_threshold,
            recovery_seconds=s.circuit_recovery_seconds,
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith(self._prefix):
            return await call_next(request)

        if not self.breaker.allow_request():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "circuit_open",
                    "error_description": "Service temporarily unavailable; failing fast.",
                },
                headers={"Retry-After": str(int(self.breaker.recovery_seconds))},
            )
        try:
            response = await call_next(request)
        except Exception:
            self.breaker.record_failure()
            raise
        if response.status_code >= 500:
            self.breaker.record_failure()
        else:
            self.breaker.record_success()
        return response
