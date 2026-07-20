#!/usr/bin/env python3
"""One-shot Keycloak configuration for the OmniAssistant demo.

Automates exactly the manual admin-console steps from the MCP authorization
tutorial (https://modelcontextprotocol.io/docs/tutorials/security/authorization):

1. Create the `calculator:use` client scope (type **Default**, "Include in
   token scope" on) — the tutorial's `mcp:tools` scope.
2. Add an **Audience** mapper (`audience-config`) to each scope with the
   MCP server's canonical resource URL as the *Included Custom Audience* —
   this embeds `aud=http://localhost:8000/mcp` in every issued token so the
   MCP server can verify tokens were minted for *it* (no token passthrough).
3. Configure anonymous **Dynamic Client Registration** policy:
   *Trusted Hosts* with "Client URIs Must Match" disabled (as in the
   tutorial) so the agent-server can self-register from localhost.
   The tutorial's *Consent Required* policy is removed because our OAuth
   client (the agent-server) is headless — there is no human to click
   "consent" (interactive clients like VS Code would see that screen).
4. Add a second **Audience** mapper so user tokens also carry the
   agent-server audience — one JWT is valid at both the agent and the MCP
   server.
5. Configure the realm: enable **self-registration** (users create their own
   account from the Streamlit login page) and set a dev-friendly 30-min
   access-token lifespan (demo-only). Deliberately seeds **no human
   accounts** — a production system never ships standing credentials in a
   committed script. (Automated tests provision an *ephemeral* user with a
   generated password via the admin API and delete it afterwards — see
   `test_identity.py`.)
6. Create the confidential clients:
   * `mcp-server` and `agent-server` — used only to call the token
     **introspection** endpoint (RFC 7662) to validate incoming user tokens.
   * `streamlit-ui` — the browser login client (authorization code + PKCE)
     that signs the *human* in.

Idempotent: safe to re-run; existing objects are left in place.

Usage:  python3 keycloak/setup_keycloak.py   (after `docker compose up -d`)
Stdlib only — no dependencies to install.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KEYCLOAK_URL = "http://localhost:8080"
REALM = "master"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin"

# Must match mcp-server/.env (MCP_RESOURCE_URL) and the audience the
# IntrospectionTokenVerifier validates.
MCP_RESOURCE_URL = "http://localhost:8000/mcp"
# The agent-server's own resource URL. The user's single token is stamped with
# BOTH audiences, so it is valid at the agent *and* at the MCP server.
AGENT_RESOURCE_URL = "http://localhost:8001"
SCOPES = ["calculator:use"]

# Confidential client the MCP server uses for token introspection
# (mcp-server/.env: MCP_OAUTH_CLIENT_ID / MCP_OAUTH_CLIENT_SECRET).
MCP_CLIENT_ID = "mcp-server"
MCP_CLIENT_SECRET = "mcp-server-secret"  # demo only — rotate in production

# Confidential client the agent-server uses for token introspection
# (agent-server/.env: AGENT_OAUTH_CLIENT_ID / AGENT_OAUTH_CLIENT_SECRET).
AGENT_CLIENT_ID = "agent-server"
AGENT_CLIENT_SECRET = "agent-server-secret"  # demo only

# Public-facing confidential client the Streamlit UI uses to log the *human*
# in (authorization code + PKCE). streamlit-ui/.env: OIDC_CLIENT_ID / SECRET.
UI_CLIENT_ID = "streamlit-ui"
UI_CLIENT_SECRET = "streamlit-ui-secret"  # demo only
UI_REDIRECT_URI = "http://localhost:8501/"

ADMIN = f"{KEYCLOAK_URL}/admin/realms/{REALM}"


def request(
    method: str,
    url: str,
    token: str | None = None,
    json_body: dict | list | None = None,
    form_body: dict | None = None,
) -> tuple[int, object]:
    headers: dict[str, str] = {}
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(json_body).encode()
    if form_body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form_body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body) if body else None
        except json.JSONDecodeError:
            return e.code, body.decode(errors="replace")


def wait_for_keycloak(timeout_seconds: int = 120) -> None:
    print(f"Waiting for Keycloak at {KEYCLOAK_URL} ...", end="", flush=True)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            status, _ = request("GET", f"{KEYCLOAK_URL}/realms/{REALM}")
            if status == 200:
                print(" up.")
                return
        except (urllib.error.URLError, ConnectionError):
            pass
        print(".", end="", flush=True)
        time.sleep(2)
    sys.exit(f"\nKeycloak did not come up within {timeout_seconds}s. "
             "Is it running? (cd keycloak && docker compose up -d)")


def admin_token() -> str:
    status, body = request(
        "POST",
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        form_body={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASSWORD,
        },
    )
    if status != 200:
        sys.exit(f"Admin login failed ({status}): {body}")
    return body["access_token"]  # type: ignore[index]


def create_client_scopes(token: str) -> None:
    """Tutorial: 'Client scopes → create scope, type Default, include in token
    scope' + 'Mappers → Audience → Included Custom Audience = server URL'."""
    status, existing = request("GET", f"{ADMIN}/client-scopes", token)
    existing_by_name = {s["name"]: s for s in existing}  # type: ignore[union-attr]

    for scope_name in SCOPES:
        if scope_name in existing_by_name:
            print(f"Client scope '{scope_name}' already exists — skipping.")
            continue
        payload = {
            "name": scope_name,
            "protocol": "openid-connect",
            "attributes": {
                "include.in.token.scope": "true",
                "display.on.consent.screen": "true",
            },
            "protocolMappers": [
                {
                    # Tutorial's `audience-config` mapper: embeds the MCP
                    # server's canonical URL as the token's `aud` claim.
                    "name": "audience-config",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-audience-mapper",
                    "consentRequired": False,
                    "config": {
                        "included.custom.audience": MCP_RESOURCE_URL,
                        "access.token.claim": "true",
                        "id.token.claim": "false",
                    },
                }
            ],
        }
        status, body = request("POST", f"{ADMIN}/client-scopes", token, payload)
        if status not in (201, 409):
            sys.exit(f"Failed to create scope '{scope_name}' ({status}): {body}")
        print(f"Created client scope '{scope_name}' with audience mapper.")

    # Type **Default**: auto-attached to every (dynamically registered) client,
    # so tokens for the agent carry the calculator scopes.
    status, scopes = request("GET", f"{ADMIN}/client-scopes", token)
    ids = {s["name"]: s["id"] for s in scopes}  # type: ignore[union-attr]
    for scope_name in SCOPES:
        status, _ = request(
            "PUT", f"{ADMIN}/default-default-client-scopes/{ids[scope_name]}", token
        )
        if status not in (204, 409):
            sys.exit(f"Failed to make '{scope_name}' a default scope ({status})")
        print(f"Marked '{scope_name}' as a realm-default client scope.")


