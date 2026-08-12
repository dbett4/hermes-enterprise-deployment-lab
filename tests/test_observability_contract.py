from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "compose.yaml"
PROMETHEUS_CONFIG = ROOT / "observability" / "prometheus.yml"
ALERT_RULES = ROOT / "observability" / "alerts.yml"
ALERT_TESTS = ROOT / "observability" / "alerts.test.yml"
RUNBOOK = ROOT / "docs" / "runbook.md"
CONTAINER_PROOF = ROOT / "scripts" / "container-proof.sh"
PROMETHEUS_IMAGE = (
    "docker.io/prom/prometheus:v3.13.2@"
    "sha256:508729e0e2d18e11fd742a5a5ca70e557b940a93948c3c95fd0123a6fd538b69"
)


def test_compose_runs_pinned_prometheus_for_api_metrics() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    api_service = compose["services"]["enterprise-api"]
    service = compose["services"]["prometheus"]

    assert api_service["ports"] == ["127.0.0.1:${ENTERPRISE_API_PORT:-8080}:8080"]
    assert service["image"] == PROMETHEUS_IMAGE
    assert service["depends_on"] == {
        "enterprise-api": {"condition": "service_healthy"},
    }
    assert service["ports"] == ["127.0.0.1:${PROMETHEUS_PORT:-9090}:9090"]
    assert service["volumes"] == [
        "./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro",
        "./observability/alerts.yml:/etc/prometheus/alerts.yml:ro",
        "./observability/alerts.test.yml:/etc/prometheus/alerts.test.yml:ro",
        "prometheus-data:/prometheus",
    ]
    assert service["command"] == [
        "--config.file=/etc/prometheus/prometheus.yml",
        "--storage.tsdb.path=/prometheus",
    ]
    assert service["healthcheck"]["test"] == [
        "CMD",
        "/bin/promtool",
        "check",
        "ready",
        "--url=http://127.0.0.1:9090",
    ]
    assert "prometheus-data" in compose["volumes"]


