"""Rate limiting middleware (a gateway responsibility).

AI agents can issue requests far faster than humans; this token-bucket
limiter prevents resource exhaustion. Buckets are keyed per client IP (the
identity available before token validation runs), refill continuously at
`rate_limit_rpm / 60` tokens per second, and reject overflow with HTTP 429 +
`Retry-After`. Every rejection is a recorded security event ("unusual access
patterns") and a Prometheus counter increment.

In a full production topology this lives once at the AI gateway tier
(centralized policy management) instead of inside each server instance.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..observability.metrics import RATE_LIMITED
from ..observability.security_events import log_security_event
from ..settings import get_settings

# Paths exempt from limiting so load balancers and scrapers are never throttled
EXEMPT_PATHS = ("/health", "/metrics")

# Memory guard: one bucket exists per client IP. If a flood of distinct IPs
# (or spoofed source addresses) pushes past this bound, reset the table —
# a crude but safe fallback (buckets restart full, so no one is locked out).
MAX_BUCKETS = 10_000


class _TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float) -> None:
        self.capacity = capacity
        self.tokens = capacity
        self.refill_per_second = refill_per_second
        self.updated_at = time.monotonic()

    def try_consume(self) -> bool:
        now = time.monotonic()
        self.tokens = min(
            self.capacity, self.tokens + (now - self.updated_at) * self.refill_per_second
        )
        self.updated_at = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._buckets: dict[str, _TokenBucket] = {}

    def _bucket_for(self, key: str) -> _TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= MAX_BUCKETS:
                self._buckets.clear()
            rpm = get_settings().rate_limit_rpm
            bucket = _TokenBucket(capacity=rpm, refill_per_second=rpm / 60.0)
            self._buckets[key] = bucket
        return bucket

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith(EXEMPT_PATHS):
            return await call_next(request)

        key = request.client.host if request.client else "unknown"
        if not self._bucket_for(key).try_consume():
            RATE_LIMITED.inc()
            log_security_event("rate_limited", ip=key, path=request.url.path)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "error_description": "Too many requests; slow down.",
                },
                headers={"Retry-After": "1"},
            )
        return await call_next(request)
