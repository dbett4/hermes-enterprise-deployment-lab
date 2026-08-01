#!/usr/bin/env bash
# Deterministic Hermes MCP discovery proof using an isolated HERMES_HOME.
# Does not invoke an LLM and does not touch live ~/.hermes configuration.
#
# Scope of the claim: Hermes connects to this MCP server and lists its tools.
# Tool NAMES are recorded exactly as Hermes prints them (bare). Hermes's
# mcp__<server>__<tool> prefixing convention is recorded separately and flagged
# as asserted-not-observed, because `mcp mcp test` does not print it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"
if [[ -z "$HERMES_BIN" || ! -x "$HERMES_BIN" ]]; then
  echo "ERROR: hermes CLI not found in PATH" >&2
  exit 1
fi

export ENTERPRISE_API_URL="${ENTERPRISE_API_URL:-http://127.0.0.1:8080}"
export ENTERPRISE_API_TOKEN="${ENTERPRISE_API_TOKEN:-lab-read-token}"
export ENTERPRISE_API_TIMEOUT_SECONDS="${ENTERPRISE_API_TIMEOUT_SECONDS:-10}"
ENABLED_TOOLS="${ENTERPRISE_MCP_ENABLED_TOOLS:-check_enterprise_api,get_incident_context,propose_incident_plan}"

RECEIPT_DIR="${RECEIPT_DIR:-${ROOT_DIR}/.mcp-receipts}"
RECEIPT_PATH="${RECEIPT_PATH:-${RECEIPT_DIR}/hermes-mcp-proof.json}"
mkdir -p "$RECEIPT_DIR"

HERMES_VERSION="$("$HERMES_BIN" --version 2>&1 | head -1 || true)"

HERMES_HOME_TMP="$(mktemp -d "${TMPDIR:-/tmp}/hermes-mcp-lab.XXXXXX")"
cleanup() {
  rm -rf "$HERMES_HOME_TMP"
}
trap cleanup EXIT

export HERMES_HOME="$HERMES_HOME_TMP"
{
  echo "_config_version: 9"
  "$ROOT_DIR/scripts/emit-hermes-mcp-config.sh" "$ROOT_DIR" "$ENABLED_TOOLS"
} > "$HERMES_HOME/config.yaml"

echo "==> hermes mcp test enterprise_ops (HERMES_HOME=$HERMES_HOME)"
set +e
HERMES_TEST_OUTPUT="$("$HERMES_BIN" mcp test enterprise_ops 2>&1)"
HERMES_STATUS=$?
set -e
echo "$HERMES_TEST_OUTPUT"

PARSED="$("$PYTHON_BIN" - "$HERMES_TEST_OUTPUT" "$HERMES_STATUS" "$ENABLED_TOOLS" <<'PY'
import json
import re
import sys

output, exit_code, enabled = sys.argv[1], int(sys.argv[2]), sys.argv[3]
known = [
    "check_enterprise_api",
    "get_incident_context",
    "propose_incident_plan",
    "apply_incident_plan",
]
expected = sorted(known) if enabled.strip() in {"all", "*"} else sorted(
    t.strip() for t in enabled.split(",") if t.strip()
)
observed = sorted(name for name in known if re.search(rf"\b{name}\b", output))
status = "passed" if exit_code == 0 and observed == expected else "failed"
print(json.dumps({
    "status": status,
    "exit_code": exit_code,
    "expected_tools": expected,
    "discovered_tools": observed,
}))
PY
)"

STATUS="$(echo "$PARSED" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['status'])")"
if [[ "$STATUS" != "passed" ]]; then
  echo "Hermes MCP discovery proof failed" >&2
  echo "$PARSED" >&2
  exit 1
fi

"$PYTHON_BIN" - "$RECEIPT_PATH" "$PARSED" "$HERMES_HOME" "$HERMES_VERSION" <<'PY'
import json
import sys
from datetime import datetime, timezone

receipt_path, parsed_raw, hermes_home, hermes_version = sys.argv[1:5]
parsed = json.loads(parsed_raw)

payload = {
    "status": parsed["status"],
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "hermes_home": hermes_home,
    "hermes_version": hermes_version,
    "invocation": "discovery_only",
    "expected_tools": parsed["expected_tools"],
    "discovered_tools": parsed["discovered_tools"],
    "tool_names_as_printed_by_hermes": "bare (unprefixed)",
    "hermes_prefix_convention": {
        "value": "mcp__enterprise_ops__<tool>",
        "observed_in_this_run": False,
        "note": (
            "Hermes prefixes MCP tool names for the model-facing toolset, but "
            "`hermes mcp test` prints bare names. The prefixed form is asserted "
            "from Hermes source, not observed here."
        ),
    },
    "exit_code": parsed["exit_code"],
    "limitations": [
        "Hermes CLI `mcp test` proves server connection and tool listing only.",
        "Tool invocation is proven separately at the FastMCP protocol layer.",
        "No LLM/provider call is made in this proof; no model chose any tool.",
    ],
}
with open(receipt_path, "w") as fh:
    json.dump(payload, fh, indent=2)
print(json.dumps(payload, indent=2))
PY
