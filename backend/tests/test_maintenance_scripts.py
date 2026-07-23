from __future__ import annotations

from pathlib import Path

from app.application.submission_recovery import SubmissionRecoveryService
from app.config import Settings
from app.infrastructure.recovery_store import RecoveryAction, RecoveryStore
from app.infrastructure.runtime_lock import ApiRuntimeLock
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.models import CompareTask
from scripts import reconcile_storage, recover_submissions


def test_recover_submissions_finalizes_committed_final_inputs(tmp_path: Path, monkeypatch) -> None:
    """The maintenance CLI must not compensate files already committed to a Task."""
    app_settings = Settings(storage_dir=tmp_path / "storage")
    recovery_store = RecoveryStore(app_settings)
    repository = LocalJsonTaskRepository(app_settings)
    task_id = "TRECOVER_SCRIPT"
    original_path = app_settings.tasks_dir / task_id / "uploads" / "original_contract.pdf"
    original_path.parent.mkdir(parents=True)
    original_path.write_bytes(b"committed original")
    recovery_store.create_marker(
        task_id=task_id,
        attempt_id="attempt-1",
        primary_error="submission interrupted",
        actions=[
            RecoveryAction(
                action="unlink",
                path=str(original_path),
                scope="final_input",
                owner_token=recovery_store.ownership_token(original_path, "attempt-1"),
            )
        ],
    )
    repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(app_settings.tasks_dir / task_id / "uploads" / "compare_contract.pdf"),
        )
    )
    monkeypatch.setattr(recover_submissions, "default_recovery_store", recovery_store)
    monkeypatch.setattr(recover_submissions, "default_task_repository", repository)

    assert recover_submissions.main() == 0
    assert original_path.exists()
    assert not recovery_store.marker_path(task_id).exists()


def test_submission_recovery_partitions_committed_and_stale_final_inputs(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    recovery_store = RecoveryStore(app_settings)
    repository = LocalJsonTaskRepository(app_settings)
    task_id = "TRECOVER_MIXED_FINALS"
    attempt_id = "attempt-1"
    uploads = app_settings.tasks_dir / task_id / "uploads"
    staging = app_settings.tasks_dir / task_id / "staging" / attempt_id
    committed_path = uploads / "original_committed.pdf"
    stale_path = uploads / "compare_stale.pdf"
    staged_path = staging / "compare_staged.pdf"
    committed_path.parent.mkdir(parents=True)
    staging.mkdir(parents=True)
    committed_path.write_bytes(b"committed original")
    stale_path.write_bytes(b"stale compare")
    staged_path.write_bytes(b"staged compare")
    recovery_store.create_marker(
        task_id=task_id,
        attempt_id=attempt_id,
        primary_error="submission interrupted",
        actions=[
            RecoveryAction(
                action="unlink",
                path=str(committed_path),
                scope="final_input",
                owner_token=recovery_store.ownership_token(committed_path, attempt_id),
            ),
            RecoveryAction(
                action="unlink",
                path=str(stale_path),
                scope="final_input",
                owner_token=recovery_store.ownership_token(stale_path, attempt_id),
            ),
            RecoveryAction(action="unlink", path=str(staged_path)),
            RecoveryAction(action="rmdir", path=str(staging)),
        ],
    )
    repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            original_pdf_path=str(committed_path),
            compare_pdf_path=str(uploads / "compare_committed.pdf"),
        )
    )

    service = SubmissionRecoveryService(recovery_store=recovery_store, repository=repository)

    assert service.recover_all() is True
    assert committed_path.read_bytes() == b"committed original"
    assert not stale_path.exists()
    assert not staging.exists()
    assert not recovery_store.marker_path(task_id).exists()
    assert service.recover_all() is True


def test_submission_recovery_partitions_both_committed_paths_across_attempts(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    recovery_store = RecoveryStore(app_settings)
    repository = LocalJsonTaskRepository(app_settings)
    task_id = "TRECOVER_MULTI_ATTEMPT"
    uploads = app_settings.tasks_dir / task_id / "uploads"
    original_path = uploads / "original_committed.pdf"
    compare_path = uploads / "compare_committed.pdf"
    uploads.mkdir(parents=True)
    original_path.write_bytes(b"committed original")
    compare_path.write_bytes(b"committed compare")
    stale_paths: list[Path] = []
    staging_paths: list[Path] = []

    for attempt_id, committed_path, stale_name in [
        ("attempt-original", original_path, "compare_stale_first.pdf"),
        ("attempt-compare", compare_path, "original_stale_second.pdf"),
    ]:
        stale_path = uploads / stale_name
        staging = app_settings.tasks_dir / task_id / "staging" / attempt_id
        staged_path = staging / "staged.pdf"
        staging.mkdir(parents=True)
        stale_path.write_bytes(attempt_id.encode())
        staged_path.write_bytes(b"staged")
        stale_paths.append(stale_path)
        staging_paths.append(staging)
        recovery_store.create_marker(
            task_id=task_id,
            attempt_id=attempt_id,
            primary_error="submission interrupted",
            actions=[
                RecoveryAction(
                    action="unlink",
                    path=str(committed_path),
                    scope="final_input",
                    owner_token=recovery_store.ownership_token(committed_path, attempt_id),
                ),
                RecoveryAction(
                    action="unlink",
                    path=str(stale_path),
                    scope="final_input",
                    owner_token=recovery_store.ownership_token(stale_path, attempt_id),
                ),
                RecoveryAction(action="unlink", path=str(staged_path)),
                RecoveryAction(action="rmdir", path=str(staging)),
            ],
        )

    repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )

    service = SubmissionRecoveryService(recovery_store=recovery_store, repository=repository)

    assert service.recover_all() is True
    assert original_path.read_bytes() == b"committed original"
    assert compare_path.read_bytes() == b"committed compare"
    assert all(not path.exists() for path in stale_paths)
    assert all(not path.exists() for path in staging_paths)
    assert not recovery_store.marker_path(task_id).exists()


def test_reconcile_storage_refuses_maintenance_while_api_runtime_holds_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    task_id = "TLOCKED_MAINTENANCE"
    job_path = app_settings.tasks_dir / task_id / "jobs" / "1.json"
    job_path.parent.mkdir(parents=True)
    original = '{"job_id":"compare:TLOCKED_MAINTENANCE:1"}'
    job_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(reconcile_storage, "settings", app_settings)
    reconciled: list[bool] = []
    monkeypatch.setattr(reconcile_storage, "reconcile_startup", lambda *args, **kwargs: reconciled.append(True))

    api_lock = ApiRuntimeLock(app_settings.storage_dir)
    api_lock.acquire()
    try:
        assert reconcile_storage.main() == 1
    finally:
        api_lock.release()

    assert reconciled == []
    assert job_path.read_text(encoding="utf-8") == original


def test_reconcile_storage_releases_maintenance_lock_after_success(tmp_path: Path, monkeypatch) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")

    class Repository:
        def resolve(self) -> None:
            return None

    class Coordinator:
        def configure_terminal_commits(self, repository, publisher) -> None:
            return None

    class Runner:
        coordinator = Coordinator()

    monkeypatch.setattr(reconcile_storage, "settings", app_settings)
    monkeypatch.setattr(reconcile_storage, "default_task_repository", Repository())
    monkeypatch.setattr(reconcile_storage, "default_task_runner", Runner())
    monkeypatch.setattr(reconcile_storage, "reconcile_startup", lambda *args, **kwargs: 0)

    assert reconcile_storage.main() == 0
    verification_lock = ApiRuntimeLock(app_settings.storage_dir)
    verification_lock.acquire()
    verification_lock.release()
