#!/usr/bin/env bash
# Credential-free container proof: post-commit failure, API restart, replay,
# and the full demo arc against a Compose-backed enterprise-api.
#
# Host vs container:
# - enterprise-api (and its ACTION_STORE_PATH volume) run in Compose.
# - The restart/idempotency probe talks to that API over a proof-selected
#   loopback port.
# - scripts/demo.sh runs on the host (MCP stdio + operator command) but targets
#   ENTERPRISE_API_URL so side effects land in the containerized store.
# - A unique Compose project name prevents cleanup from touching another stack.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROOF_DIR="${CONTAINER_PROOF_DIR:-$ROOT_DIR/.container-proof}"
RECEIPT_PATH="$PROOF_DIR/receipt.json"
LOG_PATH="$PROOF_DIR/compose-logs.txt"
INCIDENT_ID="${DEFAULT_INCIDENT_ID:-INC-2026-0042}"
READ_TOKEN="${CONTAINER_PROOF_READ_TOKEN:-lab-read-token}"
WRITE_TOKEN="${CONTAINER_PROOF_WRITE_TOKEN:-lab-write-token}"
IDEM_KEY="${CONTAINER_PROOF_IDEM_KEY:-container-proof-idempotency-key}"
COMPOSE_CMD=()
TEARDOWN_COMPLETE=0

