#!/usr/bin/env bash
# Native OpenTelemetry trace proof: loopback OTLP/HTTP capture, W3C propagation
# across workflow-runner CLIENT spans and enterprise-api SERVER spans, and the
# bounded approval -> post-commit failure -> resume/replay event sequence.
#
# This does not start Compose and is not a collector, retention, or production
# tracing backend. Tokens, capabilities, idempotency keys, notes, and bodies
# must not appear in captured bytes or the receipt.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
PROOF_DIR="${TRACE_PROOF_DIR:-${ROOT_DIR}/.trace-proof}"
RECEIPT_PATH="${PROOF_DIR}/receipt.json"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-trace-proof.XXXXXX")"
API_PID=""
CAPTURE_PID=""

cleanup() {
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "$CAPTURE_PID" ]] && kill -0 "$CAPTURE_PID" 2>/dev/null; then
    kill "$CAPTURE_PID" 2>/dev/null || true
    wait "$CAPTURE_PID" 2>/dev/null || true
  fi
  case "$WORK_DIR" in
    /tmp/*) rm -rf -- "$WORK_DIR" ;;
    *) echo "Refusing to remove unexpected trace-proof work directory: $WORK_DIR" >&2 ;;
  esac
}
trap cleanup EXIT

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "TRACE_PROOF_FAIL: create .venv and install requirements-dev.txt first" >&2
  exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "TRACE_PROOF_FAIL: missing required command: curl" >&2
  exit 2
fi

free_port() {
  "$PYTHON_BIN" - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

API_PORT="$(free_port)"
OTLP_PORT="$(free_port)"
while [[ "$OTLP_PORT" == "$API_PORT" ]]; do
  OTLP_PORT="$(free_port)"
done
API_URL="http://127.0.0.1:${API_PORT}"
OTLP_TRACES_ENDPOINT="http://127.0.0.1:${OTLP_PORT}/v1/traces"
READ_TOKEN="${TRACE_PROOF_READ_TOKEN:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(24))')}"
WRITE_TOKEN="${TRACE_PROOF_WRITE_TOKEN:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(24))')}"

mkdir -p "$PROOF_DIR" "${WORK_DIR}/otlp"
rm -f "$RECEIPT_PATH" "$PROOF_DIR/api.log" "$PROOF_DIR/capture.log" "$PROOF_DIR/workflow.log"

"$PYTHON_BIN" "${ROOT_DIR}/scripts/otlp-capture.py" \
  --host 127.0.0.1 \
  --port "$OTLP_PORT" \
  --output-dir "${WORK_DIR}/otlp" \
  >"${PROOF_DIR}/capture.log" 2>&1 &
CAPTURE_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${OTLP_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$CAPTURE_PID" 2>/dev/null; then
    echo "TRACE_PROOF_FAIL: otlp-capture exited during startup" >&2
    exit 1
  fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:${OTLP_PORT}/healthz" >/dev/null

(
  cd "${ROOT_DIR}/enterprise-api"
  exec env \
    PYTHONPATH="${ROOT_DIR}/enterprise-api" \
    ENTERPRISE_API_TOKEN="$READ_TOKEN" \
    ENTERPRISE_API_WRITE_TOKEN="$WRITE_TOKEN" \
    ACTION_STORE_PATH="${WORK_DIR}/actions.json" \
    OTEL_TRACES_EXPORTER=otlp \
    OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:${OTLP_PORT}" \
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="$OTLP_TRACES_ENDPOINT" \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" \
      --log-level warning >"${PROOF_DIR}/api.log" 2>&1
) &
API_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "${API_URL}/readyz" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "TRACE_PROOF_FAIL: enterprise-api exited during startup" >&2
    exit 1
  fi
  sleep 0.25
done
curl -fsS "${API_URL}/readyz" >/dev/null

APPROVAL_STORE="${WORK_DIR}/approvals.json"
AUDIT_LOG="${WORK_DIR}/audit.jsonl"
SECRETS_PATH="${WORK_DIR}/forbid.json"
FLOW_PATH="${WORK_DIR}/flow.json"

PYTHONPATH="${ROOT_DIR}/workflow-runner" \
ENTERPRISE_API_TOKEN="$READ_TOKEN" \
ENTERPRISE_API_WRITE_TOKEN="$WRITE_TOKEN" \
APPROVAL_STORE_PATH="$APPROVAL_STORE" \
AUDIT_LOG_PATH="$AUDIT_LOG" \
OTEL_TRACES_EXPORTER=otlp \
OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:${OTLP_PORT}" \
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="$OTLP_TRACES_ENDPOINT" \
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
"$PYTHON_BIN" - \
  "$API_URL" \
  "$WRITE_TOKEN" \
  "$SECRETS_PATH" \
  "$FLOW_PATH" \
  "$READ_TOKEN" >"${PROOF_DIR}/workflow.log" 2>&1 <<'PY'
from pathlib import Path
import json
import sys

from workflow_runner.approval_operator import approve_request
from workflow_runner.approvals import ApprovalStore
from workflow_runner.client import EnterpriseApiClient
from workflow_runner.executor import apply_incident_action
from workflow_runner.tracing import configure_tracing, flush_tracing, shutdown_tracing

api_url, write_token, secrets_path, flow_path, read_token = sys.argv[1:]
incident_id = "INC-2026-0042"
action_id = "RB-PAY-GATEWAY-01-S2"

configure_tracing()
client = EnterpriseApiClient(base_url=api_url, token=write_token)
pending = apply_incident_action(client, incident_id, action_id)
grant, rejection = approve_request(pending["approval_id"], "trace-proof-operator@example.com")
if grant is None:
    raise SystemExit(f"approval failed: {rejection}")
failed = apply_incident_action(
    client,
    incident_id,
    action_id,
    approval_capability=grant.approval_capability,
    inject="error_after_commit",
)
replayed = apply_incident_action(
    client,
    incident_id,
    action_id,
    approval_capability=grant.approval_capability,
)
flush_tracing()
shutdown_tracing()

store = ApprovalStore()
saved = store.get(pending["approval_id"])
if saved is None:
    raise SystemExit("approval disappeared")
Path(secrets_path).write_text(
    json.dumps(
        {
            "read_token": read_token,
            "write_token": write_token,
            "capability": grant.approval_capability,
            "approval_id": pending["approval_id"],
            "idempotency_key": saved.idempotency_key,
            "note": "trace-proof-note-must-never-appear",
            "operator": "trace-proof-operator@example.com",
        }
    )
    + "\n",
    encoding="utf-8",
)
Path(flow_path).write_text(
    json.dumps(
        {
            "pending_status": pending["status"],
            "failed_status": failed["status"],
            "failed_code": (failed.get("error") or {}).get("code"),
            "replayed_status": replayed["status"],
            "replayed": replayed.get("replayed"),
        }
    )
    + "\n",
    encoding="utf-8",
)
if pending["status"] != "pending_approval":
    raise SystemExit(f"unexpected pending status: {pending['status']}")
if failed["status"] != "error" or (failed.get("error") or {}).get("code") != "upstream_5xx":
    raise SystemExit(f"unexpected failed status: {failed['status']}")
if replayed["status"] != "replayed":
    raise SystemExit(f"unexpected replayed status: {replayed['status']}")
PY

kill "$API_PID" 2>/dev/null || true
wait "$API_PID" 2>/dev/null || true
API_PID=""

"$PYTHON_BIN" - \
  "${ROOT_DIR}/scripts/trace_proof_verify.py" \
  "${WORK_DIR}/otlp" \
  "$SECRETS_PATH" \
  "$FLOW_PATH" \
  "$RECEIPT_PATH" \
  "$OTLP_TRACES_ENDPOINT" <<'PY'
from pathlib import Path
import importlib.util
import json
import sys

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

verify_path, otlp_dir, secrets_path, flow_path, receipt_path, endpoint = sys.argv[1:]
spec = importlib.util.spec_from_file_location("trace_proof_verify", verify_path)
assert spec is not None and spec.loader is not None
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

secrets = json.loads(Path(secrets_path).read_text(encoding="utf-8"))
flow = json.loads(Path(flow_path).read_text(encoding="utf-8"))
assert flow["pending_status"] == "pending_approval", flow
assert flow["failed_status"] == "error", flow
assert flow["failed_code"] == "upstream_5xx", flow
assert flow["replayed_status"] == "replayed", flow

files = sorted(Path(otlp_dir).glob("export-*.pb"))
assert files, "no OTLP protobuf payloads captured"

KIND_INTERNAL = Span.SPAN_KIND_INTERNAL
KIND_SERVER = Span.SPAN_KIND_SERVER
KIND_CLIENT = Span.SPAN_KIND_CLIENT


def attr_map(attributes) -> dict[str, str]:
    out: dict[str, str] = {}
    for attribute in attributes:
        value = attribute.value
        if value.HasField("string_value"):
            out[attribute.key] = value.string_value
        elif value.HasField("int_value"):
            out[attribute.key] = str(value.int_value)
        elif value.HasField("double_value"):
            out[attribute.key] = str(value.double_value)
        elif value.HasField("bool_value"):
            out[attribute.key] = str(value.bool_value)
    return out


captured_bytes = b"".join(path.read_bytes() for path in files)
spans = []
for path in files:
    request = ExportTraceServiceRequest()
    request.ParseFromString(path.read_bytes())
    for resource_span in request.resource_spans:
        resource = attr_map(resource_span.resource.attributes)
        service = resource.get("service.name", "")
        for scope_span in resource_span.scope_spans:
            for span in scope_span.spans:
                spans.append(
                    {
                        "service": service,
                        "name": span.name,
                        "kind": span.kind,
                        "trace_id": span.trace_id.hex(),
                        "span_id": span.span_id.hex(),
                        "parent_span_id": span.parent_span_id.hex() if span.parent_span_id else "",
                        "events": [event.name for event in span.events],
                        "attributes": attr_map(span.attributes),
                    }
                )

assert spans, "parsed zero spans from OTLP payloads"

forbidden = [
    secrets["read_token"],
    secrets["write_token"],
    secrets["capability"],
    secrets["approval_id"],
    secrets["idempotency_key"],
    secrets["note"],
    secrets["operator"],
    "Bearer ",
    "INC-2026-0042",
    "RB-PAY-GATEWAY-01-S2",
    "trace-proof-note-must-never-appear",
]
haystack = captured_bytes + json.dumps(spans).encode()
for item in forbidden:
    encoded = item.encode() if isinstance(item, str) else item
    assert encoded not in haystack, f"forbidden value leaked into traces: {item!r}"
    assert item not in json.dumps(spans)

clients = [span for span in spans if span["kind"] == KIND_CLIENT and span["service"] == "workflow-runner"]
servers = [span for span in spans if span["kind"] == KIND_SERVER and span["service"] == "enterprise-api"]
internals = [
    span
    for span in spans
    if span["kind"] == KIND_INTERNAL
    and span["service"] == "workflow-runner"
    and span["name"] == "apply_incident_action"
]
assert clients, spans
assert servers, spans
assert internals, spans

# Shared trace ID alone is insufficient; require direct CLIENT→SERVER parentage.
parent_child_links = verify.assert_parent_child_propagation(spans)
shared = sorted({link["trace_id"] for link in parent_child_links})
assert shared, parent_child_links

event_sequences = [span["events"] for span in internals]
assert ["approval.requested"] in event_sequences, event_sequences
assert [
    "approval.accepted",
    "mutation.dispatched",
    "mutation.failed_resumable",
] in event_sequences, event_sequences
assert [
    "approval.accepted",
    "mutation.dispatched",
    "mutation.replayed",
] in event_sequences, event_sequences

for span in spans:
    attrs = span["attributes"]
    assert "http.route" not in attrs or "{" in attrs["http.route"] or attrs["http.route"] in {
        "/healthz",
        "/readyz",
        "/metrics",
        "__unmatched__",
    }, attrs
    for key in attrs:
        lowered = key.lower()
        for fragment in (
            "token",
            "auth",
            "bearer",
            "capability",
            "idempotency",
            "note",
            "body",
            "incident",
            "action_id",
            "approval_id",
        ):
            assert fragment not in lowered, key

receipt = {
    "proof": "native-otlp-trace-proof",
    "result": "pass",
    "otlp": {
        "endpoint": "http://127.0.0.1/v1/traces",
        "payload_files": len(files),
        "span_count": len(spans),
    },
    "propagation": {
        "shared_trace_ids": shared,
        "parent_child_link_count": len(parent_child_links),
        "parent_child_links": parent_child_links,
        "workflow_client_spans": sorted({span["name"] for span in clients}),
        "api_server_span_names": sorted({span["name"] for span in servers}),
    },
    "events": {
        "internal_sequences": event_sequences,
        "required": [
            ["approval.requested"],
            ["approval.accepted", "mutation.dispatched", "mutation.failed_resumable"],
            ["approval.accepted", "mutation.dispatched", "mutation.replayed"],
        ],
    },
    "limits": [
        "Loopback OTLP/HTTP capture only; no collector backend or retention system.",
        "Synthetic fixture traffic only; not production tracing or customer data.",
        "SimpleSpanProcessor is lab-only and is not a production export path.",
        "No Alertmanager, pager, or external trace backend was used.",
    ],
}
# Keep the receipt free of the real loopback port and of secret material.
assert secrets["write_token"] not in json.dumps(receipt)
Path(receipt_path).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
PY

printf 'TRACE_PROOF_PASS endpoint=loopback-otlp-http events=pending,failed_resumable,replayed receipt=%s\n' \
  "$RECEIPT_PATH"
