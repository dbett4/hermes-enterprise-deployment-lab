#!/usr/bin/env bash
# Emit an isolated Hermes config snippet for the enterprise MCP server.
# Usage: ./scripts/emit-hermes-mcp-config.sh [REPO_ROOT]
set -euo pipefail

ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

cat <<EOF
# Hermes MCP example — generated for repo root: ${ROOT_DIR}
# Install into an isolated HERMES_HOME, not ~/.hermes/config.yaml.
# Token is resolved from the operator environment at connect time.
mcp_servers:
  enterprise_ops:
    command: "${ROOT_DIR}/scripts/run-enterprise-mcp.sh"
    args: []
    env:
      ENTERPRISE_API_URL: "http://127.0.0.1:8080"
      ENTERPRISE_API_TOKEN: "\${ENTERPRISE_API_TOKEN}"
      ENTERPRISE_API_TIMEOUT_SECONDS: "10"
      PYTHONPATH: "${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner"
    tools:
      include:
        - check_enterprise_api
        - get_incident_context
        - propose_incident_plan
    sampling:
      enabled: false
EOF