free_port() {
  python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

export COMPOSE_PROJECT_NAME="${CONTAINER_PROOF_PROJECT_NAME:-hermes-lab-proof-$$}"
export ENTERPRISE_API_PORT="${ENTERPRISE_API_PORT:-$(free_port)}"
export PROMETHEUS_PORT="${PROMETHEUS_PORT:-$(free_port)}"
while [[ "$PROMETHEUS_PORT" == "$ENTERPRISE_API_PORT" ]]; do
  PROMETHEUS_PORT="$(free_port)"
  export PROMETHEUS_PORT
done
export ENTERPRISE_API_TOKEN="$READ_TOKEN"
export ENTERPRISE_API_WRITE_TOKEN="$WRITE_TOKEN"
API_URL="http://127.0.0.1:${ENTERPRISE_API_PORT}"
PROMETHEUS_URL="http://127.0.0.1:${PROMETHEUS_PORT}"

select_compose() {
  # Require a working engine, not just a CLI plugin. Local hosts without
  # docker.sock access must fail closed instead of claiming runtime proof.
  if docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
    return 0
  fi
  if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
    if podman info >/dev/null 2>&1; then
      COMPOSE_CMD=(podman compose)
      return 0
    fi
  fi
  echo "CONTAINER_PROOF_FAIL: no usable docker/podman compose engine (CLI alone is not enough)" >&2
  exit 2
}

compose() {
  "${COMPOSE_CMD[@]}" --project-name "$COMPOSE_PROJECT_NAME" "$@"
}

cleanup() {
  if ((${#COMPOSE_CMD[@]})) && [[ "$TEARDOWN_COMPLETE" != "1" ]]; then
    compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

redact() {
  # Literal token replace (not sed interpolation) so proof-scoped fixture
  # overrides that contain sed metacharacters cannot break redaction. Use -c so
  # stdin remains the Compose log stream instead of carrying Python source.
  READ_TOKEN="$READ_TOKEN" WRITE_TOKEN="$WRITE_TOKEN" python3 -c '
import os
import re
import sys

text = sys.stdin.read()
for old, new in (
    (os.environ.get("READ_TOKEN", ""), "<redacted-read-token>"),
    (os.environ.get("WRITE_TOKEN", ""), "<redacted-write-token>"),
):
    if old:
        text = text.replace(old, new)
text = re.sub(r"(Bearer )[A-Za-z0-9._-]+", r"\1<redacted>", text)
text = re.sub(r"(Authorization: )\S+", r"\1<redacted>", text)
sys.stdout.write(text)
'
}

wait_ready() {
  local url="$1"
  local i
  for i in $(seq 1 60); do
    if curl -fsS "$url/healthz" >/dev/null 2>&1 && curl -fsS "$url/readyz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "CONTAINER_PROOF_FAIL: API not ready at $url" >&2
  compose logs enterprise-api 2>&1 | redact | tee "$LOG_PATH" >/dev/null || true
  exit 1
}

api_json() {
  # Usage: api_json METHOD PATH [curl args...]
  local method="$1"
  local path="$2"
  shift 2
  curl -sS -X "$method" "${API_URL}${path}" "$@"
}

prom_query_value() {
  local query="$1"
  curl -fsS --get \
    --data-urlencode "query=${query}" \
    "${PROMETHEUS_URL}/api/v1/query" \
  | python3 -c '
import json, sys
body = json.load(sys.stdin)
assert body.get("status") == "success", body
results = body.get("data", {}).get("result", [])
print(results[0]["value"][1] if results else "")
'
}

wait_metric_at_least() {
  local query="$1"
  local minimum="$2"
  local value=""
  local i
  for i in $(seq 1 80); do
    value="$(prom_query_value "$query")"
    if python3 - "$value" "$minimum" <<'PY'
import sys

value, minimum = sys.argv[1:]
raise SystemExit(0 if value and float(value) >= float(minimum) else 1)
PY
    then
      printf '%s\n' "$value"
      return 0
    fi
    sleep 0.25
  done
  echo "CONTAINER_PROOF_FAIL: metric did not reach ${minimum}: ${query}" >&2
  return 1
}

wait_prometheus_target_up() {
  local candidate="${TARGETS_JSON}.tmp"
  local i
  for i in $(seq 1 80); do
    if curl -fsS "${PROMETHEUS_URL}/api/v1/targets" -o "$candidate"; then
      if python3 - "$candidate" <<'PY'
from pathlib import Path
import json, sys

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if body.get("status") != "success":
    raise SystemExit(1)
targets = [
    target
    for target in body.get("data", {}).get("activeTargets", [])
    if target.get("labels", {}).get("job") == "enterprise-api"
]
raise SystemExit(0 if len(targets) == 1 and targets[0].get("health") == "up" else 1)
PY
      then
        mv "$candidate" "$TARGETS_JSON"
        printf '1\n'
        return 0
      fi
    fi
    sleep 0.25
  done
  rm -f "$candidate"
  echo "CONTAINER_PROOF_FAIL: Prometheus enterprise-api target did not become up" >&2
  return 1
}

select_compose

mkdir -p "$PROOF_DIR"
rm -f "$RECEIPT_PATH" "$LOG_PATH"

echo "==> Using: ${COMPOSE_CMD[*]}"
echo "==> Isolated Compose project: ${COMPOSE_PROJECT_NAME}"
echo "==> Building and starting enterprise-api + Prometheus (wait on Compose healthchecks)"
# --wait blocks until both service healthchecks are healthy (fail closed on timeout).
compose up --build -d --wait enterprise-api prometheus

wait_ready "$API_URL"
echo "==> API ready (healthz + readyz)"

echo "==> Validating Prometheus config, alerts, and no-traffic rule behavior in the pinned image"
compose exec -T prometheus /bin/promtool check config /etc/prometheus/prometheus.yml
compose exec -T prometheus /bin/promtool check rules /etc/prometheus/alerts.yml
compose exec -T --workdir /etc/prometheus prometheus /bin/promtool test rules alerts.test.yml

TARGETS_JSON="$PROOF_DIR/prometheus-targets.json"
RULES_JSON="$PROOF_DIR/prometheus-rules.json"
REQUEST_METRICS_JSON="$PROOF_DIR/prometheus-request-metrics.json"

TARGET_UP="$(wait_prometheus_target_up)"

curl -fsS "${PROMETHEUS_URL}/api/v1/rules" -o "$RULES_JSON"
RULES_LOADED="$(python3 - "$RULES_JSON" <<'PY'
from pathlib import Path
import json, sys

expected = {
    "EnterpriseApiAvailabilityFastBurn",
    "EnterpriseApiAvailabilitySlowBurn",
    "EnterpriseApiLatencyFastBurn",
    "EnterpriseApiLatencySlowBurn",
    "EnterpriseApiPostCommitFailure",
}
body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert body.get("status") == "success", body
loaded = {
    rule["name"]
    for group in body["data"]["groups"]
    for rule in group["rules"]
    if rule.get("type") == "alerting"
}
assert loaded == expected, loaded
print(len(loaded))
PY
)"

echo "==> Negative authentication cases"
NO_AUTH_HTTP="$(
  curl -sS -o /dev/null -w "%{http_code}" \
    -X POST "${API_URL}/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Idempotency-Key: ${IDEM_KEY}-noauth" \
    -H "Content-Type: application/json" \
    -d '{"action_id":"RB-PAY-GATEWAY-01-S2","note":"no auth"}'
)"
test "$NO_AUTH_HTTP" = "401"

READ_ONLY_HTTP="$(
  curl -sS -o /dev/null -w "%{http_code}" \
    -X POST "${API_URL}/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer ${READ_TOKEN}" \
    -H "Idempotency-Key: ${IDEM_KEY}-readonly" \
    -H "Content-Type: application/json" \
    -d '{"action_id":"RB-PAY-GATEWAY-01-S2","note":"read token cannot write"}'
)"
test "$READ_ONLY_HTTP" = "403"

BAD_TOKEN_HTTP="$(
  curl -sS -o /dev/null -w "%{http_code}" \
    -X POST "${API_URL}/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer invalid-container-proof-token" \
    -H "Idempotency-Key: ${IDEM_KEY}-bad" \
    -H "Content-Type: application/json" \
    -d '{"action_id":"RB-PAY-GATEWAY-01-S2","note":"bad token"}'
)"
test "$BAD_TOKEN_HTTP" = "403"

COUNT_AFTER_AUTH="$(
  api_json GET "/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer ${READ_TOKEN}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
)"
test "$COUNT_AFTER_AUTH" = "0"

