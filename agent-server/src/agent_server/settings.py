"""Agent-server configuration — loaded from this service's own `.env`
(each service is self-contained with its own env + dependencies).

`validate_startup()` fails fast: refuse to boot without required secrets
instead of failing on the first request.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="AGENT_", extra="ignore"
    )

    llm_model: str = "anthropic:claude-sonnet-5"
    mcp_server_url: str = "http://localhost:8000/mcp"

    # Auth server (Keycloak) — the agent introspects the user's token here.
    auth_host: str = "localhost"
    auth_port: int = 8080
    auth_realm: str = "master"

    # Confidential client the agent uses to call the introspection endpoint.
    oauth_client_id: str = "agent-server"
    oauth_client_secret: str = ""

    # This agent's canonical resource URL — the audience the user's token must
    # carry to be accepted here.
    resource_url: str = "http://localhost:8001"
    required_scope: str = "calculator:use"

    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"

    @property
    def auth_base_url(self) -> str:
        return f"http://{self.auth_host}:{self.auth_port}/realms/{self.auth_realm}"

    @property
    def introspection_endpoint(self) -> str:
        return f"{self.auth_base_url}/protocol/openid-connect/token/introspect"


def validate_startup() -> None:
    """Fail fast when required secrets are missing."""
    required = ("ANTHROPIC_API_KEY", "AGENT_OAUTH_CLIENT_SECRET")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Startup validation failed — missing required secrets: "
            f"{', '.join(missing)}. Copy .env.example to .env and fill them in."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
