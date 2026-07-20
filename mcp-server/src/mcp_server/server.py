"""The shared FastMCP server instance, configured as an OAuth 2.1 resource
server exactly like the MCP authorization tutorial's Python example
(https://modelcontextprotocol.io/docs/tutorials/security/authorization):

* `token_verifier=IntrospectionTokenVerifier(...)` — every Bearer token is
  validated against Keycloak's RFC 7662 introspection endpoint, and its
  audience checked against this server's canonical resource URL.
* `auth=AuthSettings(...)` — the SDK then automatically:
    - serves RFC 9728 Protected Resource Metadata at
      `/.well-known/oauth-protected-resource/mcp` (pointing at Keycloak),
    - answers unauthenticated requests with `401` + a `WWW-Authenticate`
      header carrying that metadata URL (authorization-flow step 1),
    - enforces `required_scopes` on the `/mcp` endpoint (403 otherwise),
    - exposes the validated identity to tools via the auth context.

Lives in its own module so any number of tool modules can register against it
(see `tools/__init__.py`). Transport choices:

* `stateless_http=True` — every request is self-contained; no sticky sessions,
  so any horizontally-scaled replica behind a load balancer can serve any
  request.
* `json_response=True` — plain JSON responses instead of SSE streams, keeping
  the transport simple for request/response tools.
"""

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from .auth.token_verifier import IntrospectionTokenVerifier
from .settings import get_settings

_settings = get_settings()

_token_verifier = IntrospectionTokenVerifier(
    introspection_endpoint=_settings.introspection_endpoint,
    server_url=_settings.resource_url,
    client_id=_settings.oauth_client_id,
    client_secret=_settings.oauth_client_secret,
)

mcp = FastMCP(
    name="calculator",
    instructions="A secure remote calculator. Use `calculate` for arithmetic.",
    stateless_http=True,
    json_response=True,
    token_verifier=_token_verifier,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(_settings.issuer),
        required_scopes=[_settings.required_scope],
        resource_server_url=AnyHttpUrl(_settings.resource_url),
    ),
)
