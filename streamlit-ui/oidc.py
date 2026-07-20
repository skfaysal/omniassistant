"""Minimal OIDC Authorization-Code + PKCE client for the Streamlit UI.

Streamlit's built-in `st.login()` only exposes identity claims, not the raw
access token — but we need that token to call the agent-server on the user's
behalf. So we run the flow ourselves.

The one tricky part: the OAuth redirect reloads the page, which wipes
`st.session_state`. We therefore stash the per-login PKCE verifier in a
*module-global* dict keyed by `state`; module globals live in the single
long-running Streamlit server process, so they survive the redirect. After the
code exchange the tokens live in `st.session_state` (stable for the session).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time

import requests
from dotenv import load_dotenv

# Load this service's .env HERE, before reading any OIDC_* values — otherwise
# whether these are populated would depend on the caller importing us only
# after its own load_dotenv() (a subtle, easy-to-break ordering dependency).
load_dotenv()

KEYCLOAK_URL = os.environ.get("OIDC_KEYCLOAK_URL", "http://localhost:8080")
REALM = os.environ.get("OIDC_REALM", "master")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "streamlit-ui")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8501/")
# Request the calculator scopes so Keycloak's audience mappers fire and the
# token is valid at both the agent and the MCP server.
SCOPES = os.environ.get("OIDC_SCOPES", "openid profile email calculator:use")

_BASE = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect"
AUTHORIZE_ENDPOINT = f"{_BASE}/auth"
TOKEN_ENDPOINT = f"{_BASE}/token"
LOGOUT_ENDPOINT = f"{_BASE}/logout"

# state -> (pkce_verifier, created_at). Survives the redirect (process global).
_pending: dict[str, tuple[str, float]] = {}
_PENDING_TTL_SECONDS = 600


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def _purge_pending() -> None:
    now = time.time()
    for k in [k for k, (_, ts) in _pending.items() if now - ts > _PENDING_TTL_SECONDS]:
        _pending.pop(k, None)


def login_url() -> str:
    """Build the authorization URL and remember this login's PKCE verifier."""
    _purge_pending()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    _pending[state] = (verifier, time.time())
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_ENDPOINT}?{urlencode(params)}"


def exchange_code(code: str, state: str) -> dict:
    """Exchange an authorization code for tokens. Raises on failure or on an
    unknown `state` (CSRF / stale login)."""
    entry = _pending.pop(state, None)
    if entry is None:
        raise RuntimeError("Unknown or expired login state — please try again.")
    verifier, _ = entry
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code_verifier": verifier,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {resp.text}")
    return resp.json()


def refresh(refresh_token: str) -> dict:
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Refresh failed: {resp.text}")
    return resp.json()


def logout_url(id_token: str | None = None) -> str:
    from urllib.parse import urlencode

    params = {"post_logout_redirect_uri": REDIRECT_URI, "client_id": CLIENT_ID}
    if id_token:
        params["id_token_hint"] = id_token
    return f"{LOGOUT_ENDPOINT}?{urlencode(params)}"


def decode_claims(jwt_token: str) -> dict:
    """Best-effort, *unverified* decode of a JWT payload — for display only.
    (Trust decisions happen server-side via introspection, never here.)"""
    try:
        payload = jwt_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}
