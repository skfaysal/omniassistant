"""Per-request authenticated-user context for multi-user security.

The MCP SDK's `AuthContextMiddleware` (installed automatically by FastMCP when
a `token_verifier` is configured) stores the introspected `AccessToken` in a
request-scoped contextvar. This module adapts it into a small
`AuthenticatedUser` view so tools can scope every operation — data access and
log lines — to the current user (least privilege) without depending on SDK
types directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mcp.server.auth.middleware.auth_context import get_access_token


@dataclass(frozen=True)
class AuthenticatedUser:
    sub: str
    """Stable user id — Keycloak's `sub` claim. Keys all per-user data."""
    username: str
    """Human-readable login name (for messages/logs), e.g. `alice`."""
    client_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)


def get_current_user() -> AuthenticatedUser:
    token = get_access_token()
    if token is None:
        raise PermissionError("No authenticated user in request context")
    claims = token.claims or {}
    return AuthenticatedUser(
        sub=token.subject or token.client_id,
        username=claims.get("username")
        or claims.get("preferred_username")
        or token.subject
        or token.client_id,
        client_id=token.client_id,
        scopes=frozenset(token.scopes),
    )
