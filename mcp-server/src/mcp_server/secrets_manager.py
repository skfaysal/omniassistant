"""Secrets management strategy.

Design goals implemented here:

1. **Provider abstraction** — secrets are read through a `SecretProvider`
   interface, not scattered `os.environ` lookups. The default provider reads
   the service-local `.env`/environment; `VaultSecretProvider`,
   `AwsSecretsManagerProvider` and `AzureKeyVaultProvider` show where a real
   deployment would plug in a dedicated secrets service.
2. **Startup validation / fail fast** — `validate_startup()` raises before
   the server binds a port if required configuration is missing (here: the
   Keycloak introspection client secret, `MCP_OAUTH_CLIENT_SECRET`).
3. **Compartmentalized access** — each service in this repo has its own env
   file and only reads the secrets it needs. Token *signing* keys don't
   appear here at all: they live inside Keycloak, never in this process.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class SecretProvider(ABC):
    """Interface every secrets backend implements."""

    @abstractmethod
    def get(self, name: str) -> str | None: ...


class EnvironmentSecretProvider(SecretProvider):
    """Default provider: process environment (populated from the local .env)."""

    def get(self, name: str) -> str | None:
        return os.environ.get(name)


class VaultSecretProvider(SecretProvider):
    """Placeholder for HashiCorp Vault — a recommended production path."""

    def get(self, name: str) -> str | None:
        raise NotImplementedError(
            "Wire up hvac + a Vault agent / workload identity here in production."
        )


class AwsSecretsManagerProvider(SecretProvider):
    """Placeholder for AWS Secrets Manager (use an IAM role — workload identity)."""

    def get(self, name: str) -> str | None:
        raise NotImplementedError(
            "Wire up boto3 secretsmanager with an IAM task/pod role here in production."
        )


class AzureKeyVaultProvider(SecretProvider):
    """Placeholder for Azure Key Vault (use a managed identity — workload identity)."""

    def get(self, name: str) -> str | None:
        raise NotImplementedError(
            "Wire up azure-keyvault-secrets with a managed identity here in production."
        )


class SecretsManager:
    def __init__(self, provider: SecretProvider | None = None) -> None:
        self._provider = provider or EnvironmentSecretProvider()

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self._provider.get(name)
        return value if value is not None else default

    def validate_startup(self, required: list[str]) -> None:
        """Fail fast: refuse to start if any required secret/config is missing."""
        missing = [name for name in required if not self._provider.get(name)]
        if missing:
            raise RuntimeError(
                f"Startup validation failed — missing required configuration: "
                f"{', '.join(missing)}. Copy .env.example to .env and fill these in."
            )
        logger.info("Startup secret validation passed", extra={"required": required})


secrets_manager = SecretsManager()
