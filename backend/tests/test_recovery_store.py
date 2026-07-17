from __future__ import annotations

import json
import multiprocessing
import os
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO

import pytest

from app.config import Settings
from app.infrastructure.atomic_files import atomic_write_json, atomic_write_text
from app.infrastructure.recovery_store import RecoveryAction, RecoveryMarker, RecoveryStore


def _multiprocess_create_marker(
    storage_dir: str,
    attempt_id: str,
    barrier: multiprocessing.synchronize.Barrier,
) -> None:
    import app.infrastructure.recovery_store as recovery_module

    write_json = recovery_module.atomic_write_json

    def synchronized_write(path: Path, payload: object) -> None:
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        write_json(path, payload)

    recovery_module.atomic_write_json = synchronized_write
    process_settings = Settings(storage_dir=Path(storage_dir))
    store = RecoveryStore(process_settings)
    action_path = process_settings.tasks_dir / "TMP_CREATE" / "staging" / attempt_id / "upload.pdf"
    store.create_marker(
        task_id="TMP_CREATE",
        attempt_id=attempt_id,
        primary_error=f"{attempt_id} primary",
        actions=[RecoveryAction(action="unlink", path=str(action_path))],
    )


def _multiprocess_recover_marker(
    storage_dir: str,
    attempt_id: str,
    barrier: multiprocessing.synchronize.Barrier,
) -> None:
    import app.infrastructure.recovery_store as recovery_module

    write_json = recovery_module.atomic_write_json

    def synchronized_write(path: Path, payload: object) -> None:
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        write_json(path, payload)

    recovery_module.atomic_write_json = synchronized_write
    store = RecoveryStore(Settings(storage_dir=Path(storage_dir)))
    marker = store.load_marker("TMP_RECOVER", attempt_id)
    store._execute_action = lambda *_args: (_ for _ in ()).throw(OSError(f"{attempt_id} cleanup"))
    if store.recover_marker(marker):
        raise AssertionError("recovery fault injection must fail")


def _hold_task_marker_lock(
    storage_dir: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    store = RecoveryStore(Settings(storage_dir=Path(storage_dir)))
    with store._task_marker_lock("TLOCK_TIMEOUT"):
        ready.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release marker lock")


def _multiprocess_recover_same_final_marker(
    storage_dir: str,
    start_barrier: multiprocessing.synchronize.Barrier,
    token_barrier: multiprocessing.synchronize.Barrier,
) -> None:
    store = RecoveryStore(Settings(storage_dir=Path(storage_dir)))
    marker = store.load_marker("TSAME_RECOVER", "attempt-a")
    ownership_token = store.ownership_token

    def synchronized_token(path: Path, attempt_id: str) -> str:
        token = ownership_token(path, attempt_id)
        try:
            token_barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return token

    store.ownership_token = synchronized_token
    start_barrier.wait(timeout=2)
    if not store.recover_marker(marker):
        raise AssertionError("same-marker recovery must remain idempotent")


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


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_atomic_write_preserves_stream_error_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    target = tmp_path / "state.json"
    fdopen = os.fdopen

    class FailingStream(AbstractContextManager):
        def __init__(self, stream: TextIO) -> None:
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.stream.close()
            return False

        def write(self, content: str) -> int:
            if failure == "write":
                raise OSError("write-primary")
            return self.stream.write(content)

        def flush(self) -> None:
            if failure == "flush":
                raise OSError("flush-primary")
            self.stream.flush()

        def fileno(self) -> int:
            return self.stream.fileno()

    monkeypatch.setattr(os, "fdopen", lambda *args, **kwargs: FailingStream(fdopen(*args, **kwargs)))

    with pytest.raises(OSError, match=f"{failure}-primary"):
        atomic_write_text(target, "payload")

    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_atomic_write_preserves_file_fsync_error_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"
    fsync = os.fsync
    calls = 0

    def fail_first_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("file-fsync-primary")
        fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_first_fsync)

    with pytest.raises(OSError, match="file-fsync-primary"):
        atomic_write_text(target, "payload")

    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_atomic_write_temp_cleanup_error_does_not_replace_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"
    unlink = Path.unlink
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace-primary")))

    def fail_temp_cleanup(path: Path, *args, **kwargs) -> None:
        if path.name.endswith(".tmp"):
            raise OSError("cleanup-secondary")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temp_cleanup)

    with pytest.raises(OSError, match="replace-primary"):
        atomic_write_text(target, "payload")


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