def test_prometheus_scrape_and_slo_alert_contract() -> None:
    config = yaml.safe_load(PROMETHEUS_CONFIG.read_text(encoding="utf-8"))
    assert config["global"] == {
        "scrape_interval": "5s",
        "evaluation_interval": "15s",
    }
    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]

    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    api_job = jobs["enterprise-api"]
    assert api_job["metrics_path"] == "/metrics"
    assert api_job["static_configs"] == [{"targets": ["enterprise-api:8080"]}]

    rule_config = yaml.safe_load(ALERT_RULES.read_text(encoding="utf-8"))
    alerts = {
        rule["alert"]: rule
        for group in rule_config["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }
    assert set(alerts) == {
        "EnterpriseApiAvailabilityFastBurn",
        "EnterpriseApiAvailabilitySlowBurn",
        "EnterpriseApiLatencyFastBurn",
        "EnterpriseApiLatencySlowBurn",
        "EnterpriseApiPostCommitFailure",
    }

    expectations = {
        "EnterpriseApiAvailabilityFastBurn": (
            "enterprise_api_http_requests_total",
            "[1h]",
            "[5m]",
            "14.4 * 0.01",
        ),
        "EnterpriseApiAvailabilitySlowBurn": (
            "enterprise_api_http_requests_total",
            "[6h]",
            "[30m]",
            "6 * 0.01",
        ),
        "EnterpriseApiLatencyFastBurn": (
            "enterprise_api_http_request_duration_seconds_bucket",
            "[1h]",
            "[5m]",
            "14.4 * 0.05",
        ),
        "EnterpriseApiLatencySlowBurn": (
            "enterprise_api_http_request_duration_seconds_bucket",
            "[6h]",
            "[30m]",
            "6 * 0.05",
        ),
        "EnterpriseApiPostCommitFailure": (
            "enterprise_api_action_outcomes_total",
            "[10m]",
        ),
    }
    for name, fragments in expectations.items():
        expression = alerts[name]["expr"]
        for fragment in fragments:
            assert fragment in expression
        assert alerts[name]["labels"]["severity"] in {"page", "ticket"}
        assert alerts[name]["annotations"]["runbook_url"].endswith("docs/runbook.md#telemetry-alerts")


def test_every_alert_has_a_positive_firing_fixture() -> None:
    rule_config = yaml.safe_load(ALERT_RULES.read_text(encoding="utf-8"))
    declared_alerts = {
        rule["alert"]
        for group in rule_config["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }

    test_config = yaml.safe_load(ALERT_TESTS.read_text(encoding="utf-8"))
    positively_tested_alerts = {
        case["alertname"]
        for test in test_config["tests"]
        for case in test.get("alert_rule_test", [])
        if case.get("exp_alerts")
    }

    assert positively_tested_alerts == declared_alerts


def test_alert_runbook_links_have_a_local_anchor() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "\n## Telemetry alerts\n" in runbook


def test_container_proof_exercises_live_prometheus_and_records_results() -> None:
    script = CONTAINER_PROOF.read_text(encoding="utf-8")

    required_fragments = (
        'export COMPOSE_PROJECT_NAME="${CONTAINER_PROOF_PROJECT_NAME:-hermes-lab-proof-$$}"',
        'export ENTERPRISE_API_PORT="${ENTERPRISE_API_PORT:-$(free_port)}"',
        "export PROMETHEUS_PORT",
        'API_URL="http://127.0.0.1:${ENTERPRISE_API_PORT}"',
        "compose up --build -d --wait enterprise-api prometheus",
        "/bin/promtool check config /etc/prometheus/prometheus.yml",
        "/bin/promtool check rules /etc/prometheus/alerts.yml",
        "/bin/promtool test rules alerts.test.yml",
        "/api/v1/targets",
        "/api/v1/rules",
        "enterprise_api_http_requests_total",
        'outcome="postcommit_error"',
        'outcome="replayed"',
        'outcome="created"',
        '"telemetry": {',
        '"target_up":',
        '"rules_loaded":',
    )
    for fragment in required_fragments:
        assert fragment in script

    assert 'ENTERPRISE_API_URL:-' not in script


def test_container_proof_waits_for_prometheus_target_to_be_up() -> None:
    """Compose health can precede the first successful Prometheus scrape."""
    script = CONTAINER_PROOF.read_text(encoding="utf-8")

    assert "wait_prometheus_target_up()" in script
    assert 'TARGET_UP="$(wait_prometheus_target_up)"' in script
    function = script[
        script.index("wait_prometheus_target_up()") : script.index("\n}\n", script.index("wait_prometheus_target_up()")) + 2
    ]
    assert "for i in $(seq 1 80)" in function
    assert "/api/v1/targets" in function
    assert "sleep 0.25" in function


def test_container_proof_does_not_create_a_persistent_dotenv() -> None:
    script = CONTAINER_PROOF.read_text(encoding="utf-8")

    assert "cp .env.example .env" not in script


def test_container_proof_requires_successful_teardown_before_receipt_and_pass() -> None:
    script = CONTAINER_PROOF.read_text(encoding="utf-8")

    teardown = 'compose down -v --remove-orphans\nTEARDOWN_COMPLETE=1'
    receipt = 'receipt = {'
    marker = 'echo "CONTAINER_PROOF_PASS'
    assert teardown in script
    assert '"teardown": {"result": "pass", "volumes_removed": True},' in script
    assert script.index(teardown) < script.index(receipt) < script.index(marker)


def test_container_proof_uses_only_proof_scoped_fixture_credentials() -> None:
    script = CONTAINER_PROOF.read_text(encoding="utf-8")

    assert 'READ_TOKEN="${CONTAINER_PROOF_READ_TOKEN:-lab-read-token}"' in script
    assert 'WRITE_TOKEN="${CONTAINER_PROOF_WRITE_TOKEN:-lab-write-token}"' in script
    assert 'READ_TOKEN="${ENTERPRISE_API_TOKEN:-' not in script
    assert 'WRITE_TOKEN="${ENTERPRISE_API_WRITE_TOKEN:-' not in script


def test_container_proof_redaction_uses_literal_token_replace() -> None:
    """Proof-scoped token overrides may include sed metacharacters."""
    script = CONTAINER_PROOF.read_text(encoding="utf-8")

    assert 's/${READ_TOKEN}/' not in script
    assert 's/${WRITE_TOKEN}/' not in script
    assert "text.replace(old, new)" in script


def test_container_proof_redact_function_preserves_pipeline_input_and_masks_tokens() -> None:
    """Exercise the production shell function, including its stdin pipeline."""
    read_token = r"lab+read/token.[x*]"
    write_token = r"lab$write|token"
    source = CONTAINER_PROOF.read_text(encoding="utf-8")
    function = source[source.index("redact() {") : source.index("\n}\n\nwait_ready()") + 2]
    text = (
        f"Authorization: Bearer {read_token}\n"
        f"write={write_token}\n"
        "Authorization: Bearer other-token-value\n"
    )
    command = (
        "set -euo pipefail\n"
        f"READ_TOKEN={read_token!r}\n"
        f"WRITE_TOKEN={write_token!r}\n"
        f"{function}\n"
        "redact\n"
    )
    result = subprocess.run(
        ["bash", "-c", command],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert read_token not in result.stdout
    assert write_token not in result.stdout
    assert "other-token-value" not in result.stdout
    assert "<redacted-read-token>" in result.stdout
    assert "<redacted-write-token>" in result.stdout
    assert result.stdout.count("Authorization: <redacted>") == 2


def test_slo_doc_states_objectives_burn_math_and_trace_limits() -> None:
    slo = (ROOT / "docs" / "slo.md").read_text(encoding="utf-8")
    for fragment in (
        "99%",
        "95%",
        "500 ms",
        "14.4",
        "7.2 hours",
        "W3C",
        "traceparent",
        "approval.requested",
        "mutation.failed_resumable",
        "SimpleSpanProcessor",
        "No collector backend",
        "No external pager",
        "No production traffic",
        "customer data",
    ):
        assert fragment in slo


def test_trace_proof_script_covers_propagation_and_secret_allowlist() -> None:
    script = (ROOT / "scripts" / "trace-proof.sh").read_text(encoding="utf-8")
    assert "OTEL_TRACES_EXPORTER=otlp" in script
    assert "127.0.0.1" in script
    assert "SPAN_KIND_CLIENT" in script
    assert "SPAN_KIND_SERVER" in script
    assert "assert_parent_child_propagation" in script
    assert "parent_child_links" in script
    assert "mutation.failed_resumable" in script
    assert "lab-read-token" not in script
    assert "lab-write-token" not in script


def test_telemetry_proof_requires_repository_pins_and_three_way_gate() -> None:
    script = (ROOT / "scripts" / "telemetry-proof.sh").read_text(encoding="utf-8")
    pins = (ROOT / "scripts" / "prometheus_asset_pins.py").read_text(encoding="utf-8")
    assert "0e8c4d46101bd025ea8265e377d2caabc57f488fc1be1c367f37db69ea41be6f" in pins
    assert "7cecb17a6f41d59814e1a0581a1f81f79051ad5973d1ecf39e23a9f747d6572a" in pins
    assert "prometheus_asset_pins.py" in script
    assert "verify" in script
    assert "actual == repository_pin == upstream_manifest" in pins
    assert "repository-pinned" in script
    assert "upstream-manifest-checked" in script
