"""Scoped-permission proof at the MCP surface.

The allowlist is applied before registration, so an excluded tool is absent from
list_tools AND not callable. Both halves are asserted: "not listed" alone would
not prove it is unreachable.
"""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.client.transports.memory import FastMCPTransport

from enterprise_mcp.config import (
    ALL_TOOLS,
    DEFAULT_TOOLS,
    ConfigurationError,
    resolve_enabled_tools,
)
from enterprise_mcp.server import build_server


def test_default_surface_is_read_plan_only() -> None:
    assert resolve_enabled_tools(None) == frozenset(DEFAULT_TOOLS)
    assert "apply_incident_plan" not in resolve_enabled_tools("")


def test_all_keyword_enables_every_tool() -> None:
    assert resolve_enabled_tools("all") == frozenset(ALL_TOOLS)
    assert resolve_enabled_tools("*") == frozenset(ALL_TOOLS)


def test_explicit_subset_is_honoured() -> None:
    assert resolve_enabled_tools("check_enterprise_api") == frozenset({"check_enterprise_api"})
    assert resolve_enabled_tools(" get_incident_context , check_enterprise_api ") == frozenset(
        {"get_incident_context", "check_enterprise_api"}
    )


def test_unknown_tool_name_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        resolve_enabled_tools("check_enterprise_api,delete_everything")
    assert "delete_everything" in str(excinfo.value)


@pytest.mark.asyncio
async def test_excluded_tool_is_not_listed_and_not_callable() -> None:
    server = build_server({"check_enterprise_api"})
    async with Client(FastMCPTransport(server)) as client:
        names = sorted(tool.name for tool in await client.list_tools())
        assert names == ["check_enterprise_api"]
        with pytest.raises(Exception) as excinfo:
            await client.call_tool("apply_incident_plan", {"incident_id": "x", "action_id": "y"})
    assert "apply_incident_plan" in str(excinfo.value)


@pytest.mark.asyncio
async def test_write_enabled_surface_exposes_four_tools() -> None:
    server = build_server(frozenset(ALL_TOOLS))
    async with Client(FastMCPTransport(server)) as client:
        names = sorted(tool.name for tool in await client.list_tools())
    assert names == [
        "apply_incident_plan",
        "check_enterprise_api",
        "get_incident_context",
        "propose_incident_plan",
    ]
