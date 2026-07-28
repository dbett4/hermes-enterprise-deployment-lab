#!/usr/bin/env bash
# Deterministic Hermes MCP discovery proof using isolated HERMES_HOME.
# Does not invoke an LLM or mutate live ~/.hermes configuration.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

HERMES_BIN="${HERMES_BIN:-$(command -v hermes)}"
if [[ -z "$HERMES_BIN" || ! -x "$HERMES_BIN" ]]; then
  echo "ERROR: hermes CLI not found in PATH" >&2
  exit 1
fi

export ENTERPRISE_API_URL="${ENTERPRISE_API_URL:-http://127.0.0.1:8080}"
export ENTERPRISE_API_TOKEN="${ENTERPRISE_API_TOKEN:-lab-read-token}"
export ENTERPRISE_API_TIMEOUT_SECONDS="${ENTERPRISE_API_TIMEOUT_SECONDS:-10}"

RECEIPT_DIR="${RECEIPT_DIR:-${ROOT_DIR}/.mcp-receipts}"
RECEIPT_PATH="${RECEIPT_PATH:-${RECEIPT_DIR}/hermes-mcp-proof.json}"
mkdir -p "$RECEIPT_DIR"

HERMES_HOME_TMP="$(mktemp -d "${TMPDIR:-/tmp}/hermes-mcp-lab.XXXXXX")"
cleanup() {
  rm -rf "$HERMES_HOME_TMP"
}
trap cleanup EXIT

export HERMES_HOME="$HERMES_HOME_TMP"
{
  echo "_config_version: 9"
  "$ROOT_DIR/scripts/emit-hermes-mcp-config.sh" "$ROOT_DIR"
} > "$HERMES_HOME/config.yaml"

echo "==> hermes mcp test enterprise_ops (HERMES_HOME=$HERMES_HOME)"
set +e
HERMES_TEST_OUTPUT="$("$HERMES_BIN" mcp test enterprise_ops 2>&1)"
HERMES_STATUS=$?
set -e
echo "$HERMES_TEST_OUTPUT"

PARSED="$("$PYTHON_BIN" - <<'PY' "$HERMES_TEST_OUTPUT" "$HERMES_STATUS"
import json
import re
import sys

output = sys.argv[1]
exit_code = int(sys.argv[2])
known = {
    "check_enterprise_api",
    "get_incident_context",
    "propose_incident_plan",
}
tools = []
for line in output.splitlines():
    match = re.match(r"^\s+([a-z_]+)\s+", line)
    if match and match.group(1) in known:
        tools.append(f"mcp__enterprise_ops__{match.group(1)}")
        continue
    match = re.search(r"mcp__enterprise_ops__([a-z_]+)", line)
    if match and match.group(1) in known:
        tools.append(f"mcp__enterprise_ops__{match.group(1)}")
tools = sorted(set(tools))
status = "passed" if exit_code == 0 and len(tools) == 3 else "failed"
print(json.dumps({"status": status, "exit_code": exit_code, "discovered_tools": tools}))
PY
)"

STATUS="$(echo "$PARSED" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['status'])")"
if [[ "$STATUS" != "passed" ]]; then
  echo "Hermes MCP discovery proof failed" >&2
  echo "$PARSED" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY' "$RECEIPT_PATH" "$PARSED" "$HERMES_HOME"
import json
import sys
from datetime import datetime, timezone

receipt_path = sys.argv[1]
parsed = json.loads(sys.argv[2])
hermes_home = sys.argv[3]

payload = {
    "status": parsed["status"],
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "hermes_home": hermes_home,
    "invocation": "discovery_only",
    "discovered_tools": parsed["discovered_tools"],
    "exit_code": parsed["exit_code"],
    "limitations": [
        "Hermes CLI mcp test proves server discovery and tool listing only.",
        "Tool invocation is proven separately via FastMCP protocol calls in mcp-smoke.sh.",
        "No LLM/provider call is made in this proof.",
    ],
}
with open(receipt_path, "w") as fh:
    json.dump(payload, fh, indent=2)
print(json.dumps(payload, indent=2))
PY
