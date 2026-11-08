import os
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]


def _import_config(**env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ENTERPRISE_API_")}
    return subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(c.settings.api_token)"],
        cwd=API_DIR,
        env={**clean, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_service_refuses_to_start_without_tokens() -> None:
    for env in ({}, {"ENTERPRISE_API_TOKEN": "t-read"}, {"ENTERPRISE_API_WRITE_TOKEN": "t-write"},
                {"ENTERPRISE_API_TOKEN": "", "ENTERPRISE_API_WRITE_TOKEN": "t-write"}):
        result = _import_config(**env)
        assert result.returncode != 0, f"started with {sorted(env)}"
        assert "lab-read-token" not in result.stdout


def test_service_uses_the_configured_tokens() -> None:
    result = _import_config(ENTERPRISE_API_TOKEN="t-read", ENTERPRISE_API_WRITE_TOKEN="t-write")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "t-read"
