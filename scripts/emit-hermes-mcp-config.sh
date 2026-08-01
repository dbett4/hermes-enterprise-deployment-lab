#!/usr/bin/env bash
# Emit an isolated Hermes config snippet for the enterprise MCP server.
# Usage: ./scripts/emit-hermes-mcp-config.sh [REPO_ROOT] [ENABLED_TOOLS]
#
# ENABLED_TOOLS is passed to the SERVER as ENTERPRISE_MCP_ENABLED_TOOLS, so the
# tool surface is enforced by the server itself rather than only requested via
# Hermes's `tools.include`. Both are emitted, and they are kept consistent.
set -euo pipefail

ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENABLED_TOOLS="${2:-check_enterprise_api,get_incident_context,propose_incident_plan}"

if [[ "$ENABLED_TOOLS" == "all" || "$ENABLED_TOOLS" == "*" ]]; then
  INCLUDE_LIST="check_enterprise_api get_incident_context propose_incident_plan apply_incident_plan"
else
  INCLUDE_LIST="${ENABLED_TOOLS//,/ }"
fi

cat <<EOF
# Hermes MCP example — generated for repo root: ${ROOT_DIR}
# Install into an isolated HERMES_HOME, not ~/.hermes/config.yaml.
# Token is resolved from the operator environment at connect time and forwarded
# to the server process through this env: block. MCP stdio does not inherit the
# parent environment, so this block is load-bearing, not decorative.
mcp_servers:
  enterprise_ops:
    command: "${ROOT_DIR}/scripts/run-enterprise-mcp.sh"
    args: []
    env:
      ENTERPRISE_API_URL: "http://127.0.0.1:8080"
      ENTERPRISE_API_TOKEN: "\${ENTERPRISE_API_TOKEN}"
      ENTERPRISE_API_TIMEOUT_SECONDS: "10"
      ENTERPRISE_MCP_ENABLED_TOOLS: "${ENABLED_TOOLS}"
      AUDIT_LOG_PATH: "${ROOT_DIR}/.audit/hermes-audit.jsonl"
      APPROVAL_STORE_PATH: "${ROOT_DIR}/.audit/hermes-approvals.json"
      PYTHONPATH: "${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner"
    tools:
      include:
$(for tool in $INCLUDE_LIST; do echo "        - ${tool}"; done)
    sampling:
      enabled: false
EOF
