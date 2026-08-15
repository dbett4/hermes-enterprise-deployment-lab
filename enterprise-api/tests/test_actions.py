"""Contract tests for the mutating action surface."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.store import ACTION_STORE

READ = {"Authorization": "Bearer lab-read-token"}
WRITE = {"Authorization": "Bearer lab-write-token"}
INCIDENT = "INC-2026-0042"
PATH = f"/v1/incidents/{INCIDENT}/actions"


@pytest.fixture(autouse=True)
def clean_store() -> None:
    ACTION_STORE.reset()
    yield
    ACTION_STORE.reset()


def _body(action_id: str = "RB-PAY-GATEWAY-01-S2") -> dict[str, str]:
    return {"action_id": action_id, "note": "unit test"}


def test_mutation_requires_a_token(client: TestClient) -> None:
    assert client.post(PATH, json=_body()).status_code == 401


def test_read_token_cannot_mutate(client: TestClient) -> None:
    response = client.post(PATH, json=_body(), headers={**READ, "Idempotency-Key": "k1"})
    assert response.status_code == 403
    assert ACTION_STORE.all_records() == []


def test_idempotency_key_is_required(client: TestClient) -> None:
    response = client.post(PATH, json=_body(), headers=WRITE)
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]
    assert ACTION_STORE.all_records() == []


def test_write_token_creates_exactly_one_record(client: TestClient) -> None:
    first = client.post(PATH, json=_body(), headers={**WRITE, "Idempotency-Key": "k-same"})
    second = client.post(PATH, json=_body(), headers={**WRITE, "Idempotency-Key": "k-same"})

    assert first.status_code == 201
    assert first.json()["replayed"] is False
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["record"]["record_id"] == first.json()["record"]["record_id"]
    assert len(ACTION_STORE.all_records()) == 1


def test_distinct_keys_create_distinct_records(client: TestClient) -> None:
    client.post(PATH, json=_body(), headers={**WRITE, "Idempotency-Key": "k-1"})
    client.post(PATH, json=_body("RB-PAY-GATEWAY-01-S3"), headers={**WRITE, "Idempotency-Key": "k-2"})
    assert len(ACTION_STORE.all_records()) == 2


def test_direct_write_cannot_rekey_an_applied_incident_action(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    first = client.post(PATH, json=_body(), headers={**WRITE, "Idempotency-Key": "k-first"})
    conflict = client.post(
        PATH,
        json=_body(),
        headers={**WRITE, "Idempotency-Key": "k-different"},
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "incident_action_already_applied",
        "existing_record_id": first.json()["record"]["record_id"],
    }
    assert len(ACTION_STORE.all_records()) == 1
    assert '"event": "action_dedup_conflict"' in caplog.text
    assert hashlib.sha256(b"k-different").hexdigest() in caplog.text
    assert "k-different" not in caplog.text


def test_idempotency_key_cannot_replay_a_different_action(client: TestClient) -> None:
    first = client.post(PATH, json=_body(), headers={**WRITE, "Idempotency-Key": "k-bound"})
    conflict = client.post(
        PATH,
        json=_body("RB-PAY-GATEWAY-01-S3"),
        headers={**WRITE, "Idempotency-Key": "k-bound"},
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "idempotency_key_binding_conflict",
        "existing_record_id": first.json()["record"]["record_id"],
    }
    assert len(ACTION_STORE.all_records()) == 1


def test_precommit_failure_injection_persists_nothing(client: TestClient) -> None:
    response = client.post(
        f"{PATH}?inject=error", json=_body(), headers={**WRITE, "Idempotency-Key": "k-pre"}
    )
    assert response.status_code == 500
    assert ACTION_STORE.all_records() == []


def test_postcommit_failure_injection_persists_and_replays(client: TestClient) -> None:
    failed = client.post(
        f"{PATH}?inject=error_after_commit",
        json=_body(),
        headers={**WRITE, "Idempotency-Key": "k-post"},
    )
    assert failed.status_code == 500
    # The dangerous case: the side effect exists even though the caller saw a 5xx.
    assert len(ACTION_STORE.all_records()) == 1

    resumed = client.post(PATH, json=_body(), headers={**WRITE, "Idempotency-Key": "k-post"})
    assert resumed.status_code == 200
    assert resumed.json()["replayed"] is True
    assert len(ACTION_STORE.all_records()) == 1


def test_unknown_incident_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/incidents/INC-MISSING/actions",
        json=_body(),
        headers={**WRITE, "Idempotency-Key": "k-missing"},
    )
    assert response.status_code == 404


def test_actions_are_listable_with_read_scope(client: TestClient) -> None:
    client.post(PATH, json=_body(), headers={**WRITE, "Idempotency-Key": "k-list"})
    response = client.get(PATH, headers=READ)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["applied_actions"][0]["action_id"] == "RB-PAY-GATEWAY-01-S2"


def test_runbook_steps_expose_stable_step_ids(client: TestClient) -> None:
    response = client.get(f"/v1/incidents/{INCIDENT}/runbook", headers=READ)
    step_ids = [step["step_id"] for step in response.json()["steps"]]
    assert step_ids == [
        "RB-PAY-GATEWAY-01-S1",
        "RB-PAY-GATEWAY-01-S2",
        "RB-PAY-GATEWAY-01-S3",
    ]