def ensure_agent_audience(token: str) -> None:
    """Add a second Audience mapper so the user's token also carries the
    agent-server audience — one JWT, valid at both the agent and the MCP
    server. Idempotent (independent of scope creation)."""
    status, scopes = request("GET", f"{ADMIN}/client-scopes", token)
    scope = next((s for s in scopes if s["name"] == "calculator:use"), None)  # type: ignore[union-attr]
    if scope is None:
        sys.exit("calculator:use scope missing — cannot add agent audience.")
    status, mappers = request(
        "GET", f"{ADMIN}/client-scopes/{scope['id']}/protocol-mappers/models", token
    )
    if any(m.get("name") == "agent-audience-config" for m in mappers):  # type: ignore[union-attr]
        print("Agent audience mapper already present — skipping.")
        return
    payload = {
        "name": "agent-audience-config",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {
            "included.custom.audience": AGENT_RESOURCE_URL,
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    }
    status, body = request(
        "POST", f"{ADMIN}/client-scopes/{scope['id']}/protocol-mappers/models", token, payload
    )
    if status not in (201, 409):
        sys.exit(f"Failed to add agent audience mapper ({status}): {body}")
    print(f"Added agent-server audience mapper (aud += {AGENT_RESOURCE_URL}).")


# Access-token lifespan. Keycloak's `master` realm defaults to 60s, which is a
# good security default but makes hand-testing (e.g. pasting a token into MCP
# Inspector) tedious. 30 min is a deliberate *demo-only* relaxation — a
# production realm keeps tokens short-lived. See the README production notes.
ACCESS_TOKEN_LIFESPAN_SECONDS = 1800


def configure_realm(token: str) -> None:
    """Enable self-registration (so users can create an account from the
    Streamlit login page) and set a dev-friendly access-token lifespan."""
    status, realm = request("GET", ADMIN, token)
    changed = False
    if not realm.get("registrationAllowed"):  # type: ignore[union-attr]
        realm["registrationAllowed"] = True  # type: ignore[index]
        changed = True
    if realm.get("accessTokenLifespan") != ACCESS_TOKEN_LIFESPAN_SECONDS:  # type: ignore[union-attr]
        realm["accessTokenLifespan"] = ACCESS_TOKEN_LIFESPAN_SECONDS  # type: ignore[index]
        changed = True
    if not changed:
        print("Realm registration + token lifespan already set — skipping.")
        return
    status, body = request("PUT", ADMIN, token, realm)
    if status != 204:
        sys.exit(f"Failed to update realm settings ({status}): {body}")
    print(
        f"Configured realm: self-registration on, access-token lifespan "
        f"{ACCESS_TOKEN_LIFESPAN_SECONDS}s (demo-only)."
    )


def configure_client_registration_policies(token: str) -> None:
    """Tutorial: 'Clients → Client registration → Trusted Hosts: disable
    Client URIs Must Match, add your host'. Plus: drop the Consent Required
    policy because our OAuth client is headless."""
    status, components = request(
        "GET",
        f"{ADMIN}/components?type=org.keycloak.services.clientregistration."
        "policy.ClientRegistrationPolicy",
        token,
    )
    if status != 200:
        sys.exit(f"Failed to list client-registration policies ({status})")

    for comp in components:  # type: ignore[union-attr]
        if comp.get("subType") != "anonymous":
            continue
        if comp["providerId"] == "trusted-hosts":
            comp["config"]["trusted-hosts"] = ["localhost", "127.0.0.1", "host.docker.internal"]
            # The host the request *comes from* is Docker's NAT gateway and
            # varies per setup (the tutorial has you fish the IP out of the
            # logs), so that check is disabled; instead the client's
            # registered URIs must be on a trusted host (our agent registers
            # a localhost redirect URI), which is deterministic everywhere.
            # Keycloak requires at least one of the two checks to stay on.
            comp["config"]["host-sending-registration-request-must-match"] = ["false"]
            comp["config"]["client-uris-must-match"] = ["true"]
            status, body = request(
                "PUT", f"{ADMIN}/components/{comp['id']}", token, comp
            )
            if status != 204:
                sys.exit(f"Failed to update Trusted Hosts policy ({status}): {body}")
            print("Updated anonymous DCR 'Trusted Hosts' policy (URI/host match off).")
        elif comp["providerId"] == "consent-required":
            status, _ = request("DELETE", f"{ADMIN}/components/{comp['id']}", token)
            if status != 204:
                sys.exit(f"Failed to remove Consent Required policy ({status})")
            print("Removed anonymous DCR 'Consent Required' policy (headless client).")


def create_confidential_client(
    token: str, client_id: str, secret: str, name: str,
    *, standard_flow: bool = False, redirect_uris: list[str] | None = None,
) -> None:
    """Create a confidential client. `standard_flow` on = browser login client
    (streamlit-ui); off = service client used only for token introspection
    (agent-server, mcp-server)."""
    status, clients = request("GET", f"{ADMIN}/clients?clientId={client_id}", token)
    if status == 200 and clients:
        print(f"Client '{client_id}' already exists — skipping.")
        return
    payload = {
        "clientId": client_id,
        "name": name,
        "protocol": "openid-connect",
        "publicClient": False,
        "secret": secret,
        "standardFlowEnabled": standard_flow,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": not standard_flow,
        "enabled": True,
    }
    if standard_flow:
        payload["redirectUris"] = redirect_uris or []
        payload["webOrigins"] = ["+"]
        payload["attributes"] = {"pkce.code.challenge.method": "S256"}
    status, body = request("POST", f"{ADMIN}/clients", token, payload)
    if status not in (201, 409):
        sys.exit(f"Failed to create '{client_id}' client ({status}): {body}")
    print(f"Created confidential client '{client_id}' (secret: {secret}).")


def main() -> None:
    wait_for_keycloak()
    token = admin_token()
    create_client_scopes(token)
    ensure_agent_audience(token)
    configure_realm(token)
    configure_client_registration_policies(token)
    # Introspection clients (verify tokens) — no browser flow.
    create_confidential_client(token, MCP_CLIENT_ID, MCP_CLIENT_SECRET,
                               "MCP Resource Server (introspection)")
    create_confidential_client(token, AGENT_CLIENT_ID, AGENT_CLIENT_SECRET,
                               "Agent Server (introspection)")
    # Browser login client for the human at the Streamlit UI.
    create_confidential_client(token, UI_CLIENT_ID, UI_CLIENT_SECRET,
                               "Streamlit UI (user login)",
                               standard_flow=True, redirect_uris=[UI_REDIRECT_URI, f"{UI_REDIRECT_URI}*"])
    print(
        "\nKeycloak is configured. Matching .env values:\n"
        f"  mcp-server/.env:   MCP_OAUTH_CLIENT_ID={MCP_CLIENT_ID}\n"
        f"                     MCP_OAUTH_CLIENT_SECRET={MCP_CLIENT_SECRET}\n"
        f"  agent-server/.env: AGENT_OAUTH_CLIENT_ID={AGENT_CLIENT_ID}\n"
        f"                     AGENT_OAUTH_CLIENT_SECRET={AGENT_CLIENT_SECRET}\n"
        f"  streamlit-ui/.env: OIDC_CLIENT_ID={UI_CLIENT_ID}\n"
        f"                     OIDC_CLIENT_SECRET={UI_CLIENT_SECRET}\n"
        f"\nNo human accounts were seeded — open the UI and use the Register link.\n"
        f"OIDC discovery: {KEYCLOAK_URL}/realms/{REALM}/.well-known/openid-configuration"
    )


if __name__ == "__main__":
    main()
