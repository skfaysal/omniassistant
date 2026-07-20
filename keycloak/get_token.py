#!/usr/bin/env python3
"""Fetch a user access token for the MCP server — e.g. to paste into MCP Inspector.

Runs the same OAuth 2.1 flow the Streamlit UI uses (authorization code + PKCE
against the `streamlit-ui` Keycloak client), signing in with a username and
password you provide, and prints the resulting access token. Because Keycloak
stamps the token with the MCP server's audience, it is accepted at
`http://localhost:8000/mcp`.

The token's lifespan follows the realm's access-token setting (30 min in this
demo, configured by setup_keycloak.py); re-run to get a fresh one.

Usage:
    python3 keycloak/get_token.py                    # prompts for username + password
    python3 keycloak/get_token.py -u faysal          # prompts for password only
    python3 keycloak/get_token.py -u faysal -p PASS  # non-interactive
    python3 keycloak/get_token.py -u faysal --token-only   # print ONLY the token

Stdlib only — no dependencies. Requires Keycloak running + configured
(keycloak/setup_keycloak.py). No account is seeded; register one in the UI first.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import html
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

KEYCLOAK_URL = os.environ.get("OIDC_KEYCLOAK_URL", "http://localhost:8080")
REALM = os.environ.get("OIDC_REALM", "master")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "streamlit-ui")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "streamlit-ui-secret")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8501/")
SCOPE = os.environ.get("OIDC_SCOPES", "openid calculator:use")

_BASE = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep 302s so we can read the Location header (the auth code) ourselves."""

    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(method: str, url: str, *, data=None, headers=None):
    """Return (status, headers, body_text) without following redirects."""
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        resp = _opener.open(req)
        return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 3xx (no-redirect) and 4xx/5xx land here
        return exc.code, exc.headers, exc.read().decode("utf-8", "replace")


def _cookies(headers) -> str:
    """Build a Cookie header from Set-Cookie. Keycloak marks its login cookies
    Secure; a browser sends them to localhost anyway, so we forward them too."""
    pairs = [
        sc.split(";", 1)[0]
        for sc in headers.get_all("Set-Cookie") or []
        if "=" in sc.split(";", 1)[0]
    ]
    return "; ".join(pairs)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def get_token(username: str, password: str) -> dict:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)

    # 1. Authorization request (PKCE) -> Keycloak login page
    authorize = f"{_BASE}/auth?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    status, headers, page = _request("GET", authorize)
    if status != 200:
        sys.exit(f"Unexpected authorize response ({status}). Is Keycloak configured?")
    m = re.search(r'<form[^>]+action="([^"]+)"', page)
    if not m:
        sys.exit("Could not find the Keycloak login form (already logged in elsewhere?).")
    form_action = html.unescape(m.group(1))

    # 2. Submit credentials -> 302 back to the redirect URI with ?code=
    status, headers, _ = _request(
        "POST", form_action,
        data={"username": username, "password": password},
        headers={"Cookie": _cookies(headers), "Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 302:
        sys.exit("Login failed — check the username/password (HTTP %s)." % status)
    location = headers["Location"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    if "code" not in query:
        sys.exit(f"No authorization code returned (redirect: {location}).")
    code = query["code"][0]

    # 3. Exchange the code (+ PKCE verifier) for tokens
    status, _, body = _request(
        "POST", f"{_BASE}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 200:
        sys.exit(f"Token exchange failed ({status}): {body}")
    import json
    return json.loads(body)


def main() -> None:
    ap = argparse.ArgumentParser(description="Get a user access token for the MCP server.")
    ap.add_argument("-u", "--username", help="Keycloak username (prompted if omitted)")
    ap.add_argument("-p", "--password", help="password (prompted securely if omitted)")
    ap.add_argument("--token-only", action="store_true",
                    help="print ONLY the access token (for piping / copy-paste)")
    args = ap.parse_args()

    username = args.username or input("Username: ").strip()
    password = args.password or getpass.getpass("Password: ")

    tokens = get_token(username, password)
    access_token = tokens["access_token"]

    if args.token_only:
        print(access_token)
        return

    print("\nAccess token (valid ~%ss):\n" % tokens.get("expires_in", "?"))
    print(access_token)
    print("\nPaste it into MCP Inspector's Authentication field, or use it directly:")
    print(f'  curl -H "Authorization: Bearer $TOKEN" -X POST {os.environ.get("MCP_URL", "http://localhost:8000/mcp")}')


if __name__ == "__main__":
    main()