def test_recovery_final_input_owner_mismatch_preserves_replacement_and_marker(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    final_path = settings.tasks_dir / "TOWNER" / "uploads" / "original_contract.pdf"
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"attempt-a")
    action = RecoveryAction(
        action="unlink",
        path=str(final_path),
        scope="final_input",
        owner_token=store.ownership_token(final_path, "attempt-a"),
    )
    marker = store.create_marker(
        task_id="TOWNER",
        attempt_id="attempt-a",
        primary_error="attempt-a failed",
        actions=[action],
    )
    final_path.unlink()
    final_path.write_bytes(b"attempt-b replacement")

    assert store.recover_marker(marker) is False
    assert final_path.read_bytes() == b"attempt-b replacement"
    updated = store.load_marker("TOWNER")
    assert updated.attempts == 1
    assert "ownership" in updated.last_error.lower()
    assert store.marker_path("TOWNER").exists()


def test_recovery_final_input_replace_during_token_check_never_deletes_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    final_path = settings.tasks_dir / "TOWNER_WINDOW" / "uploads" / "original_contract.pdf"
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"attempt-a")
    action = RecoveryAction(
        action="unlink",
        path=str(final_path),
        scope="final_input",
        owner_token=store.ownership_token(final_path, "attempt-a"),
    )
    marker = store.create_marker(
        task_id="TOWNER_WINDOW",
        attempt_id="attempt-a",
        primary_error="attempt-a failed",
        actions=[action],
    )
    ownership_token = store.ownership_token
    injected = False

    def replace_after_hash(path: Path, attempt_id: str) -> str:
        nonlocal injected
        token = ownership_token(path, attempt_id)
        if not injected:
            injected = True
            final_path.unlink(missing_ok=True)
            final_path.write_bytes(b"attempt-b replacement")
        return token

    monkeypatch.setattr(store, "ownership_token", replace_after_hash)

    assert store.recover_marker(marker) is True
    assert final_path.read_bytes() == b"attempt-b replacement"
    assert not store.marker_path("TOWNER_WINDOW").exists()


def test_recovery_owner_mismatch_and_concurrent_replacement_retains_quarantine_and_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    final_path = settings.tasks_dir / "TOWNER_CONFLICT" / "uploads" / "original_contract.pdf"
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"attempt-a")
    expected_token = store.ownership_token(final_path, "attempt-a")
    final_path.write_bytes(b"captured foreign file")
    marker = store.create_marker(
        task_id="TOWNER_CONFLICT",
        attempt_id="attempt-a",
        primary_error="attempt-a failed",
        actions=[
            RecoveryAction(
                action="unlink",
                path=str(final_path),
                scope="final_input",
                owner_token=expected_token,
            )
        ],
    )
    ownership_token = store.ownership_token
    injected = False

    def replace_after_hash(path: Path, attempt_id: str) -> str:
        nonlocal injected
        token = ownership_token(path, attempt_id)
        if not injected:
            injected = True
            final_path.write_bytes(b"new replacement")
        return token

    monkeypatch.setattr(store, "ownership_token", replace_after_hash)
    caplog.set_level("ERROR", logger="app.infrastructure.recovery_store")

    assert store.recover_marker(marker) is False
    assert final_path.read_bytes() == b"new replacement"
    quarantines = list(final_path.parent.glob("*.recovery-quarantine"))
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == b"captured foreign file"
    assert store.marker_path("TOWNER_CONFLICT").exists()
    assert "quarantine" in caplog.text.lower()

    final_path.unlink()
    retained = store.load_marker("TOWNER_CONFLICT", "attempt-a")
    assert store.recover_marker(retained) is False
    assert final_path.read_bytes() == b"captured foreign file"
    assert not quarantines[0].exists()


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


