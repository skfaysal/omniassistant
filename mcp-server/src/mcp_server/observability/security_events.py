"""Security event logging: capture every authentication attempt,
authorization failure, and unusual access pattern with enough context for
forensic analysis.

All events flow through the dedicated `security` logger as structured JSON
(event type, user, client IP, correlation ID via the JSON formatter), so they
can be shipped to a SIEM and queried independently of application logs.

Event catalog:
  auth_success, auth_token_inactive, auth_wrong_audience,
  auth_introspection_error, auth_introspection_unreachable,
  rate_limited, tool_invoked, tool_denied

(Authorization-server events — logins, client registrations, token issuance —
now live in Keycloak's own event log: Realm settings → Sessions/Events.)
"""

from __future__ import annotations

import logging

security_logger = logging.getLogger("security")


def log_security_event(event_type: str, **context: object) -> None:
    """Emit a structured security event with forensic context."""
    security_logger.info(
        "security_event", extra={"event_type": event_type, **context}
    )