echo "==> Resetting action store"
api_json POST /v1/admin/reset-actions \
  -H "Authorization: Bearer ${WRITE_TOKEN}" \
  -H "Content-Type: application/json" \
| python3 -c 'import json,sys; body=json.load(sys.stdin); assert body.get("status")=="reset", body'

echo "==> Post-commit failure injection"
FAIL_HTTP="$(
  curl -sS -o "$PROOF_DIR/fail-body.json" -w "%{http_code}" \
    -X POST "${API_URL}/v1/incidents/${INCIDENT_ID}/actions?inject=error_after_commit" \
    -H "Authorization: Bearer ${WRITE_TOKEN}" \
    -H "Idempotency-Key: ${IDEM_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"action_id":"RB-PAY-GATEWAY-01-S2","note":"container-proof post-commit"}'
)"
test "$FAIL_HTTP" = "500"

# Metrics are process-local. Capture the ambiguous post-commit transition before
# restarting the API; durable state, not counters, is what survives restart.
POSTCOMMIT_METRIC="$(
  wait_metric_at_least 'enterprise_api_action_outcomes_total{outcome="postcommit_error"}' 1
)"

COUNT_AFTER_FAIL="$(
  api_json GET "/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer ${READ_TOKEN}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
)"
test "$COUNT_AFTER_FAIL" = "1"

echo "==> Restarting enterprise-api (persistent store must survive)"
compose restart enterprise-api
wait_ready "$API_URL"

COUNT_AFTER_RESTART="$(
  api_json GET "/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer ${READ_TOKEN}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
)"
test "$COUNT_AFTER_RESTART" = "1"

echo "==> Replay without inject"
REPLAY_BODY="$(
  api_json POST "/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer ${WRITE_TOKEN}" \
    -H "Idempotency-Key: ${IDEM_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"action_id":"RB-PAY-GATEWAY-01-S2","note":"container-proof replay"}'
)"
echo "$REPLAY_BODY" | python3 -c '
import json, sys
body = json.load(sys.stdin)
assert body.get("replayed") is True, body
assert "record" in body and body["record"].get("record_id"), body
'

COUNT_AFTER_REPLAY="$(
  api_json GET "/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer ${READ_TOKEN}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
)"
test "$COUNT_AFTER_REPLAY" = "1"

REPLAY_METRIC="$(
  wait_metric_at_least 'enterprise_api_action_outcomes_total{outcome="replayed"}' 1
)"

echo "==> Clean create after restart (distinct metric transition)"
CREATED_HTTP="$(
  curl -sS -o "$PROOF_DIR/created-body.json" -w "%{http_code}" \
    -X POST "${API_URL}/v1/incidents/${INCIDENT_ID}/actions" \
    -H "Authorization: Bearer ${WRITE_TOKEN}" \
    -H "Idempotency-Key: ${IDEM_KEY}-telemetry-created" \
    -H "Content-Type: application/json" \
    -d '{"action_id":"RB-PAY-GATEWAY-01-S3","note":"container-proof telemetry create"}'
)"
test "$CREATED_HTTP" = "201"
CREATED_METRIC="$(
  wait_metric_at_least 'enterprise_api_action_outcomes_total{outcome="created"}' 1
)"

