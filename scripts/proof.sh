#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
FASTMCP_BIN="${FASTMCP_BIN:-${ROOT_DIR}/.venv/bin/fastmcp}"
PROOF_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-lab-proof.XXXXXX")"
WITH_CONTAINERS=0

for arg in "$@"; do
  case "$arg" in
    --with-containers) WITH_CONTAINERS=1 ;;
    -h|--help)
      echo "Usage: $0 [--with-containers]"
      echo "Fast path by default. Set PROOF_WITH_CONTAINERS=1 or pass --with-containers"
      echo "to also run scripts/container-proof.sh (requires docker or podman compose)."
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ "${PROOF_WITH_CONTAINERS:-0}" == "1" ]]; then
  WITH_CONTAINERS=1
fi

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

TEST_LOG="${PROOF_DIR}/pytest.txt"
set +e
"$PYTHON_BIN" -m pytest -o addopts="" -q 2>&1 | tee "$TEST_LOG"
PYTEST_RC=${PIPESTATUS[0]}
set -e
if [[ "$PYTEST_RC" -ne 0 ]]; then
  exit "$PYTEST_RC"
fi
TEST_COUNT="$(
  "$PYTHON_BIN" - "$TEST_LOG" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
matches = re.findall(r"(?:^|\n)(\d+) passed(?:,|\s)", text)
if not matches:
    raise SystemExit("Could not derive pytest pass count from proof output")
print(matches[-1])
PY
)"

PYTHONPATH="${ROOT_DIR}/enterprise-mcp:${ROOT_DIR}/workflow-runner" \
  "$FASTMCP_BIN" inspect enterprise-mcp/enterprise_mcp/server.py:mcp >/dev/null

"$PYTHON_BIN" -c \
  "import yaml; yaml.safe_load(open('compose.yaml')); print('COMPOSE_PARSE_PASS')"

AUDIT_DIR="${PROOF_DIR}/audit" \
RECEIPT_DIR="${PROOF_DIR}/receipts" \
PYTHON_BIN="$PYTHON_BIN" \
  ./scripts/demo.sh

TELEMETRY_PROOF_DIR="${ROOT_DIR}/.telemetry-proof" \
PYTHON_BIN="$PYTHON_BIN" ./scripts/telemetry-proof.sh

TRACE_PROOF_DIR="${ROOT_DIR}/.trace-proof" \
PYTHON_BIN="$PYTHON_BIN" ./scripts/trace-proof.sh

CONTAINER_NOTE="containers=skipped"
if [[ "$WITH_CONTAINERS" == "1" ]]; then
  bash ./scripts/container-proof.sh
  CONTAINER_NOTE="containers=pass"
fi

echo "LAB_PROOF_PASS tests=${TEST_COUNT} mcp_inspect=pass compose_parse=pass demo=pass telemetry=pass trace=pass ${CONTAINER_NOTE}"
