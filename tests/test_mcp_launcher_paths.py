from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_mcp_smoke_uses_repo_relative_launcher() -> None:
    script = (REPO_ROOT / "scripts" / "mcp-smoke.sh").read_text()
    assert 'MCP_LAUNCHER="./scripts/run-enterprise-mcp.sh"' in script
    assert "SERVER_SPEC=\"enterprise-mcp/enterprise_mcp/server.py:mcp\"" in script


def test_emit_hermes_config_quotes_spaced_repo_root() -> None:
    spaced_root = REPO_ROOT / "fixtures" / "Career Materials" / "lab"
    output = subprocess.check_output(
        [str(REPO_ROOT / "scripts" / "emit-hermes-mcp-config.sh"), str(spaced_root)],
        text=True,
    )
    launcher = f"{spaced_root}/scripts/run-enterprise-mcp.sh"
    assert f'command: "{launcher}"' in output
    assert f'PYTHONPATH: "{spaced_root}/enterprise-mcp:{spaced_root}/workflow-runner"' in output
