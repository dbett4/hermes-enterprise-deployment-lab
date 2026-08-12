from __future__ import annotations

import os
import subprocess
import sys
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


def test_demo_normalizes_relative_python_before_changing_directories() -> None:
    script = (REPO_ROOT / "scripts" / "demo.sh").read_text()
    normalization = 'PYTHON_BIN="$(cd "$(dirname "$PYTHON_BIN")" && pwd)/$(basename "$PYTHON_BIN")"'
    api_chdir = 'cd "${ROOT_DIR}/enterprise-api"'
    assert normalization in script
    assert script.index(normalization) < script.index(api_chdir)
    assert 'realpath "$PYTHON_BIN"' not in script


def test_demo_fails_closed_when_an_explicit_api_url_is_unhealthy(tmp_path: Path) -> None:
    """An external proof target must never fall back to an unproven local API."""
    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "demo.sh")],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHON_BIN": sys.executable,
            "ENTERPRISE_API_URL": "http://127.0.0.1:1",
            "ENTERPRISE_API_TOKEN": "fixture-read",
            "ENTERPRISE_API_WRITE_TOKEN": "fixture-write",
            "AUDIT_DIR": str(tmp_path / "audit"),
            "RECEIPT_DIR": str(tmp_path / "receipts"),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "explicit ENTERPRISE_API_URL is not healthy" in result.stderr
    assert not (tmp_path / "receipts" / "demo-receipt.json").exists()


def test_demo_rejects_a_non_loopback_explicit_api_url(tmp_path: Path) -> None:
    """Fixture credentials must never be sent to an ambient external endpoint."""
    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "demo.sh")],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHON_BIN": sys.executable,
            "ENTERPRISE_API_URL": "http://0.0.0.0:1",
            "ENTERPRISE_API_TOKEN": "fixture-read",
            "ENTERPRISE_API_WRITE_TOKEN": "fixture-write",
            "AUDIT_DIR": str(tmp_path / "audit"),
            "RECEIPT_DIR": str(tmp_path / "receipts"),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "explicit ENTERPRISE_API_URL must be loopback" in result.stderr
    assert not (tmp_path / "receipts" / "demo-receipt.json").exists()


def test_container_proof_uses_fixture_tokens_for_live_requests() -> None:
    """Keep receipt redaction out of the HTTP request path.

    A literal ``Bearer ***`` still looks redacted in review, but it makes every
    authenticated container-proof request fail with 403 before the restart and
    replay assertions can run.
    """
    script = (REPO_ROOT / "scripts" / "container-proof.sh").read_text()
    assert "Authorization: Bearer ***" not in script
    assert script.count("Authorization: Bearer ${READ_TOKEN}") == 5
    assert script.count("Authorization: Bearer ${WRITE_TOKEN}") == 4
    assert "Authorization: Bearer invalid-container-proof-token" in script
