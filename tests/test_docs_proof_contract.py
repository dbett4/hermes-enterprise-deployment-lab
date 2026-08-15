"""Documentation contracts for container-proof vs attested runtime evidence."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROOF = ROOT / "PROOF.md"
RUNBOOK = ROOT / "docs" / "runbook.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"
SECOND_OPERATOR = ROOT / "docs" / "second-operator-protocol.md"
COMPOSE = ROOT / "compose.yaml"
API_MAIN = ROOT / "enterprise-api" / "app" / "main.py"


def _fenced_bash_blocks(markdown: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", markdown, flags=re.DOTALL)


def test_runbook_splits_self_contained_container_proof_from_manual_hermes() -> None:
    """container-proof tears itself down; Hermes/MCP on 8080 is a separate workflow."""
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for block in _fenced_bash_blocks(runbook):
        mentions_container_proof = "container-proof.sh" in block
        points_at_8080 = "ENTERPRISE_API_URL=http://127.0.0.1:8080" in block
        assert not (mentions_container_proof and points_at_8080), (
            "runbook must not chain container-proof.sh with fixed-port 8080 Hermes/MCP "
            "commands in the same bash block; the proof uses dynamic ports and tears down"
        )

    assert re.search(r"(?i)tear\s*down|teardown", runbook)
    assert re.search(r"(?i)dynamic|free host port", runbook)
    assert ".container-proof/receipt.json" in runbook

    # Manual Compose + Hermes remains documented, but not as a sequel to the proof.
    assert "mcp-smoke.sh" in runbook
    assert "hermes-tool-filter-proof.sh" in runbook
    assert re.search(
        r"(?is)manual.*(compose|Hermes)|(compose|Hermes).*manual|compose up",
        runbook,
    )


def test_readme_splits_self_contained_container_proof_from_manual_hermes() -> None:
    """README copy/paste must not target 8080 after container-proof tears down."""
    readme = README.read_text(encoding="utf-8")

    for block in _fenced_bash_blocks(readme):
        mentions_container_proof = "container-proof.sh" in block
        points_at_8080 = "ENTERPRISE_API_URL=http://127.0.0.1:8080" in block
        assert not (mentions_container_proof and points_at_8080), (
            "README must separate self-contained container-proof from fixed-port "
            "manual Compose + Hermes/MCP commands"
        )

    assert re.search(r"(?i)dynamic|free host port", readme)
    assert re.search(r"(?i)tear\s*down|teardown", readme)
    assert re.search(r"(?is)manual.*compose.*8080|compose.*8080.*manual", readme)


def test_architecture_separates_container_proof_contract_from_runtime_receipt() -> None:
    """Script/CI contract is not the same claim as a successful runtime receipt."""
    architecture = ARCHITECTURE.read_text(encoding="utf-8")

    assert ".container-proof/receipt.json" in architecture
    assert re.search(
        r"(?i)(successful|green).{0,40}(CI |Actions )?job|(CI |Actions )?job.{0,40}(successful|green|pass)",
        architecture,
    )

    assert re.search(r"(?i)contract", architecture)
    assert re.search(
        r"(?i)attested only when|evidence boundary|runtime (receipt|evidence)|attestation requires",
        architecture,
    )
    # Avoid permanent "never ran" wording that becomes false after CI eventually succeeds.
    assert not re.search(r"(?i)has never run|never been run|no CI has ever", architecture)


def test_every_fresh_clone_description_excludes_native_telemetry() -> None:
    """Fresh-clone runs pytest + demo only, not the separate native telemetry or trace proofs."""
    descriptions = {
        "README.md": next(
            line
            for line in README.read_text(encoding="utf-8").splitlines()
            if "A clean clone of HEAD" in line
        ),
        "PROOF.md": next(
            line
            for line in PROOF.read_text(encoding="utf-8").splitlines()
            if "A new checkout reproduces" in line
        ),
        "docs/architecture.md": next(
            line
            for line in ARCHITECTURE.read_text(encoding="utf-8").splitlines()
            if "| Fresh clone |" in line
        ),
        "docs/runbook.md": next(
            line
            for line in RUNBOOK.read_text(encoding="utf-8").splitlines()
            if "`./scripts/fresh-clone-check.sh`" in line
        ),
    }

    for path, description in descriptions.items():
        lowered = description.lower()
        assert "telemetry" in lowered, (
            f"{path} must explicitly say fresh-clone does not run native telemetry"
        )
        assert "trace" in lowered, (
            f"{path} must explicitly say fresh-clone does not run native traces"
        )


def test_second_operator_starts_manual_compose_stack_before_hermes_checks() -> None:
    """container-proof tears down; fixed-port Hermes checks need a new manual stack."""
    protocol = SECOND_OPERATOR.read_text(encoding="utf-8")
    container_step = protocol.split("## Step 6", 1)[1].split("## Step 7", 1)[0]
    hermes_step = protocol.split("## Step 7", 1)[1].split("## Step 8", 1)[0]

    assert re.search(r"(?i)tear(?:s|ing)?\s+.*down|teardown", container_step)
    assert "docker compose up -d --build enterprise-api prometheus" in hermes_step
    assert "docker compose down -v --remove-orphans" in hermes_step


def test_metrics_exposure_boundary_is_explicit_and_loopback_only() -> None:
    """Prometheus must scrape without a bearer token, but the lab publishes only on loopback."""
    docs = "\n".join(
        [
            ARCHITECTURE.read_text(encoding="utf-8"),
            RUNBOOK.read_text(encoding="utf-8"),
        ]
    )
    api = API_MAIN.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")

    assert '@app.get("/metrics", include_in_schema=False)' in api
    assert re.search(r"(?is)/metrics.{0,160}unauthenticated", docs)
    assert re.search(r"(?is)/metrics.{0,220}loopback", docs)
    assert '"127.0.0.1:${ENTERPRISE_API_PORT:-8080}:8080"' in compose


def test_docs_name_the_strong_native_supply_chain_and_trace_gates() -> None:
    """Recruiter-facing docs must not regress to the weaker v1 proof language."""
    live_docs = {
        "README.md": README.read_text(encoding="utf-8"),
        "PROOF.md": PROOF.read_text(encoding="utf-8"),
        "docs/architecture.md": ARCHITECTURE.read_text(encoding="utf-8"),
        "docs/runbook.md": RUNBOOK.read_text(encoding="utf-8"),
        "docs/second-operator-protocol.md": SECOND_OPERATOR.read_text(encoding="utf-8"),
    }

    architecture = live_docs["docs/architecture.md"]
    assert "actual == repository_pin == upstream_manifest" in architecture
    assert "parent_span_id == client.span_id" in architecture

    for path, text in live_docs.items():
        if path in {"README.md", "PROOF.md", "docs/architecture.md"}:
            assert "parent_span_id" in text, f"{path} must name the direct-parent gate"

    combined = "\n".join(live_docs.values())
    assert "Checksum-verified Prometheus" not in combined
    assert "checksum-verified Prometheus" not in combined


def test_langgraph_stage1_public_ci_claim_stays_bounded() -> None:
    """The exact public run attests only the synthetic exact-token proof."""
    readme = README.read_text(encoding="utf-8")
    proof = PROOF.read_text(encoding="utf-8")

    for path, text in (("README.md", readme), ("PROOF.md", proof)):
        assert "31845098855" in text, f"{path} must cite the adopting public run"
        assert "6a8c437" in text, f"{path} must bind Stage-1 to the adopting commit"
        assert "exact keyword-token overlap" in text
        assert "in-script two-document fixture" in text
        assert "No vector store" in text or "no vector store" in text
        assert "scripts/agent-workflow-proof.py" in text

        for line in text.splitlines():
            if "31637042354" in line and ("LangGraph" in line or "Stage-1" in line):
                assert "predate" in line.lower() or "does not attest" in line.lower(), (
                    f"{path} must not treat run 31637042354 as Stage-1 evidence"
                )

    assert "Local-only until a green public CI run on the adopting commit" in proof
    assert "run `31845098855` predates the provenance hardening" in proof
    assert "does not attest it" in proof
