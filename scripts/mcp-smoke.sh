#!/usr/bin/env bash
# MCP smoke: FastMCP protocol proof (with real credential injection) plus
# optional Hermes discovery proof.
#
# The protocol section runs through enterprise_mcp.protocol_smoke rather than the
# fastmcp CLI's command-spawning subcommands, because that path cannot pass
# environment variables to the spawned server. MCP stdio forwards only an
# allowlist (HOME, LOGNAME, PATH, SHELL, USER), so the token silently never
# arrived and a deliberately wrong token still "succeeded". See ADR 004.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

# Protocol-only mode is for CI when the Hermes CLI is unavailable.
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
  echo "ERROR: fastmcp not found (pip install -r requirements-dev.txt or set FASTMCP_BIN)" >&2
  exit 1
}

PYTHON_BIN="$(resolve_python)"
resolve_fastmcp
HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"
SERVER_SPEC="enterprise-mcp/enterprise_mcp/server.py:mcp"
RECEIPT_DIR="${RECEIPT_DIR:-${ROOT_DIR}/.mcp-receipts}"
RECEIPT_PATH="${RECEIPT_PATH:-${RECEIPT_DIR}/mcp-smoke-receipt.json}"
PROTOCOL_RECEIPT="${RECEIPT_DIR}/fastmcp-protocol.json"
INCIDENT_ID="${DEFAULT_INCIDENT_ID:-INC-2026-0042}"
AUDIT_LOG="${AUDIT_LOG:-${ROOT_DIR}/.audit/smoke-audit.jsonl}"

export PYTHONPATH="${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner${PYTHONPATH:+:${PYTHONPATH}}"
export ENTERPRISE_API_URL="${ENTERPRISE_API_URL:-http://127.0.0.1:8080}"
export ENTERPRISE_API_TOKEN="${ENTERPRISE_API_TOKEN:-lab-read-token}"

mkdir -p "$RECEIPT_DIR" "$(dirname "$AUDIT_LOG")"

echo "==> Checking enterprise-api health at ${ENTERPRISE_API_URL}"
curl -fsS "${ENTERPRISE_API_URL}/healthz" >/dev/null
curl -fsS "${ENTERPRISE_API_URL}/readyz" >/dev/null

echo "==> FastMCP inspect"
"${FASTMCP_CMD[@]}" inspect "$SERVER_SPEC"

echo "==> FastMCP protocol smoke (explicit credential injection + negative control)"
"$PYTHON_BIN" -m enterprise_mcp.protocol_smoke \
  --api-url "$ENTERPRISE_API_URL" \
  --token "$ENTERPRISE_API_TOKEN" \
  --incident-id "$INCIDENT_ID" \
  --audit-log "$AUDIT_LOG" \
  --output "$PROTOCOL_RECEIPT"

FASTMCP_PROTOCOL_STATUS="passed"
HERMES_DISCOVERY_STATUS="skipped"
HERMES_DISCOVERY_REASON="protocol_only_mode"
HERMES_TOOLS_JSON="[]"

if [[ "$MCP_SMOKE_PROTOCOL_ONLY" == "1" ]]; then
  echo "==> Hermes discovery skipped (MCP_SMOKE_PROTOCOL_ONLY=1)"
elif [[ -n "$HERMES_BIN" && -x "$HERMES_BIN" ]]; then
  echo "==> Hermes MCP discovery (isolated HERMES_HOME)"
  "$ROOT_DIR/scripts/hermes-mcp-proof.sh"
  HERMES_DISCOVERY_STATUS="$("$PYTHON_BIN" -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "${RECEIPT_DIR}/hermes-mcp-proof.json")"
  HERMES_TOOLS_JSON="$("$PYTHON_BIN" -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))['discovered_tools']))" "${RECEIPT_DIR}/hermes-mcp-proof.json")"
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
  echo "Hermes discovery did not pass" >&2
  exit 1
fi

"$PYTHON_BIN" - "$RECEIPT_PATH" "$PROTOCOL_RECEIPT" "$HERMES_TOOLS_JSON" "$INCIDENT_ID" \
  "$FASTMCP_PROTOCOL_STATUS" "$HERMES_DISCOVERY_STATUS" "$HERMES_DISCOVERY_REASON" \
  "$OVERALL_STATUS" "$MCP_SMOKE_PROTOCOL_ONLY" <<'PY'
import json
import sys
from datetime import datetime, timezone

(
    receipt_path,
    protocol_receipt,
    hermes_tools_json,
    incident_id,
    fastmcp_status,
    hermes_status,
    hermes_reason,
    overall_status,
    protocol_only,
) = sys.argv[1:10]

with open(protocol_receipt) as fh:
    protocol = json.load(fh)

payload = {
    "overall_status": overall_status,
    "protocol_only": protocol_only == "1",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "incident_id": incident_id,
    "fastmcp_protocol": {
        "status": protocol.get("status", fastmcp_status),
        "credential_injection": "explicit env passed to the stdio subprocess",
        "checks": protocol.get("checks", []),
        "receipt": protocol_receipt,
    },
    "hermes_discovery": {
        "status": hermes_status,
        "reason": hermes_reason,
        "invocation": "discovery_only",
        "discovered_tools": json.loads(hermes_tools_json),
        "note": (
            "Hermes CLI `mcp test` proves connection and tool listing only. Tool "
            "invocation here is performed by scripts, not by a model."
        ),
    },
    "not_proven": [
        "No LLM chose or invoked any of these tools in this run, or in any run: "
        "provider spend was declined on 2026-08-01 and no model-driven run is "
        "planned. Hermes's role here is discovery and enumeration only.",
        "Hermes-side tools.include enforcement against a model is not exercised.",
    ],
}
with open(receipt_path, "w") as fh:
    json.dump(payload, fh, indent=2)
print(json.dumps({
    "overall_status": overall_status,
    "receipt_path": receipt_path,
    "fastmcp_protocol": payload["fastmcp_protocol"]["status"],
    "hermes_discovery": hermes_status,
    "hermes_tools": payload["hermes_discovery"]["discovered_tools"],
}, indent=2))
PY
