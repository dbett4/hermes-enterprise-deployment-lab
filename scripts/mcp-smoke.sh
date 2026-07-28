#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

# Protocol-only mode is for CI when Hermes CLI is unavailable.
# Default local full-smoke requires Hermes discovery proof.
MCP_SMOKE_PROTOCOL_ONLY="${MCP_SMOKE_PROTOCOL_ONLY:-0}"

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" && -x "${PYTHON_BIN}" ]]; then
    echo "$PYTHON_BIN"
    return
  fi
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    echo "${ROOT_DIR}/.venv/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  echo "ERROR: no Python interpreter found (set PYTHON_BIN or create .venv)" >&2
  exit 1
}

FASTMCP_CMD=()

resolve_fastmcp() {
  if [[ -n "${FASTMCP_BIN:-}" && -x "${FASTMCP_BIN}" ]]; then
    FASTMCP_CMD=("$FASTMCP_BIN")
    return
  fi
  local py
  py="$(resolve_python)"
  if [[ -x "${ROOT_DIR}/.venv/bin/fastmcp" ]]; then
    FASTMCP_CMD=("${ROOT_DIR}/.venv/bin/fastmcp")
    return
  fi
  if command -v fastmcp >/dev/null 2>&1; then
    FASTMCP_CMD=("$(command -v fastmcp)")
    return
  fi
  if "$py" -m fastmcp --help >/dev/null 2>&1; then
    FASTMCP_CMD=("$py" -m fastmcp)
    return
  fi
  echo "ERROR: fastmcp not found (pip install -r requirements-dev.txt or set FASTMCP_BIN to an executable path)" >&2
  exit 1
}

PYTHON_BIN="$(resolve_python)"
resolve_fastmcp
HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"
# FastMCP shell-splits --command; use repo-relative paths so spaced ROOT_DIR works.
MCP_LAUNCHER="./scripts/run-enterprise-mcp.sh"
SERVER_SPEC="enterprise-mcp/enterprise_mcp/server.py:mcp"
RECEIPT_DIR="${RECEIPT_DIR:-${ROOT_DIR}/.mcp-receipts}"
RECEIPT_PATH="${RECEIPT_PATH:-${RECEIPT_DIR}/mcp-smoke-receipt.json}"
INCIDENT_ID="${DEFAULT_INCIDENT_ID:-INC-2026-0042}"

export PYTHONPATH="${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner${PYTHONPATH:+:${PYTHONPATH}}"
export ENTERPRISE_API_URL="${ENTERPRISE_API_URL:-http://127.0.0.1:8080}"
export ENTERPRISE_API_TOKEN="${ENTERPRISE_API_TOKEN:-lab-read-token}"

mkdir -p "$RECEIPT_DIR"

echo "==> Checking enterprise-api health at ${ENTERPRISE_API_URL}"
curl -fsS "${ENTERPRISE_API_URL}/healthz" >/dev/null
curl -fsS "${ENTERPRISE_API_URL}/readyz" >/dev/null

echo "==> FastMCP inspect"
"${FASTMCP_CMD[@]}" inspect "$SERVER_SPEC"

echo "==> FastMCP list tools"
TOOL_LIST_JSON="$("${FASTMCP_CMD[@]}" list --command "$MCP_LAUNCHER" --json)"
echo "$TOOL_LIST_JSON"

echo "==> FastMCP call check_enterprise_api"
CHECK_JSON="$("${FASTMCP_CMD[@]}" call --command "$MCP_LAUNCHER" --target check_enterprise_api --json)"
echo "$CHECK_JSON"

echo "==> FastMCP call get_incident_context"
CONTEXT_JSON="$("${FASTMCP_CMD[@]}" call --command "$MCP_LAUNCHER" --target get_incident_context --input-json "{\"incident_id\":\"$INCIDENT_ID\"}" --json)"
echo "$CONTEXT_JSON"

echo "==> FastMCP call propose_incident_plan"
PLAN_JSON="$("${FASTMCP_CMD[@]}" call --command "$MCP_LAUNCHER" --target propose_incident_plan --input-json "{\"incident_id\":\"$INCIDENT_ID\"}" --json)"
echo "$PLAN_JSON"

"$PYTHON_BIN" - <<'PY' "$CHECK_JSON" "$CONTEXT_JSON" "$PLAN_JSON"
import json
import sys

def payload(raw: str) -> dict:
    obj = json.loads(raw)
    if isinstance(obj, dict) and "structured_content" in obj:
        return obj["structured_content"]
    if isinstance(obj, dict) and "data" in obj:
        return obj["data"]
    return obj

