#!/usr/bin/env bash
# Launcher for the enterprise MCP stdio server.
#
# Fails closed on a missing credential. MCP stdio does NOT inherit the caller's
# environment: the SDK forwards only an allowlist (HOME, LOGNAME, PATH, SHELL,
# USER). Whatever starts this launcher must pass ENTERPRISE_API_TOKEN explicitly
# (Hermes does this through the `env:` block of its MCP server config).
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -z "${ENTERPRISE_API_TOKEN:-}" ]]; then
  echo "ERROR: ENTERPRISE_API_TOKEN is not set in this process environment." >&2
  echo "       The MCP client must pass it explicitly; there is no default token." >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
exec "$PYTHON_BIN" -m enterprise_mcp.server