curl -fsS --get \
  --data-urlencode 'query=enterprise_api_http_requests_total' \
  "${PROMETHEUS_URL}/api/v1/query" -o "$REQUEST_METRICS_JSON"
REQUEST_SERIES="$(python3 - "$REQUEST_METRICS_JSON" "$INCIDENT_ID" <<'PY'
from pathlib import Path
import json, sys

body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert body.get("status") == "success", body
series = body.get("data", {}).get("result", [])
assert series, series
serialized = json.dumps(series, sort_keys=True)
assert sys.argv[2] not in serialized, serialized
assert any(
    item["metric"].get("route") == "/v1/incidents/{incident_id}/actions"
    for item in series
), series
print(len(series))
PY
)"

echo "==> Demo arc against containerized API (host MCP/operator → container store)"
AUDIT_DIR="$PROOF_DIR/audit" \
RECEIPT_DIR="$PROOF_DIR/demo-receipts" \
ENTERPRISE_API_URL="$API_URL" \
ENTERPRISE_API_TOKEN="$READ_TOKEN" \
ENTERPRISE_API_WRITE_TOKEN="$WRITE_TOKEN" \
  ./scripts/demo.sh

echo "==> Capturing redacted compose logs"
compose logs --no-color --tail=200 enterprise-api prometheus 2>&1 | redact > "$LOG_PATH"

echo "==> Tearing down the isolated Compose project and volumes"
compose down -v --remove-orphans
TEARDOWN_COMPLETE=1

python3 - <<PY
import json
from pathlib import Path

receipt = {
    "proof": "container-proof",
    "result": "pass",
    "api_url": "${API_URL}",
    "compose": "${COMPOSE_CMD[*]}",
    "compose_project": "${COMPOSE_PROJECT_NAME}",
    "incident_id": "${INCIDENT_ID}",
    "idempotency_key": "${IDEM_KEY}",
    "teardown": {"result": "pass", "volumes_removed": True},
    "counts": {
        "after_negative_auth": int("${COUNT_AFTER_AUTH}"),
        "after_post_commit_failure": int("${COUNT_AFTER_FAIL}"),
        "after_restart": int("${COUNT_AFTER_RESTART}"),
        "after_replay": int("${COUNT_AFTER_REPLAY}"),
    },
    "auth_negative": {
        "missing_bearer": int("${NO_AUTH_HTTP}"),
        "read_token_write": int("${READ_ONLY_HTTP}"),
        "bad_token": int("${BAD_TOKEN_HTTP}"),
    },
    "telemetry": {
        "target_up": bool(int("${TARGET_UP}")),
        "rules_loaded": int("${RULES_LOADED}"),
        "request_series": int("${REQUEST_SERIES}"),
        "action_outcomes": {
            "postcommit_error_before_restart": float("${POSTCOMMIT_METRIC}"),
            "replayed_after_restart": float("${REPLAY_METRIC}"),
            "created_after_restart": float("${CREATED_METRIC}"),
        },
        "promtool": {
            "config": "pass",
            "rules": "pass",
            "rule_tests": "pass",
        },
    },
    "host_vs_container": {
        "enterprise_api": "container (compose)",
        "action_store": "volume-backed ACTION_STORE_PATH inside enterprise-api",
        "restart_probe": "host curl against published port",
        "demo_mcp_and_operator": "host process targeting ENTERPRISE_API_URL",
    },
    "notes": [
        "Fixture tokens only; receipt values are non-secret lab identifiers.",
        "JSON file on a named volume survives container restart; not a transactional DB.",
        "Prometheus counters are process-local; the post-commit sample is captured before API restart.",
        "Prometheus target/rules/metrics are queried locally; no external pager delivery is claimed.",
        "Does not prove Kubernetes, OIDC, cloud deploy, or model-driven invocation.",
    ],
}
path = Path("${RECEIPT_PATH}")
path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
print(path.read_text(encoding="utf-8"))
PY

echo "CONTAINER_PROOF_PASS auth_negative=pass restart=pass replay=pass telemetry=pass demo=pass compose=${COMPOSE_CMD[*]}"
