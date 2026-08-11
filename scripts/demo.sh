#!/usr/bin/env bash
# One-command demo of the whole arc: scoped discovery -> read/plan -> blocked
# approval request -> separate operator command -> forced failure -> idempotent
# resume -> terminal capability -> audit trail.
#
# The script automates both roles for determinism, but crosses a distinct
# operator-command boundary and records a fixture approver identity. This proves
# structural separation, not a real person's identity or judgment.
#
# Everything runs locally over MCP stdio. No LLM, no provider credits, no
# network egress. No model has ever driven this arc and none will — provider
# spend was declined on 2026-08-01. Containers are optional: by default this boots the enterprise
# API directly with uvicorn so the demo is deterministic and CI-runnable.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

export ENTERPRISE_API_TOKEN="${ENTERPRISE_API_TOKEN:-lab-read-token}"
export ENTERPRISE_API_WRITE_TOKEN="${ENTERPRISE_API_WRITE_TOKEN:-lab-write-token}"
export PYTHONPATH="${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner${PYTHONPATH:+:${PYTHONPATH}}"

AUDIT_DIR="${AUDIT_DIR:-${ROOT_DIR}/.audit}"
RECEIPT_DIR="${RECEIPT_DIR:-${ROOT_DIR}/.smoke-receipts}"
mkdir -p "$AUDIT_DIR" "$RECEIPT_DIR"

# Fresh audit + approval state so the printed transcript is exactly this run.
AUDIT_LOG="${AUDIT_LOG:-${AUDIT_DIR}/demo-audit.jsonl}"
APPROVAL_STORE="${APPROVAL_STORE:-${AUDIT_DIR}/demo-approvals.json}"
rm -f "$AUDIT_LOG" "$APPROVAL_STORE"

API_PID=""
cleanup() {
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

API_URL="${ENTERPRISE_API_URL:-}"
if [[ -n "$API_URL" ]] && curl -fsS "${API_URL}/healthz" >/dev/null 2>&1; then
  echo "==> Using the enterprise API already running at ${API_URL}"
else
  PORT="$("$PYTHON_BIN" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
  API_URL="http://127.0.0.1:${PORT}"
  API_LOG="${AUDIT_DIR}/demo-enterprise-api.log"
  echo "==> Starting enterprise-api on ${API_URL} (log: ${API_LOG})"
  # `exec` so API_PID is uvicorn itself, not a wrapper subshell that would leave
  # an orphan holding this script's stdout open.
  (
    cd "${ROOT_DIR}/enterprise-api"
    exec env PYTHONPATH="${ROOT_DIR}/enterprise-api" \
      "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
      --log-level warning >"$API_LOG" 2>&1
  ) &
  API_PID=$!

  for _ in $(seq 1 60); do
    if curl -fsS "${API_URL}/healthz" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  curl -fsS "${API_URL}/healthz" >/dev/null
fi

"$PYTHON_BIN" -m enterprise_mcp.demo \
  --api-url "$API_URL" \
  --read-token "$ENTERPRISE_API_TOKEN" \
  --write-token "$ENTERPRISE_API_WRITE_TOKEN" \
  --audit-log "$AUDIT_LOG" \
  --approval-store "$APPROVAL_STORE" \
  --receipt "${RECEIPT_DIR}/demo-receipt.json"
