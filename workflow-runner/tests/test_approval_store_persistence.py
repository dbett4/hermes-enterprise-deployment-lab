"""Persistence fail-closed checks for the approval capability store."""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

import pytest

from workflow_runner.approvals import ApprovalStore


_MP_TIMEOUT_SECONDS = 30


def _approval_request_worker(
    path: str,
    barrier: mp.Barrier,
    result_queue: mp.Queue,
) -> None:
    from workflow_runner.approvals import ApprovalStore as Store

    store = Store(path=path)
    barrier.wait(timeout=_MP_TIMEOUT_SECONDS)
    created = store.request(incident_id="INC-2026-0042", action_id="RB-PAY-GATEWAY-01-S2")
    result_queue.put(created.approval_id)


def _approval_approve_worker(
    path: str,
    approval_id: str,
    barrier: mp.Barrier,
    result_queue: mp.Queue,
) -> None:
    from workflow_runner.approvals import ApprovalStore as Store

    store = Store(path=path)
    barrier.wait(timeout=_MP_TIMEOUT_SECONDS)
    grant, error = store.approve(approval_id, "operator@example.com")
    result_queue.put(
        {
            "has_grant": grant is not None,
            "capability": None if grant is None else grant.approval_capability,
            "error": error,
        }
    )


def _join_workers(workers: list[mp.Process], *, timeout: float = _MP_TIMEOUT_SECONDS) -> None:
    for worker in workers:
        worker.join(timeout=timeout)
    still_alive = [worker for worker in workers if worker.is_alive()]
    for worker in still_alive:
        worker.terminate()
        worker.join(timeout=5)
    if still_alive:
        raise AssertionError(f"multiprocess workers timed out: {len(still_alive)} still alive")
    failures = [worker.exitcode for worker in workers if worker.exitcode not in (0, None)]
    if failures:
        raise AssertionError(f"multiprocess workers failed with exit codes: {failures}")


def test_corrupt_approval_store_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    path.write_text("{not-json", encoding="utf-8")
    store = ApprovalStore(path=path)
    with pytest.raises(ValueError, match="APPROVAL_STORE"):
        store.request(incident_id="INC-2026-0042", action_id="RB-PAY-GATEWAY-01-S2")


def test_malformed_per_entry_shape_fails_closed_with_controlled_error(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    path.write_text(json.dumps({"apr_bad": ["not", "a", "mapping"]}), encoding="utf-8")
    store = ApprovalStore(path=path)
    with pytest.raises(ValueError, match="APPROVAL_STORE"):
        store.get("apr_bad")


def test_malformed_entry_missing_required_fields_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    path.write_text(
        json.dumps({"apr_partial": {"status": "pending", "incident_id": "INC-2026-0042"}}),
        encoding="utf-8",
    )
    store = ApprovalStore(path=path)
    with pytest.raises(ValueError, match="APPROVAL_STORE"):
        store.all_requests()


def test_malformed_required_field_type_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    path.write_text(
        json.dumps(
            {
                "apr_bad_expiry": {
                    "approval_id": "apr_bad_expiry",
                    "incident_id": "INC-2026-0042",
                    "action_id": "RB-PAY-GATEWAY-01-S2",
                    "idempotency_key": "idem-bad-expiry",
                    "requested_at": "2026-08-12T00:00:00+00:00",
                    "expires_at": 123,
                    "status": "pending",
                    "history": [],
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="APPROVAL_STORE"):
        ApprovalStore(path=path).get("apr_bad_expiry")


def test_failed_save_does_not_publish_phantom_lifecycle_or_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "approvals.json"
    store = ApprovalStore(path=path)
    created = store.request(incident_id="INC-2026-0042", action_id="RB-PAY-GATEWAY-01-S2")

    def fail_save(_data: dict) -> None:
        raise OSError("simulated approval store disk failure")

    monkeypatch.setattr(store, "_save", fail_save)
    with pytest.raises(OSError, match="simulated approval store disk failure"):
        store.approve(created.approval_id, "operator@example.com")

    reloaded = ApprovalStore(path=path).get(created.approval_id)
    assert reloaded is not None
    assert reloaded.status == "pending"
    assert reloaded.capability_hash is None
    assert reloaded.approved_by is None


def test_approval_survives_new_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    first = ApprovalStore(path=path)
    created = first.request(incident_id="INC-2026-0042", action_id="RB-PAY-GATEWAY-01-S2")

    second = ApprovalStore(path=path)
    loaded = second.get(created.approval_id)
    assert loaded is not None
    assert loaded.approval_id == created.approval_id
    assert loaded.idempotency_key == created.idempotency_key
    assert loaded.status == "pending"


def test_multiprocess_requests_retain_every_approval_id(tmp_path: Path) -> None:
    """Independent request() workers must all land in the durable store."""
    path = tmp_path / "approvals.json"
    worker_count = 10
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(worker_count)
    result_queue: mp.Queue = ctx.Queue()
    workers = [
        ctx.Process(target=_approval_request_worker, args=(str(path), barrier, result_queue))
        for _ in range(worker_count)
    ]
    for worker in workers:
        worker.start()
    _join_workers(workers)

    returned_ids = [result_queue.get(timeout=1) for _ in range(worker_count)]
    assert len(returned_ids) == worker_count
    assert len(set(returned_ids)) == worker_count

    final = ApprovalStore(path=path).all_requests()
    persisted_ids = {item.approval_id for item in final}
    assert persisted_ids == set(returned_ids)
    for approval_id in returned_ids:
        assert sum(1 for item in final if item.approval_id == approval_id) == 1


def test_multiprocess_approve_single_plaintext_grant(tmp_path: Path) -> None:
    """Concurrent approve() yields exactly one plaintext capability grant."""
    path = tmp_path / "approvals.json"
    created = ApprovalStore(path=path).request(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
    )
    worker_count = 2
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(worker_count)
    result_queue: mp.Queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=_approval_approve_worker,
            args=(str(path), created.approval_id, barrier, result_queue),
        )
        for _ in range(worker_count)
    ]
    for worker in workers:
        worker.start()
    _join_workers(workers)

    outcomes = [result_queue.get(timeout=1) for _ in range(worker_count)]
    winners = [item for item in outcomes if item["has_grant"]]
    losers = [item for item in outcomes if not item["has_grant"]]
    assert len(winners) == 1
    assert len(losers) == 1
    assert winners[0]["capability"] is not None
    assert winners[0]["capability"].startswith("cap_")
    assert losers[0]["error"] == "approval_approved"
    assert losers[0]["capability"] is None

    final = ApprovalStore(path=path).get(created.approval_id)
    assert final is not None
    assert final.status == "approved"
    assert final.capability_hash is not None
