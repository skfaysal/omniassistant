#!/usr/bin/env python3
"""End-to-end verification of the user-identity flow.

Production-faithful test hygiene: this script **provisions its own ephemeral
user** (random username + generated password, via Keycloak's admin API),
runs the checks as that user, and **deletes the user in a `finally` block** —
no standing test account, no credentials in source control. The only static
secret is Keycloak's dev-default admin login, which in real CI would itself
come from a secrets manager.

What it verifies:
  1. A logged-in user's token carries BOTH audiences (agent + MCP server).
  2. That single token is accepted by the MCP server (what the agent forwards).
  3. The agent's /chat rejects a forged token (401) and accepts the real one.

Prereqs: Keycloak configured (setup_keycloak.py) and mcp-server + agent-server
running. Stdlib + `requests` only.

Usage:  python3 keycloak/test_identity.py
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

import requests

KEYCLOAK = "http://localhost:8080"
REALM = "master"
ADMIN_USER, ADMIN_PASSWORD = "admin", "admin"  # dev default; a real CI vaults this
KC = f"{KEYCLOAK}/realms/{REALM}/protocol/openid-connect"
ADMIN = f"{KEYCLOAK}/admin/realms/{REALM}"

UI_CLIENT, UI_SECRET = "streamlit-ui", "streamlit-ui-secret"
REDIRECT = "http://localhost:8501/"
MCP_URL = "http://localhost:8000/mcp"
AGENT_CHAT = "http://localhost:8001/chat"
AGENT_AUD, MCP_AUD = "http://localhost:8001", "http://localhost:8000/mcp"


# ---- admin helpers (ephemeral user lifecycle) -------------------------------

def _admin_token() -> str:
    r = requests.post(f"{KC}/token", data={
        "grant_type": "password", "client_id": "admin-cli",
        "username": ADMIN_USER, "password": ADMIN_PASSWORD,
    })
    r.raise_for_status()
    return r.json()["access_token"]


def create_ephemeral_user(admin: str) -> tuple[str, str, str]:
    """Create a throwaway user with a generated password. Returns (id, username, password)."""
    username = f"test-user-{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)  # generated; never persisted anywhere
    r = requests.post(f"{ADMIN}/users", headers={"Authorization": f"Bearer {admin}"}, json={
        "username": username, "enabled": True, "emailVerified": True,
        "email": f"{username}@example.test",
        "credentials": [{"type": "password", "value": password, "temporary": False}],
    })
    if r.status_code not in (201, 204):
        sys.exit(f"Failed to create ephemeral user ({r.status_code}): {r.text}")
    loc = requests.get(f"{ADMIN}/users?username={username}&exact=true",
                       headers={"Authorization": f"Bearer {admin}"}).json()
    return loc[0]["id"], username, password


def delete_user(admin: str, user_id: str) -> None:
    requests.delete(f"{ADMIN}/users/{user_id}", headers={"Authorization": f"Bearer {admin}"})


# ---- login (authorization code + PKCE, as the UI does) ----------------------

def _pkce() -> tuple[str, str]:
    v = secrets.token_urlsafe(64)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def _cookies(resp) -> str:
    # Keycloak marks login cookies Secure; a browser sends them to localhost
    # anyway, `requests` won't — forward them by hand (test only).
    raw = resp.raw.headers.get_all("Set-Cookie") if hasattr(resp.raw.headers, "get_all") else []
    return "; ".join(sc.split(";", 1)[0] for sc in raw if "=" in sc.split(";", 1)[0])


def login(username: str, password: str, scope="openid calculator:use") -> str:
    s = requests.Session()
    v, c = _pkce()
    state = secrets.token_urlsafe(16)
    r = s.get(f"{KC}/auth", params={
        "response_type": "code", "client_id": UI_CLIENT, "redirect_uri": REDIRECT,
        "scope": scope, "state": state, "code_challenge": c, "code_challenge_method": "S256",
    }, allow_redirects=False)
    action = html.unescape(re.search(r'<form[^>]+action="([^"]+)"', r.text).group(1))
    r2 = s.post(action, data={"username": username, "password": password},
                headers={"Cookie": _cookies(r)}, allow_redirects=False)
    assert r2.status_code == 302, f"login failed: {r2.status_code}"
    code = dict(x.split("=") for x in urllib.parse.urlparse(r2.headers["Location"]).query.split("&"))["code"]
    tok = s.post(f"{KC}/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": UI_CLIENT, "client_secret": UI_SECRET, "code_verifier": v,
    })
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


def claims(t: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(t.split(".")[1] + "=="))


def mcp(token: str, method: str, params: dict, rid: int):
    return requests.post(MCP_URL, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})


def main() -> None:
    admin = _admin_token()
    user_id, username, password = create_ephemeral_user(admin)
    print(f"0. PROVISIONED ephemeral user '{username}' (generated password)")
    try:
        token = login(username, password)
        cl = claims(token)
        print("1. LOGIN OK — sub:", cl["sub"], "user:", cl.get("preferred_username"))

        aud = cl["aud"] if isinstance(cl["aud"], list) else [cl["aud"]]
        assert AGENT_AUD in aud, f"missing agent aud: {aud}"
        assert MCP_AUD in aud, f"missing mcp aud: {aud}"
        print("2. DUAL AUDIENCE OK —", aud)

        mcp(token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "t", "version": "0"}}, 1)
        r = mcp(token, "tools/call", {"name": "calculate",
                "arguments": {"operation": "add", "a": 40, "b": 2}}, 2)
        assert "42" in r.text, r.text
        print("3. SAME TOKEN ACCEPTED BY MCP-SERVER OK →", r.json()["result"]["content"][0]["text"])

        bad = requests.post(AGENT_CHAT, headers={"Authorization": "Bearer forged"},
                            json={"message": "hi"})
        assert bad.status_code == 401, bad.status_code
        print("4. AGENT /chat forged token → 401 OK")

        good = requests.post(AGENT_CHAT, headers={"Authorization": f"Bearer {token}"},
                             json={"message": "what is 2+2"}, timeout=120)
        assert good.status_code not in (401, 403), f"auth wrongly rejected valid user: {good.status_code}"
        print(f"5. AGENT /chat valid user token → passed auth (status {good.status_code}"
              + (", LLM ran" if good.status_code == 200 else ", 500 = no/placeholder LLM key, auth OK") + ")")

        print("\nIDENTITY FLOW VERIFIED")
    finally:
        delete_user(admin, user_id)
        print(f"6. TORE DOWN ephemeral user '{username}'")


if __name__ == "__main__":
    main()
