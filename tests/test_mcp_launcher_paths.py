from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_smoke_does_not_use_the_env_dropping_fastmcp_call_path() -> None:
    """Regression guard for the credential-injection defect (ADR 004).

    `fastmcp call --command ...` cannot pass env to the spawned server, and MCP
    stdio forwards only an allowlist, so the token never arrived. The smoke must
    go through the protocol module that passes env explicitly.
    """
    script = (REPO_ROOT / "scripts" / "mcp-smoke.sh").read_text()
    assert "fastmcp call" not in script
    assert "enterprise_mcp.protocol_smoke" in script
    assert 'SERVER_SPEC="enterprise-mcp/enterprise_mcp/server.py:mcp"' in script


def test_launcher_fails_closed_without_a_token() -> None:
    script = (REPO_ROOT / "scripts" / "run-enterprise-mcp.sh").read_text()
    assert 'if [[ -z "${ENTERPRISE_API_TOKEN:-}" ]]' in script
    assert "exit 2" in script


def test_emit_hermes_config_quotes_spaced_repo_root() -> None:
    spaced_root = REPO_ROOT / "fixtures" / "Career Materials" / "lab"
    output = subprocess.check_output(
        [str(REPO_ROOT / "scripts" / "emit-hermes-mcp-config.sh"), str(spaced_root)],
        text=True,
    )
    launcher = f"{spaced_root}/scripts/run-enterprise-mcp.sh"
    assert f'command: "{launcher}"' in output
    assert f'PYTHONPATH: "{spaced_root}/enterprise-mcp:{spaced_root}/workflow-runner"' in output


def test_emit_hermes_config_propagates_the_server_side_allowlist() -> None:
    output = subprocess.check_output(
        [str(REPO_ROOT / "scripts" / "emit-hermes-mcp-config.sh"), str(REPO_ROOT), "all"],
        text=True,
    )
    assert 'ENTERPRISE_MCP_ENABLED_TOOLS: "all"' in output
    assert "        - apply_incident_plan" in output

    restricted = subprocess.check_output(
        [
            str(REPO_ROOT / "scripts" / "emit-hermes-mcp-config.sh"),
            str(REPO_ROOT),
            "check_enterprise_api",
        ],
        text=True,
    )
    assert 'ENTERPRISE_MCP_ENABLED_TOOLS: "check_enterprise_api"' in restricted
    assert "apply_incident_plan" not in restricted


def test_demo_script_exists_and_is_executable() -> None:
    demo = REPO_ROOT / "scripts" / "demo.sh"
    assert demo.exists()
    assert demo.stat().st_mode & 0o111, "scripts/demo.sh must be executable"
