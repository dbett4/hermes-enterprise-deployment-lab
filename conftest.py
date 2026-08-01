"""Repo-wide pytest configuration.

Every test gets its own audit log and approval store so no test can pass because
of state another test left behind, and so the suite never writes into the repo.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_audit_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit" / "audit.jsonl"))
    monkeypatch.setenv("APPROVAL_STORE_PATH", str(tmp_path / "audit" / "approvals.json"))
    monkeypatch.setenv("AUDIT_RUN_ID", "pytest-run")
