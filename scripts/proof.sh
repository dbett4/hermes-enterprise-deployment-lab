#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
FASTMCP_BIN="${FASTMCP_BIN:-${ROOT_DIR}/.venv/bin/fastmcp}"
PROOF_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-lab-proof.XXXXXX")"

cleanup() {
  case "$PROOF_DIR" in
    /tmp/*) rm -rf -- "$PROOF_DIR" ;;
    *) echo "Refusing to remove unexpected proof directory: $PROOF_DIR" >&2 ;;
  esac
}
trap cleanup EXIT

if [[ ! -x "$PYTHON_BIN" || ! -x "$FASTMCP_BIN" ]]; then
  echo "Create .venv and install requirements-dev.txt plus both component requirements first." >&2
  exit 2
fi

cd "$ROOT_DIR"

"$PYTHON_BIN" -m pytest -o addopts="" -q

PYTHONPATH="${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner" \
  "$FASTMCP_BIN" inspect enterprise-mcp/enterprise_mcp/server.py:mcp >/dev/null

"$PYTHON_BIN" -c \
  "import yaml; yaml.safe_load(open('compose.yaml')); print('COMPOSE_PARSE_PASS')"

AUDIT_DIR="${PROOF_DIR}/audit" \
RECEIPT_DIR="${PROOF_DIR}/receipts" \
PYTHON_BIN="$PYTHON_BIN" \
  ./scripts/demo.sh

echo "LAB_PROOF_PASS tests=73 mcp_inspect=pass compose_parse=pass demo=pass"
