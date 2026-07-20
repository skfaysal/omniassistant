"""Token verifier implementation using OAuth 2.0 Token Introspection (RFC 7662).

This is the MCP authorization tutorial's Python pattern
(https://modelcontextprotocol.io/docs/tutorials/security/authorization):
instead of validating JWTs locally, every Bearer token is sent to the
authorization server's (Keycloak's) introspection endpoint, which answers
"is this token active, and what are its claims?". The verifier then checks
that the token's `aud` (audience) matches *this* MCP server's canonical
resource URL (RFC 8707) — a token minted for some other API is rejected even
if Keycloak says it is active (no token passthrough).

Plugged into FastMCP via `token_verifier=` (see `server.py`); the SDK's
bearer-auth middleware calls `verify_token()` for each request to the
protected `/mcp` endpoint and enforces the required scopes.

Observability: every outcome is recorded as a security event and counted in
the auth-failure metric, preserving this project's audit-trail pattern.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url

from ..observability.metrics import AUTH_FAILURES
from ..observability.security_events import log_security_event

logger = logging.getLogger(__name__)


class IntrospectionTokenVerifier(TokenVerifier):
    """Token verifier that uses OAuth 2.0 Token Introspection (RFC 7662)."""

    def __init__(
        self,
        introspection_endpoint: str,
        server_url: str,
        client_id: str,
        client_secret: str,
    ):
        self.introspection_endpoint = introspection_endpoint
        self.server_url = server_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.resource_url = resource_url_from_server_url(server_url)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token via the authorization server's introspection endpoint."""
        # Enforce HTTPS in production; plain HTTP only for local development.
        if not self.introspection_endpoint.startswith(
            ("https://", "http://localhost", "http://127.0.0.1")
        ):
            logger.error("Introspection endpoint must be HTTPS (or localhost)")
            return None

        timeout = httpx.Timeout(10.0, connect=5.0)
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        async with httpx.AsyncClient(timeout=timeout, limits=limits, verify=True) as client:
            try:
                # The confidential `mcp-server` client authenticates to
                # Keycloak; the *user's* token is the subject of the query.
                response = await client.post(
                    self.introspection_endpoint,
                    data={
                        "token": token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if response.status_code != 200:
                    AUTH_FAILURES.labels(reason="introspection_error").inc()
                    log_security_event(
                        "auth_introspection_error", status=response.status_code
                    )
                    return None

                data = response.json()
                if not data.get("active", False):
                    # Expired, revoked, or forged — Keycloak refuses it.
                    AUTH_FAILURES.labels(reason="inactive_token").inc()
                    log_security_event("auth_token_inactive")
                    return None

                if not self._validate_resource(data):
                    # Active token, but minted for a different resource server.
                    AUTH_FAILURES.labels(reason="wrong_audience").inc()
                    log_security_event(
                        "auth_wrong_audience", aud=data.get("aud"), sub=data.get("sub")
                    )
                    return None

                log_security_event(
                    "auth_success",
                    sub=data.get("sub"),
                    username=data.get("username"),
                    client_id=data.get("client_id"),
                )
                return AccessToken(
                    token=token,
                    client_id=data.get("client_id", "unknown"),
                    scopes=data.get("scope", "").split() if data.get("scope") else [],
                    expires_at=data.get("exp"),
                    # This server's canonical resource URL (a string). `aud` may
                    # be a *list* when the token targets several resources — we
                    # already validated our URL is among them above.
                    resource=self.resource_url,
                    subject=data.get("sub"),
                    claims=data,  # full introspection response for downstream use
                )

            except Exception as exc:
                AUTH_FAILURES.labels(reason="introspection_unreachable").inc()
                log_security_event("auth_introspection_unreachable", error=str(exc))
                logger.warning("Token introspection failed: %s", exc)
                return None

    def _validate_resource(self, token_data: dict[str, Any]) -> bool:
        """Validate token was issued for this resource server (RFC 8707).

        Rules:
        - Reject if `aud` missing.
        - Accept if any audience entry matches the derived resource URL.
        - Supports string or list forms per the JWT spec.
        """
        if not self.server_url or not self.resource_url:
            return False

        aud: list[str] | str | None = token_data.get("aud")
        if isinstance(aud, list):
            return any(self._is_valid_resource(a) for a in aud)
        if isinstance(aud, str):
            return self._is_valid_resource(aud)
        return False

    def _is_valid_resource(self, resource: str) -> bool:
        """Check if the given resource matches our server."""
        return check_resource_allowed(self.resource_url, resource)
