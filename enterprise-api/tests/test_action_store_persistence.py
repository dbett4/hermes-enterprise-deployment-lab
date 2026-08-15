"""Persistence tests for the optional file-backed ActionStore."""

from __future__ import annotations

import json
import multiprocessing as mp
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.store import ActionStore, IncidentActionAlreadyAppliedError


_MP_TIMEOUT_SECONDS = 30


def _action_commit_worker(
    path: str,
    idempotency_key: str,
    barrier: mp.Barrier,
    result_queue: mp.Queue,
    incident_id: str = "INC-2026-0042",
    action_id: str = "RB-PAY-GATEWAY-01-S2",
) -> None:
    from app.store import ActionStore as Store
    from app.store import IncidentActionAlreadyAppliedError as PairConflict

    store = Store(path=path)
    barrier.wait(timeout=_MP_TIMEOUT_SECONDS)
    try:
        record, created = store.commit(
            incident_id=incident_id,
            action_id=action_id,
            idempotency_key=idempotency_key,
            correlation_id=f"c-{idempotency_key}",
        )
    except PairConflict as exc:
        result_queue.put(
            {
                "key": idempotency_key,
                "created": None,
                "conflict": True,
                "record_id": exc.existing_record["record_id"],
            }
        )
        return
    result_queue.put(
        {
            "key": idempotency_key,
            "created": created,
            "conflict": False,
            "record_id": record["record_id"],
            "sequence": record["sequence"],
        }
    )


def _join_workers(workers: Sequence[Any], *, timeout: float = _MP_TIMEOUT_SECONDS) -> None:
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


def _persisted_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "record_id": "ACT-test-record",
        "incident_id": "INC-2026-0042",
        "action_id": "RB-PAY-GATEWAY-01-S2",
        "idempotency_key": "k-valid",
        "correlation_id": "c-valid",
        "note": None,
        "applied_at": "2026-08-12T00:00:00+00:00",
        "sequence": 1,
    }
    record.update(overrides)
    return record


