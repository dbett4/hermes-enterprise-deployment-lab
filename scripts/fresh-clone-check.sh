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

echo "==> Creating a fresh virtualenv"
python3 -m venv .venv
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
