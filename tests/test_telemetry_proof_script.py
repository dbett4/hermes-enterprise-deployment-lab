from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "telemetry-proof.sh"
PINS = ROOT / "scripts" / "prometheus_asset_pins.py"
CANONICAL_PROOF = ROOT / "scripts" / "proof.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GITIGNORE = ROOT / ".gitignore"


def test_telemetry_proof_is_pinned_fail_closed_and_receipted() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "scripts/telemetry-proof.sh must be executable"

    script = SCRIPT.read_text(encoding="utf-8")
    pins = PINS.read_text(encoding="utf-8")
    required_script_fragments = (
        'PROMETHEUS_VERSION="3.13.2"',
        'prometheus-${PROMETHEUS_VERSION}.linux-${ASSET_ARCH}.tar.gz',
        'sha256sums.txt',
        "prometheus_asset_pins.py",
        "verify",
        'mktemp -d "${TMPDIR:-/tmp}/hermes-telemetry-proof.XXXXXX"',
        "trap cleanup EXIT",
        '"$PROMTOOL_BIN" check config --syntax-only observability/prometheus.yml',
        '"$PROMTOOL_BIN" check rules observability/alerts.yml',
        '"$PROMTOOL_BIN" test rules observability/alerts.test.yml',
        '"$PROMTOOL_BIN" check metrics',
        'app.main:app --host 127.0.0.1',
        '--web.listen-address="127.0.0.1:${PROMETHEUS_PORT}"',
        '/api/v1/targets',
        '/api/v1/rules',
        'enterprise_api_http_requests_total',
        'enterprise_api_action_outcomes_total',
        'telemetry-proof-conflict',
        'test "$CONFLICT_HTTP" = "409"',
        '"conflict", "postcommit_error"',
        'postcommit_error',
        '"rule_tests": {',
        '"result": "pass",',
        '"positive_alerts": expected_alerts,',
        '"idle_latency_negative_control": True,',
        'TELEMETRY_PROOF_PASS',
        'repository-pinned',
        'upstream-manifest-checked',
    )
    for fragment in required_script_fragments:
        assert fragment in script

    assert "0e8c4d46101bd025ea8265e377d2caabc57f488fc1be1c367f37db69ea41be6f" in pins
    assert "7cecb17a6f41d59814e1a0581a1f81f79051ad5973d1ecf39e23a9f747d6572a" in pins
    assert "assert_three_way_equality" in pins
    assert "actual == repository_pin == upstream_manifest" in pins

    assert "kill \"$API_PID\"" in script
    assert "kill \"$PROMETHEUS_PID\"" in script
    assert "receipt.json" in script
    assert "lab-read-token" not in script
    assert "lab-write-token" not in script


def test_prometheus_pin_table_and_three_way_gate() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("prometheus_asset_pins", PINS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.asset_arch_from_uname("x86_64") == "amd64"
    assert module.asset_arch_from_uname("aarch64") == "arm64"
    assert module.asset_arch_from_uname("arm64") == "arm64"
    with pytest.raises(ValueError, match="unsupported"):
        module.asset_arch_from_uname("ppc64le")

    amd64 = module.repository_pin_for_arch("amd64")
    arm64 = module.repository_pin_for_arch("arm64")
    assert amd64 == "0e8c4d46101bd025ea8265e377d2caabc57f488fc1be1c367f37db69ea41be6f"
    assert arm64 == "7cecb17a6f41d59814e1a0581a1f81f79051ad5973d1ecf39e23a9f747d6572a"
    assert amd64 != arm64

    with pytest.raises(SystemExit, match="digest mismatch"):
        module.assert_three_way_equality(
            actual=amd64,
            repository_pin="0" * 64,
            upstream_manifest=amd64,
        )
    with pytest.raises(SystemExit, match="digest mismatch"):
        module.assert_three_way_equality(
            actual=amd64,
            repository_pin=amd64,
            upstream_manifest="1" * 64,
        )
    module.assert_three_way_equality(
        actual=amd64,
        repository_pin=amd64,
        upstream_manifest=amd64,
    )


def test_prometheus_verify_rejects_wrong_repository_pin(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("prometheus_asset_pins", PINS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    asset = tmp_path / "prometheus-3.13.2.linux-amd64.tar.gz"
    payload = b"not-a-real-prometheus-archive"
    asset.write_bytes(payload)
    actual = hashlib.sha256(payload).hexdigest()
    manifest = tmp_path / "sha256sums.txt"
    manifest.write_text(
        f"{actual}  prometheus-3.13.2.linux-amd64.tar.gz\n",
        encoding="utf-8",
    )

    # Upstream matches the file, but the repository pin does not — fail closed.
    with pytest.raises(SystemExit, match="digest mismatch"):
        module.verify_asset_digests(
            asset_path=asset,
            checksum_manifest_path=manifest,
            asset_name="prometheus-3.13.2.linux-amd64.tar.gz",
            arch="amd64",
        )

def test_telemetry_proof_does_not_inherit_generic_api_credentials() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'READ_TOKEN="${TELEMETRY_PROOF_READ_TOKEN:-' in script
    assert 'WRITE_TOKEN="${TELEMETRY_PROOF_WRITE_TOKEN:-' in script
    assert 'READ_TOKEN="${ENTERPRISE_API_TOKEN:-' not in script
    assert 'WRITE_TOKEN="${ENTERPRISE_API_WRITE_TOKEN:-' not in script


def test_canonical_proof_and_ci_execute_and_retain_telemetry_evidence() -> None:
    proof = CANONICAL_PROOF.read_text(encoding="utf-8")
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    gitignore = GITIGNORE.read_text(encoding="utf-8").splitlines()

    assert 'TELEMETRY_PROOF_DIR="${ROOT_DIR}/.telemetry-proof"' in proof
    assert 'PYTHON_BIN="$PYTHON_BIN" ./scripts/telemetry-proof.sh' in proof
    assert "telemetry=pass" in proof

    assert "Run live native telemetry proof" in workflow
    assert "PYTHON_BIN=$(which python) ./scripts/telemetry-proof.sh" in workflow
    assert ".telemetry-proof/" in workflow
    assert ".telemetry-proof/" in gitignore


def test_ci_uploads_hidden_proof_artifacts_fail_closed() -> None:
    """Pinned upload-artifact v4 excludes dot-directories unless explicitly enabled."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    upload_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "actions/upload-artifact@" in str(step.get("uses", ""))
    ]

    assert upload_steps, "CI must retain proof artifacts"
    for step in upload_steps:
        upload = step.get("with", {})
        paths = str(upload.get("path", "")).splitlines()
        assert any(path.strip().startswith(".") for path in paths), step
        assert upload.get("include-hidden-files") is True, step
        assert upload.get("if-no-files-found") == "error", step


def test_container_ci_preserves_engine_preflight_failure_evidence() -> None:
    """A daemon failure before container-proof still needs an uploaded primary cause."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["container-proof"]["steps"]
    preflight = next(step for step in steps if step.get("name") == "Confirm docker compose engine is usable")
    upload = next(step for step in steps if step.get("name") == "Upload container-proof artifacts")

    run = str(preflight.get("run", ""))
    assert "mkdir -p .container-proof" in run
    assert "engine-preflight.log" in run
    assert "pipefail" in run
    assert "docker compose version" in run
    assert "docker info" in run

    upload_paths = str(upload.get("with", {}).get("path", ""))
    assert ".container-proof/engine-preflight.log" in upload_paths