def test_default_store_is_in_memory_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No path means nothing is written to disk."""
    monkeypatch.chdir(tmp_path)
    store = ActionStore()
    store.commit(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        idempotency_key="k-mem",
        correlation_id="c1",
    )
    assert list(tmp_path.iterdir()) == []


def test_commit_survives_new_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    first = ActionStore(path=path)
    record, created = first.commit(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        idempotency_key="k-persist",
        correlation_id="c-persist",
        note="restart proof",
    )
    assert created is True
    assert path.is_file()

    second = ActionStore(path=path)
    loaded = second.get_by_key("k-persist")
    assert loaded is not None
    assert loaded["record_id"] == record["record_id"]
    assert loaded["idempotency_key"] == "k-persist"
    assert len(second.all_records()) == 1


def test_replay_after_reload_does_not_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    first = ActionStore(path=path)
    original, _ = first.commit(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        idempotency_key="k-replay",
        correlation_id="c1",
    )

    reloaded = ActionStore(path=path)
    replayed, created = reloaded.commit(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        idempotency_key="k-replay",
        correlation_id="c2",
    )
    assert created is False
    assert replayed["record_id"] == original["record_id"]
    assert len(reloaded.all_records()) == 1


def test_reset_clears_persistent_file(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    store = ActionStore(path=path)
    store.commit(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        idempotency_key="k-reset",
        correlation_id="c1",
    )
    cleared = store.reset()
    assert cleared == 1
    assert store.all_records() == []

    reloaded = ActionStore(path=path)
    assert reloaded.all_records() == []


def test_missing_file_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist-yet.json"
    store = ActionStore(path=path)
    assert store.all_records() == []
    assert not path.exists()


def test_corrupt_file_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="ACTION_STORE"):
        ActionStore(path=path)


def test_malformed_record_missing_required_fields_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(json.dumps({"records": [{"idempotency_key": "orphan"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="ACTION_STORE.*missing required fields"):
        ActionStore(path=path)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("record_id", ""),
        ("incident_id", 42),
        ("action_id", None),
        ("idempotency_key", []),
        ("correlation_id", False),
    ],
)
def test_malformed_required_string_field_fails_closed(
    tmp_path: Path, field_name: str, invalid_value: object
) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps({"records": [_persisted_record(**{field_name: invalid_value})]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=f"ACTION_STORE.*invalid {field_name}"):
        ActionStore(path=path)


def test_malformed_applied_at_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps({"records": [_persisted_record(applied_at="not-a-timestamp")]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ACTION_STORE.*invalid applied_at"):
        ActionStore(path=path)


@pytest.mark.parametrize("invalid_sequence", [0, -1, 1.5, True, "1"])
def test_malformed_sequence_fails_closed(tmp_path: Path, invalid_sequence: object) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps({"records": [_persisted_record(sequence=invalid_sequence)]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ACTION_STORE.*invalid sequence"):
        ActionStore(path=path)


def test_malformed_note_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps({"records": [_persisted_record(note={"not": "text"})]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ACTION_STORE.*invalid note"):
        ActionStore(path=path)


def test_failed_persist_does_not_publish_phantom_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "actions.json"
    store = ActionStore(path=path)

    def fail_persist(_records: list[dict[str, object]]) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(store, "_persist_records_unlocked", fail_persist)
    with pytest.raises(OSError, match="simulated disk failure"):
        store.commit(
            incident_id="INC-2026-0042",
            action_id="RB-PAY-GATEWAY-01-S2",
            idempotency_key="k-failed-persist",
            correlation_id="c-failed-persist",
        )

    assert store.get_by_key("k-failed-persist") is None
    assert store.all_records() == []
    assert not path.exists()


def test_failed_reset_preserves_memory_and_durable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "actions.json"
    store = ActionStore(path=path)
    original, _ = store.commit(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        idempotency_key="k-reset-failure",
        correlation_id="c-reset-failure",
    )

    def fail_persist(_records: list[dict[str, object]]) -> None:
        raise OSError("simulated reset disk failure")

    monkeypatch.setattr(store, "_persist_records_unlocked", fail_persist)
    with pytest.raises(OSError, match="simulated reset disk failure"):
        store.reset()

    assert store.get_by_key("k-reset-failure") == original
    assert len(store.all_records()) == 1
    reloaded = ActionStore(path=path)
    assert reloaded.get_by_key("k-reset-failure") == original


def test_same_pair_with_different_key_raises_conflict(tmp_path: Path) -> None:
    store = ActionStore(path=tmp_path / "actions.json")
    existing, _ = store.commit(
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        idempotency_key="k-first",
        correlation_id="c-first",
    )

    with pytest.raises(IncidentActionAlreadyAppliedError) as raised:
        store.commit(
            incident_id="INC-2026-0042",
            action_id="RB-PAY-GATEWAY-01-S2",
            idempotency_key="k-different",
            correlation_id="c-different",
        )

    assert raised.value.existing_record["record_id"] == existing["record_id"]
    assert len(store.all_records()) == 1


def test_duplicate_incident_action_pair_fails_closed_on_load(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    _persisted_record(),
                    _persisted_record(
                        record_id="ACT-duplicate-pair",
                        idempotency_key="k-other",
                        sequence=2,
                    ),
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate incident/action pair"):
        ActionStore(path=path)


def test_multiprocess_distinct_keys_retain_every_record(tmp_path: Path) -> None:
    """Independent processes must not lose distinct pair commits to one file."""
    path = tmp_path / "actions.json"
    worker_count = 8
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(worker_count)
    result_queue: mp.Queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=_action_commit_worker,
            args=(
                str(path),
                f"k-mp-{index}",
                barrier,
                result_queue,
                f"INC-2026-{index:04d}",
                f"RB-ACTION-{index}",
            ),
        )
        for index in range(worker_count)
    ]
    for worker in workers:
        worker.start()
    _join_workers(workers)

    results = [result_queue.get(timeout=1) for _ in range(worker_count)]
    assert len(results) == worker_count
    assert all(item["created"] is True for item in results)

    final = ActionStore(path=path)
    records = final.all_records()
    assert len(records) == worker_count
    keys = {record["idempotency_key"] for record in records}
    assert keys == {f"k-mp-{index}" for index in range(worker_count)}
    sequences = [record["sequence"] for record in records]
    assert sorted(sequences) == list(range(1, worker_count + 1))
    assert len(set(sequences)) == worker_count
    assert len({record["record_id"] for record in records}) == worker_count


def test_multiprocess_same_pair_distinct_keys_single_record(tmp_path: Path) -> None:
    """Distinct keys cannot bypass pair uniqueness across processes."""
    path = tmp_path / "actions.json"
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(2)
    result_queue: mp.Queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=_action_commit_worker,
            args=(str(path), f"k-pair-{index}", barrier, result_queue),
        )
        for index in range(2)
    ]
    for worker in workers:
        worker.start()
    _join_workers(workers)

    outcomes = [result_queue.get(timeout=1) for _ in workers]
    assert [item["created"] for item in outcomes].count(True) == 1
    assert [item["conflict"] for item in outcomes].count(True) == 1
    assert len({item["record_id"] for item in outcomes}) == 1
    assert len(ActionStore(path=path).all_records()) == 1


def test_multiprocess_same_key_single_created_winner(tmp_path: Path) -> None:
    """Same idempotency key across processes yields one durable record."""
    path = tmp_path / "actions.json"
    worker_count = 2
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(worker_count)
    result_queue: mp.Queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=_action_commit_worker,
            args=(str(path), "k-race-same", barrier, result_queue),
        )
        for _ in range(worker_count)
    ]
    for worker in workers:
        worker.start()
    _join_workers(workers)

    outcomes = [result_queue.get(timeout=1) for _ in range(worker_count)]
    created_flags = [item["created"] for item in outcomes]
    assert created_flags.count(True) <= 1
    assert created_flags.count(True) + created_flags.count(False) == worker_count

    final = ActionStore(path=path)
    records = final.all_records()
    assert len(records) == 1
    assert records[0]["idempotency_key"] == "k-race-same"
    assert len({item["record_id"] for item in outcomes}) == 1
