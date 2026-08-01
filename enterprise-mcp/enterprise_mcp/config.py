from __future__ import annotations

import os
from dataclasses import dataclass

# The complete tool surface this server is capable of exposing. The effective
# surface is a subset chosen by ENTERPRISE_MCP_ENABLED_TOOLS.
ALL_TOOLS: tuple[str, ...] = (
    "check_enterprise_api",
    "get_incident_context",
    "propose_incident_plan",
    "apply_incident_plan",
)

# Read/plan tools only. This is the default surface: the mutating tool must be
# switched on deliberately.
DEFAULT_TOOLS: tuple[str, ...] = (
    "check_enterprise_api",
    "get_incident_context",
    "propose_incident_plan",
)


class ConfigurationError(RuntimeError):
    """Raised when the server is asked to run without required configuration."""


@dataclass(frozen=True)
class McpSettings:
    api_url: str
    api_token: str
    api_write_token: str | None
    timeout_seconds: float
    inject_failure: str | None

    @property
    def can_mutate(self) -> bool:
        return bool(self.api_write_token)


def resolve_enabled_tools(raw: str | None) -> frozenset[str]:
    """Resolve the allowlist for the MCP tool surface.

    Unset  -> read/plan tools only (the mutating tool is opt-in).
    "all"  -> every tool this server implements.
    A list -> exactly those tools; an unknown name is a configuration error, not
              a silently ignored entry.
    """
    if raw is None or not raw.strip():
        return frozenset(DEFAULT_TOOLS)

    value = raw.strip()
    if value.lower() in {"all", "*"}:
        return frozenset(ALL_TOOLS)

    requested = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(ALL_TOOLS))
    if unknown:
        raise ConfigurationError(
            "ENTERPRISE_MCP_ENABLED_TOOLS names unknown tools: "
            f"{', '.join(unknown)}. Known tools: {', '.join(ALL_TOOLS)}"
        )
    if not requested:
        raise ConfigurationError("ENTERPRISE_MCP_ENABLED_TOOLS resolved to an empty tool surface")
    return frozenset(requested)


def enabled_tools_from_env() -> frozenset[str]:
    return resolve_enabled_tools(os.environ.get("ENTERPRISE_MCP_ENABLED_TOOLS"))


def load_settings() -> McpSettings:
    """Load settings, failing closed when no credential was supplied.

    There is deliberately no fallback token value. A hardcoded default made the
    server appear to work while the operator's credentials were being silently
    dropped by the MCP stdio environment allowlist.
    """
    token = os.environ.get("ENTERPRISE_API_TOKEN", "").strip()
    if not token:
        raise ConfigurationError(
            "ENTERPRISE_API_TOKEN is not set in the MCP server process environment. "
            "The MCP stdio transport forwards only an allowlisted set of variables, so "
            "the client must pass this explicitly (env= on the stdio transport, or the "
            "env: block of the Hermes MCP server config)."
        )

    write_token = os.environ.get("ENTERPRISE_API_WRITE_TOKEN", "").strip() or None

    return McpSettings(
        api_url=os.environ.get("ENTERPRISE_API_URL", "http://127.0.0.1:8080").rstrip("/"),
        api_token=token,
        api_write_token=write_token,
        timeout_seconds=float(os.environ.get("ENTERPRISE_API_TIMEOUT_SECONDS", "10")),
        inject_failure=os.environ.get("ENTERPRISE_INJECT_FAILURE", "").strip() or None,
    )
