#!/usr/bin/env bash
# Native telemetry proof: validate Prometheus config/rules, run the API and a
# repository-pinned plus upstream-manifest-checked Prometheus binary on
# temporary localhost ports, scrape real traffic, query metrics/rules/targets,
# and write a credential-free receipt.
#
# This does not start Compose and is not container-runtime evidence. The separate
# container proof remains the authority for image build, volume, restart/replay,
# and Compose-network claims.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
PROMETHEUS_VERSION="3.13.2"
PROOF_DIR="${TELEMETRY_PROOF_DIR:-${ROOT_DIR}/.telemetry-proof}"
RECEIPT_PATH="${PROOF_DIR}/receipt.json"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-telemetry-proof.XXXXXX")"
API_PID=""
PROMETHEUS_PID=""

cleanup() {
  if [[ -n "$PROMETHEUS_PID" ]] && kill -0 "$PROMETHEUS_PID" 2>/dev/null; then
    kill "$PROMETHEUS_PID" 2>/dev/null || true
    wait "$PROMETHEUS_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  case "$WORK_DIR" in
    /tmp/*) rm -rf -- "$WORK_DIR" ;;
    *) echo "Refusing to remove unexpected telemetry work directory: $WORK_DIR" >&2 ;;
  esac
}
trap cleanup EXIT

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "TELEMETRY_PROOF_FAIL: create .venv and install requirements-dev.txt first" >&2
  exit 2
fi
for command in curl sha256sum tar; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "TELEMETRY_PROOF_FAIL: missing required command: $command" >&2
    exit 2
  fi
done

ASSET_ARCH="$("$PYTHON_BIN" "${ROOT_DIR}/scripts/prometheus_asset_pins.py" arch "$(uname -m)")"
ASSET="prometheus-${PROMETHEUS_VERSION}.linux-${ASSET_ARCH}.tar.gz"
RELEASE_URL="https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}"
ASSET_PATH="${WORK_DIR}/${ASSET}"
CHECKSUM_PATH="${WORK_DIR}/sha256sums.txt"
REPOSITORY_PIN="$("$PYTHON_BIN" "${ROOT_DIR}/scripts/prometheus_asset_pins.py" pin "$ASSET_ARCH")"

mkdir -p "$PROOF_DIR"
rm -f "$RECEIPT_PATH" "$PROOF_DIR/api.log" "$PROOF_DIR/prometheus.log"

curl --fail --silent --show-error --location --retry 3 \
  "${RELEASE_URL}/sha256sums.txt" -o "$CHECKSUM_PATH"
curl --fail --silent --show-error --location --retry 3 \
  "${RELEASE_URL}/${ASSET}" -o "$ASSET_PATH"

# Require actual == repository_pin == upstream_manifest before extraction.
ACTUAL_SHA256="$("$PYTHON_BIN" "${ROOT_DIR}/scripts/prometheus_asset_pins.py" verify \
  "$ASSET_PATH" "$CHECKSUM_PATH" "$ASSET" "$ASSET_ARCH")"
test "$ACTUAL_SHA256" = "$REPOSITORY_PIN"

tar -xzf "$ASSET_PATH" -C "$WORK_DIR"
PROMETHEUS_HOME="${WORK_DIR}/prometheus-${PROMETHEUS_VERSION}.linux-${ASSET_ARCH}"
PROMETHEUS_BIN="${PROMETHEUS_HOME}/prometheus"
PROMTOOL_BIN="${PROMETHEUS_HOME}/promtool"
test -x "$PROMETHEUS_BIN"
test -x "$PROMTOOL_BIN"

"$PROMTOOL_BIN" check config --syntax-only observability/prometheus.yml
"$PROMTOOL_BIN" check rules observability/alerts.yml
"$PROMTOOL_BIN" test rules observability/alerts.test.yml

free_port() {
  "$PYTHON_BIN" - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

API_PORT="$(free_port)"
PROMETHEUS_PORT="$(free_port)"
while [[ "$PROMETHEUS_PORT" == "$API_PORT" ]]; do
  PROMETHEUS_PORT="$(free_port)"
done
API_URL="http://127.0.0.1:${API_PORT}"
PROMETHEUS_URL="http://127.0.0.1:${PROMETHEUS_PORT}"
READ_TOKEN="${TELEMETRY_PROOF_READ_TOKEN:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(24))')}"
WRITE_TOKEN="${TELEMETRY_PROOF_WRITE_TOKEN:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(24))')}"

(
  cd "${ROOT_DIR}/enterprise-api"
  exec env \
    PYTHONPATH="${ROOT_DIR}/enterprise-api" \
    ENTERPRISE_API_TOKEN="$READ_TOKEN" \
    ENTERPRISE_API_WRITE_TOKEN="$WRITE_TOKEN" \
    ACTION_STORE_PATH="${WORK_DIR}/actions.json" \
    "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" \
      --log-level warning >"${PROOF_DIR}/api.log" 2>&1
) &
API_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "${API_URL}/readyz" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "TELEMETRY_PROOF_FAIL: enterprise-api exited during startup" >&2
    exit 1
  fi
  sleep 0.25
done
curl -fsS "${API_URL}/readyz" >/dev/null
curl -fsS "${API_URL}/metrics" | "$PROMTOOL_BIN" check metrics

NATIVE_CONFIG="${WORK_DIR}/prometheus-native.yml"
"$PYTHON_BIN" - \
  "$ROOT_DIR/observability/prometheus.yml" \
  "$ROOT_DIR/observability/alerts.yml" \
  "$NATIVE_CONFIG" \
  "$API_PORT" <<'PY'
from pathlib import Path
import sys
import yaml

source, rules, output, api_port = sys.argv[1:]
config = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
config["rule_files"] = [str(Path(rules).resolve())]
config["scrape_configs"][0]["static_configs"] = [
    {"targets": [f"127.0.0.1:{api_port}"]},
]
Path(output).write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY
"$PROMTOOL_BIN" check config "$NATIVE_CONFIG"

mkdir -p "${WORK_DIR}/prometheus-data"
"$PROMETHEUS_BIN" \
  --config.file="$NATIVE_CONFIG" \
  --storage.tsdb.path="${WORK_DIR}/prometheus-data" \
  --web.listen-address="127.0.0.1:${PROMETHEUS_PORT}" \
  --log.level=warn >"${PROOF_DIR}/prometheus.log" 2>&1 &
PROMETHEUS_PID=$!

for _ in $(seq 1 60); do
  if "$PROMTOOL_BIN" check ready --url="$PROMETHEUS_URL" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$PROMETHEUS_PID" 2>/dev/null; then
    echo "TELEMETRY_PROOF_FAIL: Prometheus exited during startup" >&2
    exit 1
  fi
  sleep 0.25
done
"$PROMTOOL_BIN" check ready --url="$PROMETHEUS_URL" >/dev/null

INCIDENT_ID="INC-2026-0042"
ACTION_PATH="/v1/incidents/${INCIDENT_ID}/actions"
ACTION_BODY='{"action_id":"RB-PAY-GATEWAY-01-S2","note":"native telemetry proof"}'

curl -fsS \
  -H "Authorization: Bearer ${READ_TOKEN}" \
  "${API_URL}/v1/incidents/${INCIDENT_ID}" >/dev/null

CREATED_HTTP="$(curl -sS -o "${WORK_DIR}/created.json" -w '%{http_code}' \
  -X POST "${API_URL}${ACTION_PATH}" \
  -H "Authorization: Bearer ${WRITE_TOKEN}" \
  -H "Idempotency-Key: telemetry-proof-created" \
  -H "Content-Type: application/json" \
  -d "$ACTION_BODY")"
test "$CREATED_HTTP" = "201"

REPLAY_HTTP="$(curl -sS -o "${WORK_DIR}/replayed.json" -w '%{http_code}' \
  -X POST "${API_URL}${ACTION_PATH}" \
  -H "Authorization: Bearer ${WRITE_TOKEN}" \
  -H "Idempotency-Key: telemetry-proof-created" \
  -H "Content-Type: application/json" \
  -d "$ACTION_BODY")"
test "$REPLAY_HTTP" = "200"

POSTCOMMIT_HTTP="$(curl -sS -o "${WORK_DIR}/postcommit.json" -w '%{http_code}' \
  -X POST "${API_URL}${ACTION_PATH}?inject=error_after_commit" \
  -H "Authorization: Bearer ${WRITE_TOKEN}" \
  -H "Idempotency-Key: telemetry-proof-postcommit" \
  -H "Content-Type: application/json" \
  -d "$ACTION_BODY")"
test "$POSTCOMMIT_HTTP" = "500"

TARGETS_JSON="${WORK_DIR}/targets.json"
RULES_JSON="${WORK_DIR}/rules.json"
REQUEST_METRICS_JSON="${WORK_DIR}/request-metrics.json"
ACTION_METRICS_JSON="${WORK_DIR}/action-metrics.json"

metrics_ready() {
  curl -fsS --get \
    --data-urlencode 'query=enterprise_api_action_outcomes_total{outcome="postcommit_error"}' \
    "${PROMETHEUS_URL}/api/v1/query" -o "$ACTION_METRICS_JSON" || return 1
  "$PYTHON_BIN" - "$ACTION_METRICS_JSON" <<'PY'
from pathlib import Path
import json
import sys

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
results = body.get("data", {}).get("result", [])
raise SystemExit(0 if results and float(results[0]["value"][1]) >= 1 else 1)
PY
}

for _ in $(seq 1 80); do
  if metrics_ready; then
    break
  fi
  sleep 0.25
done
metrics_ready

curl -fsS "${PROMETHEUS_URL}/api/v1/targets" -o "$TARGETS_JSON"
curl -fsS "${PROMETHEUS_URL}/api/v1/rules" -o "$RULES_JSON"
curl -fsS --get \
  --data-urlencode 'query=enterprise_api_http_requests_total' \
  "${PROMETHEUS_URL}/api/v1/query" -o "$REQUEST_METRICS_JSON"
curl -fsS --get \
  --data-urlencode 'query=enterprise_api_action_outcomes_total' \
  "${PROMETHEUS_URL}/api/v1/query" -o "$ACTION_METRICS_JSON"

"$PYTHON_BIN" - \
  "$TARGETS_JSON" \
  "$RULES_JSON" \
  "$REQUEST_METRICS_JSON" \
  "$ACTION_METRICS_JSON" \
  "$ROOT_DIR/observability/alerts.yml" \
  "$ROOT_DIR/observability/alerts.test.yml" \
  "$RECEIPT_PATH" \
  "$PROMETHEUS_VERSION" \
  "$ACTUAL_SHA256" <<'PY'
from pathlib import Path
import json
import sys
import yaml

(
    targets_path,
    rules_path,
    request_metrics_path,
    action_metrics_path,
    alert_rules_path,
    alert_tests_path,
    receipt_path,
    prometheus_version,
    binary_sha256,
) = sys.argv[1:]

def load(path: str) -> dict:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    assert body.get("status") == "success", body
    return body

targets = load(targets_path)
active_targets = targets["data"]["activeTargets"]
assert len(active_targets) == 1, active_targets
assert active_targets[0]["health"] == "up", active_targets[0]

rules = load(rules_path)
loaded_alerts = sorted(
    rule["name"]
    for group in rules["data"]["groups"]
    for rule in group["rules"]
    if rule.get("type") == "alerting"
)
expected_alerts = sorted(
    [
        "EnterpriseApiAvailabilityFastBurn",
        "EnterpriseApiAvailabilitySlowBurn",
        "EnterpriseApiLatencyFastBurn",
        "EnterpriseApiLatencySlowBurn",
        "EnterpriseApiPostCommitFailure",
    ]
)
assert loaded_alerts == expected_alerts, loaded_alerts

alert_rules = yaml.safe_load(Path(alert_rules_path).read_text(encoding="utf-8"))
declared_alerts = sorted(
    rule["alert"]
    for group in alert_rules["groups"]
    for rule in group["rules"]
    if "alert" in rule
)
assert declared_alerts == expected_alerts, declared_alerts

alert_tests = yaml.safe_load(Path(alert_tests_path).read_text(encoding="utf-8"))
positive_alerts = sorted(
    case["alertname"]
    for test in alert_tests["tests"]
    for case in test.get("alert_rule_test", [])
    if case.get("exp_alerts")
)
assert positive_alerts == expected_alerts, positive_alerts
idle_latency_alerts = {
    case["alertname"]
    for test in alert_tests["tests"]
    if test.get("name") == "idle existing series do not burn the latency budget"
    for case in test.get("alert_rule_test", [])
    if case.get("exp_alerts") == []
}
assert idle_latency_alerts == {
    "EnterpriseApiLatencyFastBurn",
    "EnterpriseApiLatencySlowBurn",
}, idle_latency_alerts

requests = load(request_metrics_path)["data"]["result"]
assert requests, requests
serialized_requests = json.dumps(requests, sort_keys=True)
assert "INC-2026-0042" not in serialized_requests, serialized_requests
assert any(
    series["metric"].get("route") == "/v1/incidents/{incident_id}"
    for series in requests
), requests

actions = load(action_metrics_path)["data"]["result"]
outcomes = {
    series["metric"].get("outcome"): float(series["value"][1])
    for series in actions
}
for outcome in ("created", "replayed", "postcommit_error"):
    assert outcomes.get(outcome, 0) >= 1, outcomes

receipt = {
    "proof": "native-telemetry-proof",
    "result": "pass",
    "prometheus": {
        "version": prometheus_version,
        "release_asset_sha256": binary_sha256,
        "integrity": "repository-pinned+upstream-manifest-checked",
        "target_health": active_targets[0]["health"],
        "loaded_alerts": loaded_alerts,
    },
    "rule_tests": {
        "result": "pass",
        "positive_alerts": expected_alerts,
        "idle_latency_negative_control": True,
    },
    "metrics": {
        "request_route_template_observed": "/v1/incidents/{incident_id}",
        "action_outcomes": outcomes,
    },
    "limits": [
        "Native localhost processes only; no Compose or container runtime was used.",
        "Synthetic traffic and fixture identity only; not production telemetry.",
        "Alerts were loaded and queried, not delivered to an external pager.",
        "Prometheus archive integrity is repository-pinned and upstream-manifest-checked.",
    ],
}
Path(receipt_path).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
PY

printf 'TELEMETRY_PROOF_PASS prometheus=%s target=up alerts=5 outcomes=created,replayed,postcommit_error receipt=%s\n' \
  "$PROMETHEUS_VERSION" "$RECEIPT_PATH"