@pytest.mark.parametrize(("task_id", "attempt_id"), [("TSAFE", ".."), ("..", "attempt-1")])
def test_recovery_rejects_dot_segment_scope_escape(
    tmp_path: Path,
    task_id: str,
    attempt_id: str,
) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    if attempt_id == "..":
        escaped = settings.tasks_dir / "TSAFE" / "task.json"
    else:
        escaped = settings.storage_dir / "staging" / "attempt-1" / "outside.pdf"

    with pytest.raises(ValueError, match="恢复路径"):
        store.create_marker(
            task_id=task_id,
            attempt_id=attempt_id,
            primary_error="failed",
            actions=[RecoveryAction(action="unlink", path=str(escaped))],
        )


@pytest.mark.parametrize("target_scope", ["outside", "other_attempt"])
def test_recovery_rejects_symlink_scope_escape(tmp_path: Path, target_scope: str) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    attempt_root = settings.tasks_dir / "TSAFE" / "staging" / "attempt-1"
    attempt_root.mkdir(parents=True)
    if target_scope == "outside":
        target = tmp_path / "outside.pdf"
    else:
        target = settings.tasks_dir / "TSAFE" / "staging" / "attempt-2" / "other.pdf"
        target.parent.mkdir(parents=True)
    target.write_bytes(b"keep")
    link = attempt_root / "linked.pdf"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="恢复路径"):
        store.create_marker(
            task_id="TSAFE",
            attempt_id="attempt-1",
            primary_error="failed",
            actions=[RecoveryAction(action="unlink", path=str(link))],
        )

    assert target.read_bytes() == b"keep"


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


