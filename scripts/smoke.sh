#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

RECEIPT_HOST_DIR="${RECEIPT_HOST_DIR:-$ROOT_DIR/.smoke-receipts}"
RECEIPT_FILE="${RECEIPT_FILE:-workflow-receipt.json}"
RECEIPT_HOST_PATH="$RECEIPT_HOST_DIR/$RECEIPT_FILE"
RECEIPT_CONTAINER_PATH="/receipts/$RECEIPT_FILE"
mkdir -p "$RECEIPT_HOST_DIR"
rm -f "$RECEIPT_HOST_PATH"

echo "==> Checking enterprise-api health"
curl -fsS "http://localhost:8080/healthz" >/dev/null
curl -fsS "http://localhost:8080/readyz" >/dev/null

echo "==> Running workflow-runner incident intake"
podman compose run --rm --no-deps \
  -v "$RECEIPT_HOST_DIR:/receipts:rw" \
  workflow-runner \
  --incident-id "${DEFAULT_INCIDENT_ID:-INC-2026-0042}" \
  --api-url "http://enterprise-api:8080" \
  --token "${ENTERPRISE_API_TOKEN:-lab-read-token}" \
  --output "$RECEIPT_CONTAINER_PATH"

export RECEIPT_HOST_PATH
python3 - <<'PY'
import json
import os

path = os.environ["RECEIPT_HOST_PATH"]
with open(path) as fh:
    receipt = json.load(fh)

assert receipt["outcome"] == "success", receipt
assert receipt["approval_required"] is True
assert receipt["correlation_id"]
assert len(receipt["dependency_calls"]) == 2
print(json.dumps({"smoke": "passed", "receipt_path": path, "correlation_id": receipt["correlation_id"]}, indent=2))
PY
