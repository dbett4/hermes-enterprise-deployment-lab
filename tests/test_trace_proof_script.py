from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "trace-proof.sh"
CAPTURE = ROOT / "scripts" / "otlp-capture.py"
VERIFY = ROOT / "scripts" / "trace_proof_verify.py"
CANONICAL_PROOF = ROOT / "scripts" / "proof.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GITIGNORE = ROOT / ".gitignore"


def _load_verify():
    spec = importlib.util.spec_from_file_location("trace_proof_verify", VERIFY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trace_proof_is_loopback_receipted_and_fail_closed() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "scripts/trace-proof.sh must be executable"
    assert CAPTURE.is_file()
    assert VERIFY.is_file()

    script = SCRIPT.read_text(encoding="utf-8")
    capture = CAPTURE.read_text(encoding="utf-8")
    required_fragments = (
        'mktemp -d "${TMPDIR:-/tmp}/hermes-trace-proof.XXXXXX"',
        "trap cleanup EXIT",
        "scripts/otlp-capture.py",
        "scripts/trace_proof_verify.py",
        "--host 127.0.0.1",
        "OTEL_TRACES_EXPORTER=otlp",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "app.main:app --host 127.0.0.1",
        "error_after_commit",
        "flush_tracing()",
        "ExportTraceServiceRequest",
        "SPAN_KIND_CLIENT",
        "SPAN_KIND_SERVER",
        "assert_parent_child_propagation",
        "parent_child_links",
        "parent_child_link_count",
        "mutation.failed_resumable",
        "mutation.replayed",
        "approval.requested",
        "TRACE_PROOF_PASS",
        '"proof": "native-otlp-trace-proof"',
    )
    for fragment in required_fragments:
        assert fragment in script

    assert "kill \"$API_PID\"" in script
    assert "kill \"$CAPTURE_PID\"" in script
    assert "receipt.json" in script
    assert "lab-read-token" not in script
    assert "lab-write-token" not in script
    assert "0.0.0.0" not in capture
    assert "refuses non-loopback bind" in capture


def test_parent_child_verifier_passes_on_direct_link_and_fails_on_shared_id_only() -> None:
    verify = _load_verify()
    shared = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    client_id = "bbbbbbbbbbbbbbbb"
    server_id = "cccccccccccccccc"
    direct = [
        {
            "service": "workflow-runner",
            "name": "apply_incident_action",
            "kind": verify.SPAN_KIND_CLIENT,
            "trace_id": shared,
            "span_id": client_id,
            "parent_span_id": "",
        },
        {
            "service": "enterprise-api",
            "name": "POST /v1/incidents/{incident_id}/actions",
            "kind": verify.SPAN_KIND_SERVER,
            "trace_id": shared,
            "span_id": server_id,
            "parent_span_id": client_id,
        },
    ]
    links = verify.assert_parent_child_propagation(direct)
    assert len(links) == 1
    assert links[0]["trace_id"] == shared
    assert links[0]["client_span_id"] == client_id
    assert links[0]["server_span_id"] == server_id

    shared_only = [
        {
            "service": "workflow-runner",
            "name": "apply_incident_action",
            "kind": verify.SPAN_KIND_CLIENT,
            "trace_id": shared,
            "span_id": client_id,
            "parent_span_id": "",
        },
        {
            "service": "enterprise-api",
            "name": "POST /v1/incidents/{incident_id}/actions",
            "kind": verify.SPAN_KIND_SERVER,
            "trace_id": shared,
            "span_id": server_id,
            "parent_span_id": "dddddddddddddddd",
        },
    ]
    assert verify.find_parent_child_links(shared_only) == []
    with pytest.raises(AssertionError, match="direct parent"):
        verify.assert_parent_child_propagation(shared_only)


def test_trace_proof_does_not_inherit_generic_api_credentials() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'READ_TOKEN="${TRACE_PROOF_READ_TOKEN:-' in script
    assert 'WRITE_TOKEN="${TRACE_PROOF_WRITE_TOKEN:-' in script
    assert 'READ_TOKEN="${ENTERPRISE_API_TOKEN:-' not in script
    assert 'WRITE_TOKEN="${ENTERPRISE_API_WRITE_TOKEN:-' not in script


def test_canonical_proof_and_ci_execute_and_retain_trace_evidence() -> None:
    proof = CANONICAL_PROOF.read_text(encoding="utf-8")
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    gitignore = GITIGNORE.read_text(encoding="utf-8").splitlines()

    assert 'TRACE_PROOF_DIR="${ROOT_DIR}/.trace-proof"' in proof
    assert 'PYTHON_BIN="$PYTHON_BIN" ./scripts/trace-proof.sh' in proof
    assert "trace=pass" in proof

    assert "Run live native trace proof" in workflow
    assert "PYTHON_BIN=$(which python) ./scripts/trace-proof.sh" in workflow
    assert ".trace-proof/" in workflow
    assert ".trace-proof/" in gitignore


def test_ci_trace_upload_stays_fail_closed_for_hidden_artifacts() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    upload_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "actions/upload-artifact@" in str(step.get("uses", ""))
    ]
    assert upload_steps
    for step in upload_steps:
        upload = step.get("with", {})
        assert upload.get("include-hidden-files") is True, step
        assert upload.get("if-no-files-found") == "error", step

    test_upload = next(
        step
        for step in workflow["jobs"]["test"]["steps"]
        if step.get("name") == "Upload receipts"
    )
    paths = str(test_upload["with"]["path"])
    assert ".trace-proof/receipt.json" in paths
    assert ".trace-proof/api.log" in paths
    assert ".trace-proof/capture.log" in paths
    assert ".trace-proof/workflow.log" in paths