def test_marker_create_merges_different_attempt_without_overwriting_first(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    first_path = settings.tasks_dir / "TSHARED" / "staging" / "attempt-a" / "first.pdf"
    second_path = settings.tasks_dir / "TSHARED" / "staging" / "attempt-b" / "second.pdf"

    first = store.create_marker(
        task_id="TSHARED",
        attempt_id="attempt-a",
        primary_error="first primary",
        actions=[RecoveryAction(action="unlink", path=str(first_path))],
    )
    second = store.create_marker(
        task_id="TSHARED",
        attempt_id="attempt-b",
        primary_error="second primary",
        actions=[RecoveryAction(action="unlink", path=str(second_path))],
    )

    entries = store.load_marker_entries("TSHARED")
    assert [(entry.attempt_id, entry.primary_error) for entry in entries] == [
        (first.attempt_id, "first primary"),
        (second.attempt_id, "second primary"),
    ]


def test_marker_create_same_attempt_is_idempotent(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    path = settings.tasks_dir / "TIDEMPOTENT" / "staging" / "attempt-a" / "first.pdf"
    first = store.create_marker(
        task_id="TIDEMPOTENT",
        attempt_id="attempt-a",
        primary_error="first primary",
        actions=[RecoveryAction(action="unlink", path=str(path))],
    )

    repeated = store.create_marker(
        task_id="TIDEMPOTENT",
        attempt_id="attempt-a",
        primary_error="replacement must not win",
        actions=[],
    )

    assert repeated == first
    assert store.load_marker_entries("TIDEMPOTENT") == [first]


def test_concurrent_marker_create_preserves_every_attempt(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def create(attempt_id: str) -> None:
        try:
            path = settings.tasks_dir / "TCONCURRENT" / "staging" / attempt_id / "upload.pdf"
            barrier.wait(timeout=2)
            store.create_marker(
                task_id="TCONCURRENT",
                attempt_id=attempt_id,
                primary_error=f"{attempt_id} primary",
                actions=[RecoveryAction(action="unlink", path=str(path))],
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=create, args=(attempt,)) for attempt in ("attempt-a", "attempt-b")]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert {entry.attempt_id for entry in store.load_marker_entries("TCONCURRENT")} == {
        "attempt-a",
        "attempt-b",
    }


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and fcntl locking")
def test_multiprocess_marker_create_preserves_both_attempts(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_multiprocess_create_marker,
            args=(str(settings.storage_dir), attempt_id, barrier),
        )
        for attempt_id in ("attempt-a", "attempt-b")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0, 0]
    assert {entry.attempt_id for entry in RecoveryStore(settings).load_marker_entries("TMP_CREATE")} == {
        "attempt-a",
        "attempt-b",
    }


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and fcntl locking")
def test_multiprocess_recovery_updates_do_not_lose_an_attempt(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    for attempt_id in ("attempt-a", "attempt-b"):
        action_path = settings.tasks_dir / "TMP_RECOVER" / "staging" / attempt_id / "upload.pdf"
        store.create_marker(
            task_id="TMP_RECOVER",
            attempt_id=attempt_id,
            primary_error=f"{attempt_id} primary",
            actions=[RecoveryAction(action="unlink", path=str(action_path))],
        )
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_multiprocess_recover_marker,
            args=(str(settings.storage_dir), attempt_id, barrier),
        )
        for attempt_id in ("attempt-a", "attempt-b")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0, 0]
    entries = RecoveryStore(settings).load_marker_entries("TMP_RECOVER")
    assert {entry.attempt_id: entry.attempts for entry in entries} == {
        "attempt-a": 1,
        "attempt-b": 1,
    }


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and fcntl locking")
def test_marker_lock_timeout_is_bounded_and_identifies_lock(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_task_marker_lock,
        args=(str(settings.storage_dir), ready, release),
    )
    holder.start()
    assert ready.wait(timeout=2)
    store = RecoveryStore(settings)
    store._lock_timeout_seconds = 0.05
    delayed_release = threading.Timer(0.2, release.set)
    delayed_release.start()

    try:
        with pytest.raises(RuntimeError) as raised:
            store.load_marker_entries("TLOCK_TIMEOUT")
    finally:
        release.set()
        delayed_release.join(timeout=1)
        holder.join(timeout=2)

    assert holder.exitcode == 0
    message = str(raised.value)
    assert "TLOCK_TIMEOUT" in message
    assert str(store.recovery_dir / ".locks" / "TLOCK_TIMEOUT.lock") in message
    assert "0.05" in message


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and fcntl locking")
def test_recover_all_defers_locked_marker_without_removing_it(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    store.create_marker(
        task_id="TLOCK_TIMEOUT",
        attempt_id="attempt-a",
        primary_error="interrupted submission",
        actions=[],
    )
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_task_marker_lock,
        args=(str(settings.storage_dir), ready, release),
    )
    holder.start()
    assert ready.wait(timeout=2)
    store._lock_timeout_seconds = 0.05
    caplog.set_level("WARNING", logger="app.infrastructure.recovery_store")

    try:
        assert store.recover_all() is False
    finally:
        release.set()
        holder.join(timeout=2)

    assert holder.exitcode == 0
    assert store.marker_path("TLOCK_TIMEOUT").exists()
    assert "event=recovery_marker_deferred" in caplog.text
    assert "task_id=TLOCK_TIMEOUT" in caplog.text


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and fcntl locking")
def test_multiprocess_same_marker_final_recovery_is_idempotent(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage")
    store = RecoveryStore(settings)
    final_path = settings.tasks_dir / "TSAME_RECOVER" / "uploads" / "original_contract.pdf"
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"attempt-a")
    marker = store.create_marker(
        task_id="TSAME_RECOVER",
        attempt_id="attempt-a",
        primary_error="attempt-a failed",
        actions=[
            RecoveryAction(
                action="unlink",
                path=str(final_path),
                scope="final_input",
                owner_token=store.ownership_token(final_path, "attempt-a"),
            )
        ],
    )
    context = multiprocessing.get_context("fork")
    start_barrier = context.Barrier(2)
    token_barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_multiprocess_recover_same_final_marker,
            args=(str(settings.storage_dir), start_barrier, token_barrier),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0, 0]
    assert not final_path.exists()
    assert not store.marker_path(marker.task_id).exists()
    assert list(final_path.parent.glob("*.recovery-quarantine")) == []


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
    assert "attempt-1" in caplog.text
    assert "action=unlink" in caplog.text
    assert str(task_file) in caplog.text
    assert "cleanup" in caplog.text
    assert "marker update" in caplog.text
    assert store.marker_path("TUPDATEFAIL").exists()
