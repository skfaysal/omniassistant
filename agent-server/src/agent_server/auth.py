"""User-token authentication for the agent-server.

The Streamlit UI logs the *human* in against Keycloak and calls `/chat` with
the user's Bearer token. This module validates that token the same way the MCP
server does — via RFC 7662 token introspection, authenticating as the
confidential `agent-server` client — and exposes the caller's identity to the
endpoint.

The very same token is then forwarded to the MCP server (`agent.py`); Keycloak
stamps it with *both* audiences (`http://localhost:8001` for this agent and
`http://localhost:8000/mcp` for the MCP server), so one login is valid at both
hops. The gold-standard alternative is OAuth 2.0 Token Exchange (RFC 8693) —
the agent swapping the user token for an MCP-scoped one — noted as the
production hardening step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException, Request

from .settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthenticatedUser:
    sub: str
    username: str
    scopes: frozenset[str]
    token: str  # the raw token, forwarded downstream to the MCP server


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


async def _introspect(token: str) -> dict:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            resp = await client.post(
                s.introspection_endpoint,
                data={
                    "token": token,
                    "client_id": s.oauth_client_id,
                    "client_secret": s.oauth_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Introspection unreachable: %s", exc)
        raise _unauthorized("Could not validate token") from exc
    if resp.status_code != 200:
        raise _unauthorized("Token validation failed")
    return resp.json()


def _audience_ok(claims: dict, expected: str) -> bool:
    aud = claims.get("aud")
    if isinstance(aud, list):
        return expected in aud
    return aud == expected


async def require_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency: authenticate the caller from the Bearer token.

    401 for missing/invalid/expired tokens or wrong audience; 403 when the
    token lacks the required scope.
    """
    s = get_settings()
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise _unauthorized("Missing bearer token")
    token = header[7:].strip()

    claims = await _introspect(token)
    if not claims.get("active", False):
        raise _unauthorized("Token is inactive or expired")
    if not _audience_ok(claims, s.resource_url):
        # Token not minted for this agent — refuse it (no blind passthrough).
        raise _unauthorized("Token was not issued for this agent server")

    scopes = frozenset(claims.get("scope", "").split())
    if s.required_scope not in scopes:
        raise HTTPException(
            status_code=403,
            detail=f"Scope '{s.required_scope}' is required",
            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
        )

    return AuthenticatedUser(
        sub=claims.get("sub", ""),
        username=claims.get("preferred_username") or claims.get("username") or claims.get("sub", ""),
        scopes=scopes,
        token=token,
    )


UserDep = Depends(require_user)
