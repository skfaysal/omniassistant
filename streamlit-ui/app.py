"""Streamlit chatbot UI for the LangGraph agent server.

The **human logs in** here against Keycloak (OAuth 2.1 authorization code +
PKCE); the resulting user token is sent as a Bearer credential to the
agent-server, which forwards it to the MCP server. So every calculation is
attributed to the signed-in user, end to end.

Talks only to the agent-server's REST API; the sidebar surfaces the service
health endpoints and each reply shows which remote MCP tools were invoked.
"""

from __future__ import annotations

import os
import time
import uuid

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # this service's own .env — MUST precede `import oidc`, which
#                reads OIDC_* config at import time.

import oidc  # noqa: E402

AGENT_SERVER_URL = os.environ.get("AGENT_SERVER_URL", "http://localhost:8001")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "120"))
TOKEN_REFRESH_MARGIN = 30

st.set_page_config(page_title="Calculator Agent", page_icon="🧮")


# --- Auth helpers ------------------------------------------------------------
def _store_tokens(tokens: dict) -> None:
    st.session_state.auth = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "id_token": tokens.get("id_token"),
        "expires_at": time.time() + tokens.get("expires_in", 60),
        "claims": oidc.decode_claims(tokens["access_token"]),
    }


def _handle_redirect() -> None:
    """If Keycloak just redirected back with ?code=…, complete the exchange."""
    params = st.query_params
    if "code" in params and "auth" not in st.session_state:
        try:
            tokens = oidc.exchange_code(params["code"], params.get("state", ""))
            _store_tokens(tokens)
            st.query_params.clear()
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            # Surface the failure instead of silently looping back to login.
            st.query_params.clear()
            st.session_state.login_error = str(exc)
    elif "error" in params:  # Keycloak returned an error (e.g. access_denied)
        st.session_state.login_error = params.get("error_description", params["error"])
        st.query_params.clear()


def _valid_access_token() -> str | None:
    """Return a currently-valid access token, refreshing if near expiry."""
    auth = st.session_state.get("auth")
    if not auth:
        return None
    if time.time() < auth["expires_at"] - TOKEN_REFRESH_MARGIN:
        return auth["access_token"]
    if auth.get("refresh_token"):
        try:
            _store_tokens(oidc.refresh(auth["refresh_token"]))
            return st.session_state.auth["access_token"]
        except Exception:  # noqa: BLE001
            st.session_state.pop("auth", None)
    return None


_handle_redirect()


# --- Login gate --------------------------------------------------------------
if "auth" not in st.session_state:
    st.title("🧮 Calculator Agent")
    st.caption("A secure remote MCP calculator — please sign in to continue.")
    if err := st.session_state.pop("login_error", None):
        st.error(f"Login failed: {err}. Please try again.")
    # A same-tab link (not st.link_button, which forces target=_blank and would
    # complete the login in a different tab than the one the user is looking at).
    st.markdown(
        f'<a href="{oidc.login_url()}" target="_self" style="display:inline-block;'
        f"padding:0.5rem 1rem;background:#ff4b4b;color:#fff;border-radius:0.5rem;"
        f'text-decoration:none;font-weight:600;">🔐 Log in / Register</a>',
        unsafe_allow_html=True,
    )
    st.info(
        "You'll be taken to Keycloak to sign in or create an account. "
        "New here? Use the **Register** link on that page — no account exists "
        "until you make one."
    )
    st.stop()


# --- Logged in ---------------------------------------------------------------
claims = st.session_state.auth["claims"]
username = claims.get("preferred_username") or claims.get("email") or claims.get("sub", "user")

st.title("🧮 Calculator Agent")
st.caption(
    "Chatbot → LangGraph agent (FastAPI) → OAuth 2.1-secured remote MCP calculator"
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "correlation_seed" not in st.session_state:
    st.session_state.correlation_seed = str(uuid.uuid4())[:8]


# --- Sidebar: identity + service health --------------------------------------
with st.sidebar:
    st.subheader("Signed in")
    st.write(f"👤 **{username}**")
    st.link_button("Log out", oidc.logout_url(st.session_state.auth.get("id_token")))
    st.divider()

    st.subheader("Service health")
    try:
        health = requests.get(f"{AGENT_SERVER_URL}/health", timeout=3).json()
        st.success(f"agent-server: {health.get('status', '?')}")
        mcp = health.get("mcp_server", "unknown")
        (st.success if mcp == "ok" else st.error)(f"mcp-server: {mcp}")
    except requests.RequestException:
        st.error("agent-server: unreachable")
        st.error("mcp-server: unknown")
    st.divider()
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()
    st.caption(f"Agent server: {AGENT_SERVER_URL}")


# --- Chat history ------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        for call in msg.get("tool_calls", []):
            with st.expander(f"🔧 MCP tool: {call['tool']}"):
                st.code(f"args: {call['args']}\nresult: {call['result']}")


# --- Input -------------------------------------------------------------------
if prompt := st.chat_input("Ask me to calculate something…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            token = _valid_access_token()
            if token is None:
                st.warning("Your session expired — please log in again.")
                st.session_state.pop("auth", None)
                st.stop()
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages[:-1]
            ]
            try:
                resp = requests.post(
                    f"{AGENT_SERVER_URL}/chat",
                    json={"message": prompt, "history": history},
                    headers={
                        "Authorization": f"Bearer {token}",
                        # Correlation ID propagated through agent → MCP server
                        "X-Correlation-ID": f"ui-{st.session_state.correlation_seed}-{uuid.uuid4().hex[:8]}",
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code == 401:
                    st.warning("Your session expired — please log in again.")
                    st.session_state.pop("auth", None)
                    st.stop()
                resp.raise_for_status()
                data = resp.json()
                reply = data.get("reply", "(no reply)")
                tool_calls = data.get("tool_calls", [])
            except requests.RequestException as exc:
                reply = f"⚠️ Could not reach the agent server: {exc}"
                tool_calls = []

        st.markdown(reply)
        for call in tool_calls:
            with st.expander(f"🔧 MCP tool: {call['tool']}"):
                st.code(f"args: {call['args']}\nresult: {call['result']}")

    st.session_state.messages.append(
        {"role": "assistant", "content": reply, "tool_calls": tool_calls}
    )
