#!/usr/bin/env bash
# Prove that a clean clone of HEAD reproduces: fresh venv, fresh install,
# full test suite, full demo. Runs entirely locally — no remote, no push.
#
# This is the half of "fresh-clone CI is green" that can be established without
# a git remote. The other half (a green Actions run) requires pushing to GitHub.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${1:-$(mktemp -d "${TMPDIR:-/tmp}/hermes-lab-freshclone.XXXXXX")}"
CLONE_DIR="${WORK_DIR}/hermes-enterprise-deployment-lab"

echo "==> Cloning HEAD of ${ROOT_DIR}"
echo "    into ${CLONE_DIR}"
git -C "$ROOT_DIR" rev-parse HEAD
rm -rf "$CLONE_DIR"
git clone --quiet --local "$ROOT_DIR" "$CLONE_DIR"

cd "$CLONE_DIR"
echo "==> Clone HEAD: $(git rev-parse HEAD)"
echo "==> Files present: $(git ls-files | wc -l | tr -d ' ')"

# Pick a supported interpreter explicitly. `python3` is whatever the machine
# defaults to, and the pinned dependency set does not build on 3.14 (pydantic-core
# needs a PyO3 newer than the one it vendors). Failing here with a clear message
# beats a ten-minute Rust build that ends in a wall of cargo output.
select_python() {
  local candidate
  if [[ -n "${FRESH_CLONE_PYTHON:-}" ]]; then
    echo "$FRESH_CLONE_PYTHON"
    return
  fi
  for candidate in python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return
    fi
  done
  local default_version
  default_version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
  echo "ERROR: no supported interpreter found (need 3.11, 3.12, or 3.13)." >&2
  echo "       Default python3 is ${default_version}, which the pinned" >&2
  echo "       dependency set does not build on. Set FRESH_CLONE_PYTHON to override." >&2
  exit 1
}

PY="$(select_python)"
echo "==> Creating a fresh virtualenv with ${PY} ($("$PY" -V 2>&1))"
"$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet \
  -r requirements-dev.txt \
  -r workflow-runner/requirements.txt \
  -r enterprise-mcp/requirements.txt

echo "==> Running the test suite in the clone"
.venv/bin/python -m pytest -q

echo "==> Running the demo in the clone"
cp .env.example .env
./scripts/demo.sh

echo
echo "==> FRESH CLONE OK: ${CLONE_DIR}"
echo "    (delete it when you are done: rm -rf '${WORK_DIR}')"