check_data = payload(sys.argv[1])
context_data = payload(sys.argv[2])
plan_data = payload(sys.argv[3])

assert check_data.get("outcome") in {"success", "degraded"}, check_data
assert context_data.get("outcome") == "success", context_data
assert plan_data.get("outcome") == "success", plan_data
assert plan_data.get("approval_required") is True
assert "lab-read-token" not in json.dumps({"check": check_data, "context": context_data, "plan": plan_data})
PY

FASTMCP_PROTOCOL_STATUS="passed"
HERMES_DISCOVERY_STATUS="skipped"
HERMES_DISCOVERY_REASON="protocol_only_mode"
HERMES_TOOLS_JSON="[]"
HERMES_TEST_OUTPUT=""

if [[ "$MCP_SMOKE_PROTOCOL_ONLY" == "1" ]]; then
  echo "==> Hermes discovery skipped (MCP_SMOKE_PROTOCOL_ONLY=1)"
elif [[ -n "$HERMES_BIN" && -x "$HERMES_BIN" ]]; then
  echo "==> Hermes MCP discovery (isolated HERMES_HOME)"
  if ! "$ROOT_DIR/scripts/hermes-mcp-proof.sh" > /tmp/hermes-mcp-proof.out 2>&1; then
    cat /tmp/hermes-mcp-proof.out >&2
    exit 1
  fi
  cat /tmp/hermes-mcp-proof.out
  HERMES_RECEIPT="$(python3 -c "import json; print(json.load(open('${ROOT_DIR}/.mcp-receipts/hermes-mcp-proof.json'))['status'])")"
  HERMES_TOOLS_JSON="$(python3 -c "import json; print(json.dumps(json.load(open('${ROOT_DIR}/.mcp-receipts/hermes-mcp-proof.json'))['discovered_tools']))")"
  HERMES_DISCOVERY_STATUS="$HERMES_RECEIPT"
  HERMES_DISCOVERY_REASON="hermes_mcp_test"
else
  echo "ERROR: hermes CLI not found; full smoke requires Hermes discovery proof." >&2
  echo "Install Hermes CLI or set MCP_SMOKE_PROTOCOL_ONLY=1 for protocol-only CI runs." >&2
  exit 1
fi

OVERALL_STATUS="passed"
if [[ "$MCP_SMOKE_PROTOCOL_ONLY" == "1" ]]; then
  OVERALL_STATUS="passed_protocol_only"
elif [[ "$HERMES_DISCOVERY_STATUS" != "passed" ]]; then
  OVERALL_STATUS="failed"
  exit 1
fi

"$PYTHON_BIN" - <<'PY' "$RECEIPT_PATH" "$CHECK_JSON" "$CONTEXT_JSON" "$PLAN_JSON" "$HERMES_TOOLS_JSON" "$INCIDENT_ID" "$FASTMCP_PROTOCOL_STATUS" "$HERMES_DISCOVERY_STATUS" "$HERMES_DISCOVERY_REASON" "$OVERALL_STATUS" "$MCP_SMOKE_PROTOCOL_ONLY"
import json
import sys
from datetime import datetime, timezone

(
    receipt_path,
    check_json,
    context_json,
    plan_json,
    hermes_tools_json,
    incident_id,
    fastmcp_status,
    hermes_status,
    hermes_reason,
    overall_status,
    protocol_only,
) = sys.argv[1:]

payload = {
    "overall_status": overall_status,
    "protocol_only": protocol_only == "1",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "incident_id": incident_id,
    "fastmcp_protocol": {
        "status": fastmcp_status,
        "check_enterprise_api": json.loads(check_json),
        "get_incident_context": json.loads(context_json),
        "propose_incident_plan": json.loads(plan_json),
    },
    "hermes_discovery": {
        "status": hermes_status,
        "reason": hermes_reason,
        "invocation": "discovery_only",
        "discovered_tools": json.loads(hermes_tools_json),
        "note": (
            "Hermes CLI supports mcp test (discovery) only; tool invocation is proven "
            "at the FastMCP protocol layer above."
        ),
    },
}
with open(receipt_path, "w") as fh:
    json.dump(payload, fh, indent=2)
print(
    json.dumps(
        {
            "overall_status": overall_status,
            "receipt_path": receipt_path,
            "fastmcp_protocol": fastmcp_status,
            "hermes_discovery": hermes_status,
            "hermes_tools": payload["hermes_discovery"]["discovered_tools"],
        },
        indent=2,
    )
)
PY
