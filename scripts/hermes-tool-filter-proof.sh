#!/usr/bin/env bash
# Differential proof that the tool surface Hermes discovers is actually scoped.
#
# Why a differential: `hermes mcp test` reports what the SERVER advertises, not
# the filtered set Hermes hands to a model. Verified on 2026-08-01 — narrowing
# Hermes's own `tools.include` to one entry still printed all three tools. So a
# config whose include list matches the server surface proves nothing either way.
#
# This script varies the SERVER-side allowlist (ENTERPRISE_MCP_ENABLED_TOOLS) and
# asserts Hermes observes a different tool set. That is a claim `hermes mcp test`
# can actually settle.
#
# Still NOT proven here: that Hermes enforces its own include list against a
# model. That needs a model-driven run.
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

RECEIPT_DIR="${RECEIPT_DIR:-${ROOT_DIR}/.mcp-receipts}"
RECEIPT_PATH="${RECEIPT_PATH:-${RECEIPT_DIR}/hermes-tool-filter-proof.json}"
mkdir -p "$RECEIPT_DIR"

HERMES_VERSION="$("$HERMES_BIN" --version 2>&1 | head -1 || true)"

run_variant() {
  local enabled="$1"
  local home
  home="$(mktemp -d "${TMPDIR:-/tmp}/hermes-filter.XXXXXX")"
  {
    echo "_config_version: 9"
    "$ROOT_DIR/scripts/emit-hermes-mcp-config.sh" "$ROOT_DIR" "$enabled"
  } > "$home/config.yaml"
  HERMES_HOME="$home" "$HERMES_BIN" mcp test enterprise_ops 2>&1 || true
  rm -rf "$home"
}

echo "==> Variant A: server allowlist = all"
VARIANT_A="$(run_variant "all")"
echo "$VARIANT_A"

echo
echo "==> Variant B: server allowlist = check_enterprise_api"
VARIANT_B="$(run_variant "check_enterprise_api")"
echo "$VARIANT_B"

"$PYTHON_BIN" - "$RECEIPT_PATH" "$VARIANT_A" "$VARIANT_B" "$HERMES_VERSION" <<'PY'
import json
import re
import sys
from datetime import datetime, timezone

receipt_path, variant_a, variant_b, hermes_version = sys.argv[1:5]

KNOWN = [
    "check_enterprise_api",
    "get_incident_context",
    "propose_incident_plan",
    "apply_incident_plan",
]


def discovered(output: str) -> list[str]:
    found = []
    for name in KNOWN:
        if re.search(rf"\b{name}\b", output):
            found.append(name)
    return sorted(found)


tools_a = discovered(variant_a)
tools_b = discovered(variant_b)

checks = {
    "variant_a_exposes_full_surface": tools_a == sorted(KNOWN),
    "variant_b_exposes_single_tool": tools_b == ["check_enterprise_api"],
    "hermes_observed_a_difference": tools_a != tools_b,
    "variant_b_is_a_strict_subset": set(tools_b) < set(tools_a),
}
status = "passed" if all(checks.values()) else "failed"

payload = {
    "status": status,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "hermes_version": hermes_version,
    "method": "hermes mcp test against two isolated HERMES_HOME configs",
    "variant_a": {"enterprise_mcp_enabled_tools": "all", "hermes_discovered": tools_a},
    "variant_b": {
        "enterprise_mcp_enabled_tools": "check_enterprise_api",
        "hermes_discovered": tools_b,
    },
    "checks": checks,
    "proves": [
        "Hermes discovers exactly the tool surface the server advertises.",
        "The server-side allowlist changes what Hermes can see.",
    ],
    "does_not_prove": [
        "That Hermes enforces its own tools.include list against a model.",
        "Any model-driven tool invocation.",
    ],
}
with open(receipt_path, "w") as fh:
    json.dump(payload, fh, indent=2)
print(json.dumps(payload, indent=2))
if status != "passed":
    sys.exit(1)
PY
