"""Central configuration for the MCP server.

Mirrors the MCP authorization tutorial's `config.py`: the authorization
server is an external Keycloak instance, addressed by host/port/realm, and
the MCP server holds confidential-client credentials for RFC 7662 token
introspection.

Values are loaded from the service-local `.env` file (self-contained env per
service). Anything security-sensitive goes through the SecretsManager
(`secrets_manager.py`) which validates required values at startup and fails
fast when required secrets are missing.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MCP_", extra="ignore"
    )

    # Auth server (Keycloak) — see keycloak/docker-compose.yml
    auth_host: str = "localhost"
    auth_port: int = 8080
    auth_realm: str = "master"

    # Confidential client used to call Keycloak's introspection endpoint.
    # The secret is required — validated at startup by SecretsManager.
    oauth_client_id: str = "mcp-server"
    oauth_client_secret: str = ""

    # Canonical resource URI of this MCP server (RFC 8707 resource indicator;
    # must equal the audience Keycloak's mapper embeds in tokens).
    resource_url: str = "http://localhost:8000/mcp"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Scope the MCP endpoint requires (least privilege)
    required_scope: str = "calculator:use"

    # Gateway
    rate_limit_rpm: int = 60
    cors_origins: str = "http://localhost:8501,http://localhost:8001"
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: int = 30

    # Observability
    log_level: str = "INFO"
    otel_console_export: bool = False

    @property
    def auth_base_url(self) -> str:
        """Keycloak realm base URL — the token issuer."""
        return f"http://{self.auth_host}:{self.auth_port}/realms/{self.auth_realm}"

    @property
    def issuer(self) -> str:
        return self.auth_base_url

    @property
    def introspection_endpoint(self) -> str:
        """RFC 7662 token-introspection endpoint (Keycloak layout)."""
        return f"{self.auth_base_url}/protocol/openid-connect/token/introspect"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
