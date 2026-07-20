"""Correlation IDs: attach a unique ID to every request so an AI agent's
request can be traced through the entire system.

Incoming `X-Correlation-ID` headers (propagated by the agent-server) are
honored so one ID follows the request across service boundaries; otherwise a
new ID is minted. The ID is stored in a contextvar so *every* log line emitted
while handling the request carries it (see `logging.py`), and echoed back in
the response header.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    return _correlation_id.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        cid = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        _correlation_id.set(cid)
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = cid
        return response
