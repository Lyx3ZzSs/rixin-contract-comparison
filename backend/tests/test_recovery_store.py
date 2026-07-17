from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from app.config import Settings
from app.infrastructure.atomic_files import atomic_write_json, atomic_write_text
from app.infrastructure.recovery_store import RecoveryAction, RecoveryMarker, RecoveryStore


def test_atomic_write_text_uses_unique_temps_and_survives_concurrent_writers(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    barrier = threading.Barrier(3)

    def write(value: str) -> None:
        barrier.wait(timeout=2)
        atomic_write_text(target, value)

    threads = [threading.Thread(target=write, args=(value,)) for value in ('{"value": 1}', '{"value": 2}')]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert json.loads(target.read_text(encoding="utf-8")) in ({"value": 1}, {"value": 2})
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


@pytest.mark.parametrize("failure", ["replace", "parent_fsync"])
def test_atomic_write_preserves_primary_error_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    target = tmp_path / "state.json"
    replace = os.replace
    fsync = os.fsync

    if failure == "replace":
        monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace-primary")))
        expected = "replace-primary"
    else:
        calls = 0

        def fail_parent_fsync(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("parent-fsync-primary")
            fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_parent_fsync)
        expected = "parent-fsync-primary"

    with pytest.raises(OSError, match=expected):
        atomic_write_text(target, "payload")

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", fsync)
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_atomic_write_json_serializes_unicode(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"message": "补偿失败"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"message": "补偿失败"}


def test_recovery_retry_success_removes_marker(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    task_file = settings.tasks_dir / "TRECOVER" / "staging" / "attempt-1" / "upload.pdf"
    task_file.parent.mkdir(parents=True)
    task_file.write_bytes(b"partial")
    marker = store.create_marker(
        task_id="TRECOVER",
        attempt_id="attempt-1",
        primary_error="upload failed",
        actions=[RecoveryAction(action="unlink", path=str(task_file))],
    )

    assert store.recover_marker(marker) is True
    assert not task_file.exists()
    assert not store.marker_path("TRECOVER").exists()


def test_recovery_retry_failure_preserves_primary_error_and_updates_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    task_file = settings.tasks_dir / "TRECOVER_FAIL" / "staging" / "attempt-1" / "upload.pdf"
    task_file.parent.mkdir(parents=True)
    task_file.write_bytes(b"partial")
    marker = store.create_marker(
        task_id="TRECOVER_FAIL",
        attempt_id="attempt-1",
        primary_error="original upload failure",
        actions=[RecoveryAction(action="unlink", path=str(task_file))],
    )
    unlink = Path.unlink

    def fail_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == task_file:
            raise OSError("cleanup unavailable")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    assert store.recover_marker(marker) is False
    updated = store.load_marker("TRECOVER_FAIL")
    assert updated.primary_error == "original upload failure"
    assert updated.attempts == 1
    assert updated.last_error == "cleanup unavailable"


def test_recovery_rejects_outside_and_traversal_actions(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"keep")

    with pytest.raises(ValueError, match="恢复路径"):
        store.create_marker(
            task_id="TSAFE",
            attempt_id="attempt-1",
            primary_error="failed",
            actions=[RecoveryAction(action="unlink", path=str(outside))],
        )

    assert outside.exists()


def test_recovery_rejects_paths_from_another_attempt_or_non_input_area(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    other_attempt = settings.tasks_dir / "TSAFE" / "staging" / "attempt-2" / "other.pdf"
    report = settings.tasks_dir / "TSAFE" / "reports" / "report.pdf"

    for path in (other_attempt, report):
        with pytest.raises(ValueError, match="恢复路径"):
            store.create_marker(
                task_id="TSAFE",
                attempt_id="attempt-1",
                primary_error="failed",
                actions=[RecoveryAction(action="unlink", path=str(path))],
            )


def test_recover_all_attempts_every_marker_after_one_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    first = settings.tasks_dir / "TFIRST" / "staging" / "attempt-1" / "first.pdf"
    second = settings.tasks_dir / "TSECOND" / "staging" / "attempt-2" / "second.pdf"
    for path in (first, second):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"partial")
    store.create_marker(
        task_id="TFIRST",
        attempt_id="attempt-1",
        primary_error="first failed",
        actions=[RecoveryAction(action="unlink", path=str(first))],
    )
    store.create_marker(
        task_id="TSECOND",
        attempt_id="attempt-2",
        primary_error="second failed",
        actions=[RecoveryAction(action="unlink", path=str(second))],
    )
    execute = store._execute_action

    def fail_first(marker, action):
        if marker.task_id == "TFIRST":
            raise OSError("first unavailable")
        execute(marker, action)

    monkeypatch.setattr(store, "_execute_action", fail_first)

    assert store.recover_all() is False
    assert first.exists()
    assert not second.exists()
    assert store.marker_path("TFIRST").exists()
    assert not store.marker_path("TSECOND").exists()


def test_ephemeral_cleanup_never_deletes_marker_owned_by_another_attempt(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    pending = settings.tasks_dir / "TSHARED" / "staging" / "pending-attempt" / "pending.pdf"
    cleanup = settings.tasks_dir / "TSHARED" / "staging" / "success-attempt" / "cleanup.pdf"
    for path in (pending, cleanup):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"partial")
    store.create_marker(
        task_id="TSHARED",
        attempt_id="pending-attempt",
        primary_error="pending failure",
        actions=[RecoveryAction(action="unlink", path=str(pending))],
    )
    ephemeral = RecoveryMarker(
        task_id="TSHARED",
        attempt_id="success-attempt",
        primary_error="post-submit cleanup",
        actions=[RecoveryAction(action="unlink", path=str(cleanup))],
    )

    assert store.cleanup_attempt(ephemeral) is True
    assert not cleanup.exists()
    assert store.load_marker("TSHARED").attempt_id == "pending-attempt"
    assert pending.exists()


def test_recovery_attempt_update_failure_logs_critical_and_keeps_primary_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    task_file = settings.tasks_dir / "TUPDATEFAIL" / "staging" / "attempt-1" / "upload.pdf"
    task_file.parent.mkdir(parents=True)
    task_file.write_bytes(b"partial")
    marker = store.create_marker(
        task_id="TUPDATEFAIL",
        attempt_id="attempt-1",
        primary_error="primary",
        actions=[RecoveryAction(action="unlink", path=str(task_file))],
    )
    monkeypatch.setattr(store, "_execute_action", lambda _marker, _action: (_ for _ in ()).throw(OSError("cleanup")))
    monkeypatch.setattr(
        "app.infrastructure.recovery_store.atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker update")),
    )
    caplog.set_level("CRITICAL", logger="app.infrastructure.recovery_store")

    assert store.recover_marker(marker) is False
    assert "TUPDATEFAIL" in caplog.text
    assert "primary" in caplog.text
    assert "marker update" in caplog.text
    assert store.marker_path("TUPDATEFAIL").exists()
