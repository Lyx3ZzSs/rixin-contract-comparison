from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import json
import math
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote

import fitz
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import api_schemas
from app.api_errors import http_error
from app.api_presenters import compare_task_response, task_execution_response
import app.application.compare_tasks as compare_tasks_module
from app.application.compare_tasks import CompareTaskApplication, default_compare_task_application
from app.application.submission_recovery import SubmissionRecoveryService
from app.config import settings
from app.errors import TaskStaleLeaseError, TaskTransitionConflict
from app.infrastructure.artifact_store import ArtifactPublishCommittedError, LocalArtifactStore
from app.infrastructure.recovery_store import RecoveryAction, RecoveryMarkerLockTimeout, RecoveryStore
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.infrastructure.task_runner import (
    LocalJsonTaskJobRepository,
    QueuedTaskRunner,
    TaskJob,
    default_task_runner,
)
from app.main import app
from app.models import (
    AuditItemReview,
    BBox,
    CompareTask,
    CompareOptions,
    DiffItem,
    EvidenceBox,
    NormalizedBBox,
    OcrRemediationAction,
    PageOcrQualityProfile,
    TaskOcrRemediationSummary,
    TaskOcrQualitySummary,
)
from app.services.review_service import CompareQualityService, CompareReviewService
from app.utils.file_utils import FileValidationError
from app.utils.json_utils import load_task, save_task as persist_task, task_json_path, to_jsonable

from auth_helpers import ADMIN

try:
    import fcntl
except ImportError:  # pragma: no cover - recovery explicitly requires POSIX fcntl
    fcntl = None


def _owned_task(task: CompareTask) -> CompareTask:
    if task.owner_sub:
        return task
    return task.model_copy(
        update={
            "owner_sub": ADMIN.sub,
            "owner_username": ADMIN.preferred_username,
            "owner_display_name": ADMIN.display_name,
        }
    )


def save_task(task: CompareTask):
    return persist_task(_owned_task(task))


@pytest.fixture
def stop_default_runner_after_test():
    try:
        yield
    finally:
        default_task_runner.stop(wait=True)


def make_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    y = height - 72
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def configure_storage(tmp_path: Path) -> None:
    settings.storage_dir = tmp_path / "storage"
    settings.uploads_dir = settings.storage_dir / "uploads"
    settings.tasks_dir = settings.storage_dir / "tasks"
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.debug_dir = settings.storage_dir / "debug"
    settings.document_extractor = "auto"
    settings.compare_document_extractor = "auto"
    settings.compare_require_structured_ocr = False
    settings.ensure_storage()


class ApiRecordingReportGenerator:
    def __init__(
        self,
        *,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.entered = entered
        self.release = release
        self.calls: list[tuple[int, Path]] = []
        self._lock = threading.Lock()

    def generate(self, task: CompareTask, output_path: str | Path) -> Path:
        path = Path(output_path)
        with self._lock:
            self.calls.append((task.report_revision, path))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(timeout=5)
        path.write_bytes(f"%PDF-api-revision-{task.report_revision}".encode())
        return path


def _submission_application(
    tmp_path: Path,
) -> tuple[CompareTaskApplication, LocalJsonTaskRepository, QueuedTaskRunner, LocalArtifactStore, RecoveryStore]:
    configure_storage(tmp_path)
    repository = LocalJsonTaskRepository(settings)
    runner = QueuedTaskRunner(app_settings=settings, autostart=False)
    artifact_store = LocalArtifactStore(settings)
    recovery_store = RecoveryStore(settings)
    application = CompareTaskApplication(
        repository=repository,
        runner=runner,
        artifact_store=artifact_store,
        recovery_store=recovery_store,
    )
    return application, repository, runner, artifact_store, recovery_store


def _pdf_upload(name: str = "contract.pdf") -> UploadFile:
    pdf = fitz.open()
    pdf.new_page()
    try:
        content = pdf.tobytes()
    finally:
        pdf.close()
    return UploadFile(filename=name, file=io.BytesIO(content))


@contextlib.contextmanager
def _hold_recovery_marker_lock(store: RecoveryStore, task_id: str):
    if fcntl is None:
        pytest.skip("requires POSIX fcntl")
    lock_path = store.recovery_dir / ".locks" / f"{task_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield lock_path
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _hold_recovery_marker_lock_in_process(
    storage_dir: str,
    task_id: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    from app.config import Settings

    store = RecoveryStore(Settings(storage_dir=Path(storage_dir)))
    with store._task_marker_lock(task_id):
        ready.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release recovery marker lock")


def test_lifespan_recovers_submissions_before_reconciliation_and_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_storage(tmp_path)
    main_module = importlib.import_module("app.main")
    events: list[str] = []
    monkeypatch.setattr(main_module.default_task_repository, "resolve", lambda: events.append("repository"))
    monkeypatch.setattr(
        main_module.default_recovery_store,
        "recover_all",
        lambda *_args: events.append("recovery") or True,
    )
    monkeypatch.setattr(
        main_module,
        "reconcile_startup",
        lambda *_args, **_kwargs: events.append("reconciliation") or 0,
    )
    monkeypatch.setattr(main_module.default_task_runner, "start", lambda: events.append("workers"))
    monkeypatch.setattr(main_module.default_task_runner, "stop", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "register_default_models", lambda: None)
    monkeypatch.setattr(main_module, "teardown_models", lambda: None)
    monkeypatch.setattr(main_module, "close_clients", lambda: None)
    monkeypatch.setattr(main_module.auth_runtime, "prewarm", lambda: None)
    monkeypatch.setattr(main_module.auth_runtime, "close", lambda: None)

    with TestClient(main_module.app) as client:
        assert client.get("/health").status_code == 200

    assert events[:4] == ["repository", "recovery", "reconciliation", "workers"]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and fcntl locking")
def test_lifespan_defers_locked_recovery_marker_and_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_storage(tmp_path)
    main_module = importlib.import_module("app.main")
    recovery_store = main_module.default_recovery_store
    recovery_store.create_marker(
        task_id="TSTARTUP_LOCKED_MARKER",
        attempt_id="attempt-a",
        primary_error="crashed after upload publish",
        actions=[],
    )
    recovery_store._lock_timeout_seconds = 0.05
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_recovery_marker_lock_in_process,
        args=(str(settings.storage_dir), "TSTARTUP_LOCKED_MARKER", ready, release),
    )
    holder.start()
    assert ready.wait(timeout=2)
    monkeypatch.setattr(main_module.default_task_repository, "resolve", lambda: None)
    monkeypatch.setattr(main_module.default_task_runner, "start", lambda: None)
    monkeypatch.setattr(main_module.default_task_runner, "stop", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "reconcile_startup", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(main_module, "register_default_models", lambda: None)
    monkeypatch.setattr(main_module, "teardown_models", lambda: None)
    monkeypatch.setattr(main_module, "close_clients", lambda: None)
    monkeypatch.setattr(main_module.auth_runtime, "prewarm", lambda: None)
    monkeypatch.setattr(main_module.auth_runtime, "close", lambda: None)
    caplog.set_level("WARNING", logger="app.infrastructure.recovery_store")

    try:
        with TestClient(main_module.app) as client:
            assert client.get("/health").status_code == 200
    finally:
        release.set()
        holder.join(timeout=2)

    assert holder.exitcode == 0
    assert recovery_store.marker_path("TSTARTUP_LOCKED_MARKER").exists()
    assert "event=recovery_marker_deferred" in caplog.text
    assert "task_id=TSTARTUP_LOCKED_MARKER" in caplog.text


def failed_compare_job(runner: QueuedTaskRunner, task_id: str) -> TaskJob:
    return runner.coordinator._persist_then_replace(
        TaskJob(
            job_id=f"compare:{task_id}:1",
            task_id=task_id,
            task_type="compare",
            status="FAILED",
            execution_no=1,
            payload={"task_id": task_id},
            max_attempts=1,
            attempt=1,
            last_error="failed",
        )
    )


def wait_for_compare_task(client: TestClient, task_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/compare/{task_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] != "PROCESSING":
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Compare task did not finish: {task_id}")


def test_api_compare_contracts(tmp_path: Path, stop_default_runner_after_test) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(
        original,
        [
            "1. Payment",
            "Buyer shall pay 1000 USD within 30 days after acceptance.",
            "Seller shall deliver two signed invoices.",
        ],
    )
    make_pdf(
        compare,
        [
            "1. Payment",
            "Buyer shall pay 1200 USD within 45 days after final acceptance.",
            "Seller shall deliver two signed invoices.",
        ],
    )

    client = TestClient(app)
    with original.open("rb") as original_file, compare.open("rb") as compare_file:
        response = client.post(
            "/api/compare",
            files={
                "original_file": ("original.pdf", original_file, "application/pdf"),
                "compare_file": ("compare.pdf", compare_file, "application/pdf"),
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    task_id = payload["task_id"]
    assert payload["status"] == "PROCESSING"
    assert payload["stage"] == "排队中"
    assert payload["progress_percent"] == 3
    assert payload["diff_count"] == 0
    assert "preview_url" not in payload
    assert payload["original_pdf_url"] == f"/api/compare/{task_id}/original"
    assert payload["compare_pdf_url"] == f"/api/compare/{task_id}/compare"
    assert payload["report_url"] == ""
    assert payload["report_filename"].endswith("差异分析报告.pdf")
    assert payload["original_highlight_pdf_url"] == ""
    assert "schema_version" not in payload
    assert payload["terminal_reason"] == "NONE"
    assert payload["revision"] == 0
    assert payload["report_revision"] == 0
    assert "original_pdf_path" not in payload
    assert "compare_pdf_path" not in payload

    task_payload = wait_for_compare_task(client, task_id)
    assert task_payload["status"] == "COMPLETED"
    assert task_payload["stage"] == "已完成"
    assert task_payload["progress_percent"] == 100
    assert task_payload["diff_count"] >= 1
    assert task_payload["extractor_used"] == "pymupdf"
    assert task_payload["document_profiles"]["original"]["recommended_strategy"] == "text"
    assert "document_profiles" in task_payload["debug_artifact_paths"]

    task_response = client.get(f"/api/compare/{task_id}")
    assert task_response.status_code == 200
    assert "preview_url" not in task_response.json()
    assert task_response.json()["extractor_used"] == "pymupdf"
    assert task_response.json()["original_pdf_url"] == f"/api/compare/{task_id}/original"
    assert task_response.json()["compare_pdf_url"] == f"/api/compare/{task_id}/compare"
    assert task_response.json()["report_url"] == f"/api/compare/{task_id}/report"
    assert task_response.json()["original_highlight_pdf_url"] == ""
    assert task_response.json()["compare_highlight_pdf_url"] == ""
    assert "diffs" not in task_response.json()
    assert "report_pdf_path" not in task_response.json()
    assert "ocr_raw_result_path" not in task_response.json()

    diffs_response = client.get(f"/api/compare/{task_id}/diffs")
    assert diffs_response.status_code == 200
    first_diff = diffs_response.json()["diffs"][0]
    assert "original_screenshot_url" not in first_diff
    assert "compare_screenshot_url" not in first_diff
    assert "original_screenshot" not in first_diff
    assert "compare_screenshot" not in first_diff
    assert "original_evidence" in first_diff
    assert "compare_evidence" in first_diff
    assert first_diff["original_evidence"][0]["method"] == "char_exact"
    assert first_diff["compare_evidence"][0]["method"] == "char_exact"
    assert first_diff["original_evidence"][0]["confidence"] == 0.98
    assert first_diff["original_evidence"][0]["evidence_quality"] == "HIGH"
    assert "ai_analysis" not in first_diff
    report_response = client.get(f"/api/compare/{task_id}/report")
    assert report_response.status_code == 200
    assert not report_response.headers["content-disposition"].lower().startswith("inline")
    assert "差异分析报告.pdf" in unquote(report_response.headers["content-disposition"])
    refreshed_task = client.get(f"/api/compare/{task_id}").json()
    assert refreshed_task["report_filename"].endswith("差异分析报告.pdf")
    assert "original_page_screenshots" not in refreshed_task
    assert "compare_page_screenshots" not in refreshed_task
    refreshed_diff = client.get(f"/api/compare/{task_id}/diffs").json()["diffs"][0]
    assert "ai_analysis" not in refreshed_diff
    original_preview_response = client.get(f"/api/compare/{task_id}/original")
    compare_preview_response = client.get(f"/api/compare/{task_id}/compare")
    assert original_preview_response.status_code == 200
    assert compare_preview_response.status_code == 200
    assert "application/pdf" in original_preview_response.headers["content-type"]
    assert "application/pdf" in compare_preview_response.headers["content-type"]
    assert original_preview_response.headers["content-disposition"].lower().startswith("inline")
    assert compare_preview_response.headers["content-disposition"].lower().startswith("inline")
    assert client.get(f"/api/compare/{task_id}/highlight/original").status_code == 404
    assert client.get(f"/api/compare/{task_id}/highlight/compare").status_code == 404
    assert client.get(f"/api/compare/{task_id}/screenshot/example.png").status_code == 404
    assert client.get("/api/compare/missing-task/original").status_code == 404
    assert client.get(f"/api/compare/{task_id}/preview").status_code == 404


def test_submission_validates_both_staged_files_before_publishing_either(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, artifact_store, _recovery_store = _submission_application(tmp_path)
    published: list[Path] = []
    publish = artifact_store.publish_staged
    validate = __import__("app.application.compare_tasks", fromlist=["validate_pdf_path"]).validate_pdf_path

    def fail_second(path: Path, filename: str) -> None:
        if "compare" in path.name:
            raise FileValidationError("second validation failed")
        validate(path, filename)

    monkeypatch.setattr("app.application.compare_tasks.validate_pdf_path", fail_second)
    monkeypatch.setattr(
        artifact_store,
        "publish_staged",
        lambda source, destination, **kwargs: (
            published.append(destination),
            publish(source, destination, **kwargs),
        )[1],
    )

    with pytest.raises(FileValidationError, match="second validation failed"):
        asyncio.run(
            application.submit_uploads(
                task_id="TVALIDATE_BOTH",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert published == []
    assert list((settings.tasks_dir / "TVALIDATE_BOTH").rglob("*.pdf")) == []


@pytest.mark.parametrize("failure_index", [1, 2])
def test_submission_publish_failure_compensates_only_current_attempt_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    application, _repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    publish = artifact_store.publish_staged
    calls = 0

    def fail_publish(source: Path, destination: Path, **kwargs) -> Path:
        nonlocal calls
        calls += 1
        if calls == failure_index:
            raise OSError(f"publish {failure_index} failed")
        return publish(source, destination, **kwargs)

    monkeypatch.setattr(artifact_store, "publish_staged", fail_publish)

    with pytest.raises(OSError, match=f"publish {failure_index} failed"):
        asyncio.run(
            application.submit_uploads(
                task_id=f"TPUBLISH_{failure_index}",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    task_root = artifact_store.task_root(f"TPUBLISH_{failure_index}")
    assert list(task_root.rglob("*.pdf")) == []
    assert not recovery_store.marker_path(f"TPUBLISH_{failure_index}").exists()


def test_submission_publish_post_commit_failure_does_not_orphan_final_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    publish = artifact_store.publish_staged
    calls = 0
    captured_actions = []
    create_marker = recovery_store.create_marker

    def publish_then_raise(source: Path, destination: Path, **kwargs) -> Path:
        nonlocal calls
        calls += 1
        published = publish(source, destination, **kwargs)
        if calls == 1:
            raise OSError("publish failed after destination commit")
        return published

    def record_marker(**kwargs):
        captured_actions.extend(kwargs["actions"])
        return create_marker(**kwargs)

    monkeypatch.setattr(artifact_store, "publish_staged", publish_then_raise)
    monkeypatch.setattr(recovery_store, "create_marker", record_marker)

    with pytest.raises(OSError, match="after destination commit"):
        asyncio.run(
            application.submit_uploads(
                task_id="TPUBLISH_POST_COMMIT",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert list(artifact_store.task_root("TPUBLISH_POST_COMMIT").rglob("*.pdf")) == []


@pytest.mark.parametrize(
    ("failure_index", "write_partial"),
    [
        (1, True),
        (1, False),
        (2, True),
        (2, False),
    ],
    ids=(
        "original-partial",
        "original-complete",
        "compare-partial",
        "compare-complete",
    ),
)
def test_submission_recovery_removes_preupload_staging_after_stream_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
    write_partial: bool,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    task_id = f"TPREUPLOAD_{failure_index}_{'PARTIAL' if write_partial else 'COMPLETE'}"
    stream = compare_tasks_module.stream_upload_to_path
    calls = 0

    async def stream_then_terminate(upload: UploadFile, destination: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls != failure_index:
            return await stream(upload, destination)
        if write_partial:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"%PDF partial upload")
        else:
            await stream(upload, destination)
        raise SystemExit("simulated process termination during upload staging")

    monkeypatch.setattr(compare_tasks_module, "stream_upload_to_path", stream_then_terminate)

    with pytest.raises(SystemExit, match="during upload staging"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    journal = recovery_store.load_marker(task_id)
    attempt_dir = artifact_store.task_root(task_id) / "staging" / journal.attempt_id
    live_artifact = artifact_store.task_root(task_id) / "uploads" / "original_committed.pdf"
    assert not (artifact_store.task_root(task_id) / "uploads").exists()
    live_artifact.parent.mkdir(parents=True, exist_ok=True)
    live_artifact.write_bytes(b"committed artifact")
    assert {action.scope for action in journal.actions} == {"attempt"}
    assert {Path(action.path).name for action in journal.actions} == {
        "original_original.pdf",
        "compare_compare.pdf",
        journal.attempt_id,
    }
    assert attempt_dir.exists()

    restarted_store = RecoveryStore(recovery_store.settings)
    restarted_service = SubmissionRecoveryService(recovery_store=restarted_store, repository=repository)
    assert restarted_service.recover_all() is True
    assert not attempt_dir.exists()
    assert live_artifact.read_bytes() == b"committed artifact"
    assert not restarted_store.marker_path(task_id).exists()


def test_submission_recovery_removes_staging_after_crash_before_first_final_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    task_id = "TPUBLISH_JOURNAL_CRASH_BOUNDARY"

    def terminate_before_final_hardlink(*_args, **_kwargs) -> Path:
        raise SystemExit("simulated process termination after durable journal write")

    monkeypatch.setattr(artifact_store, "publish_staged", terminate_before_final_hardlink)

    with pytest.raises(SystemExit, match="after durable journal write"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    journal = recovery_store.load_marker(task_id)
    attempt_dir = artifact_store.task_root(task_id) / "staging" / journal.attempt_id
    assert len(list(attempt_dir.glob("*.pdf"))) == 2
    assert not list((artifact_store.task_root(task_id) / "uploads").glob("*.pdf"))

    restarted_store = RecoveryStore(recovery_store.settings)
    restarted_service = SubmissionRecoveryService(recovery_store=restarted_store, repository=repository)
    assert restarted_service.recover_all() is True
    assert not list((artifact_store.task_root(task_id) / "uploads").glob("*.pdf"))
    assert not attempt_dir.exists()
    assert not restarted_store.marker_path(task_id).exists()


def test_submission_recovery_removes_final_and_staging_after_crash_before_staged_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    task_id = "TPUBLISH_HARDLINK_CRASH_BOUNDARY"
    unlink = Path.unlink

    def terminate_on_staged_unlink(path: Path, *args, **kwargs) -> None:
        if path.parent.parent.name == "staging":
            raise SystemExit("simulated process termination after final hardlink")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", terminate_on_staged_unlink)

    with pytest.raises(SystemExit, match="after final hardlink"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    journal = recovery_store.load_marker(task_id)
    attempt_dir = artifact_store.task_root(task_id) / "staging" / journal.attempt_id
    assert len(list((artifact_store.task_root(task_id) / "uploads").glob("*.pdf"))) == 1
    assert len(list(attempt_dir.glob("*.pdf"))) == 2
    monkeypatch.undo()

    restarted_store = RecoveryStore(recovery_store.settings)
    restarted_service = SubmissionRecoveryService(recovery_store=restarted_store, repository=repository)
    assert restarted_service.recover_all() is True
    assert not list((artifact_store.task_root(task_id) / "uploads").glob("*.pdf"))
    assert not attempt_dir.exists()
    assert not restarted_store.marker_path(task_id).exists()


def test_submission_recovery_keeps_committed_final_inputs_and_removes_staging_after_cleanup_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    task_id = "TPUBLISH_COMMITTED_STAGING_CRASH"
    unlink = Path.unlink

    def fail_staged_unlink(path: Path, *args, **kwargs) -> None:
        if path.parent.parent.name == "staging":
            raise OSError("staging unlink failed")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)
    monkeypatch.setattr(
        recovery_store,
        "cleanup_attempt",
        lambda _marker: (_ for _ in ()).throw(SystemExit("simulated crash after task persistence")),
    )

    with pytest.raises(SystemExit, match="after task persistence"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )
    monkeypatch.undo()

    task = repository.load_compare_task(task_id)
    journal = recovery_store.load_marker(task_id)
    attempt_dir = artifact_store.task_root(task_id) / "staging" / journal.attempt_id
    assert Path(task.original_pdf_path).is_file()
    assert Path(task.compare_pdf_path).is_file()
    assert len(list(attempt_dir.glob("*.pdf"))) == 2

    restarted_service = SubmissionRecoveryService(recovery_store=recovery_store, repository=repository)
    assert restarted_service.recover_all() is True
    assert Path(task.original_pdf_path).is_file()
    assert Path(task.compare_pdf_path).is_file()
    assert not attempt_dir.exists()
    assert not recovery_store.marker_path(task_id).exists()


def test_startup_recovery_keeps_final_inputs_after_task_commit_before_journal_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    task_id = "TPUBLISH_COMMITTED_BOUNDARY"
    monkeypatch.setattr(
        recovery_store,
        "finalize_final_inputs",
        lambda _marker: (_ for _ in ()).throw(SystemExit("simulated crash after task commit")),
    )

    with pytest.raises(SystemExit, match="after task commit"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )
    monkeypatch.undo()

    task = repository.load_compare_task(task_id)
    assert Path(task.original_pdf_path).is_file()
    assert Path(task.compare_pdf_path).is_file()
    assert recovery_store.marker_path(task_id).exists()

    restarted_service = SubmissionRecoveryService(recovery_store=recovery_store, repository=repository)
    assert restarted_service.recover_all() is True
    assert Path(task.original_pdf_path).is_file()
    assert Path(task.compare_pdf_path).is_file()
    assert not recovery_store.marker_path(task_id).exists()


def test_submission_precomputes_owner_token_before_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    publish = artifact_store.publish_staged
    publish_calls = 0

    def record_publish(*args, **kwargs):
        nonlocal publish_calls
        publish_calls += 1
        return publish(*args, **kwargs)

    monkeypatch.setattr(artifact_store, "publish_staged", record_publish)
    monkeypatch.setattr(
        recovery_store,
        "ownership_token",
        lambda *_args: (_ for _ in ()).throw(OSError("token precompute failed")),
    )

    with pytest.raises(OSError, match="token precompute failed"):
        asyncio.run(
            application.submit_uploads(
                task_id="TTOKEN_PRECOMPUTE",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert publish_calls == 0
    assert list((artifact_store.task_root("TTOKEN_PRECOMPUTE") / "uploads").glob("*.pdf")) == []


def test_submission_committed_publish_error_registers_typed_destination_before_compensation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    publish = artifact_store.publish_staged
    captured_actions: list[RecoveryAction] = []
    create_marker = recovery_store.create_marker
    unlink = Path.unlink

    def callback_then_rollback_failure(source: Path, destination: Path, **kwargs) -> Path:
        kwargs["on_created"] = lambda _path: (_ for _ in ()).throw(OSError("callback primary"))
        return publish(source, destination, **kwargs)

    def fail_upload_rollback(path: Path, *args, **kwargs) -> None:
        if path.parent.name == "uploads":
            raise OSError("rollback secondary")
        unlink(path, *args, **kwargs)

    def record_marker(**kwargs):
        captured_actions.extend(kwargs["actions"])
        return create_marker(**kwargs)

    monkeypatch.setattr(artifact_store, "publish_staged", callback_then_rollback_failure)
    monkeypatch.setattr(Path, "unlink", fail_upload_rollback)
    monkeypatch.setattr(recovery_store, "create_marker", record_marker)

    with pytest.raises(ArtifactPublishCommittedError) as raised:
        asyncio.run(
            application.submit_uploads(
                task_id="TPUBLISH_TYPED_COMMITTED",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert raised.value.owner_token
    assert any(
        action.scope == "final_input"
        and Path(action.path) == raised.value.destination
        and action.owner_token == raised.value.owner_token
        for action in captured_actions
    )
    assert recovery_store.marker_path("TPUBLISH_TYPED_COMMITTED").exists()


def test_submission_publish_file_exists_race_never_claims_or_deletes_other_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    publish = artifact_store.publish_staged
    captured_actions = []
    create_marker = recovery_store.create_marker
    raced_destination: Path | None = None

    def race_publish(source: Path, destination: Path, **kwargs) -> Path:
        nonlocal raced_destination
        raced_destination = destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"other attempt")
        return publish(source, destination, **kwargs)

    def record_marker(**kwargs):
        captured_actions.extend(kwargs["actions"])
        return create_marker(**kwargs)

    monkeypatch.setattr(artifact_store, "publish_staged", race_publish)
    monkeypatch.setattr(recovery_store, "create_marker", record_marker)

    with pytest.raises(FileExistsError):
        asyncio.run(
            application.submit_uploads(
                task_id="TPUBLISH_RACE",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert raced_destination is not None
    assert raced_destination.read_bytes() == b"other attempt"
    assert all(action.scope != "final_input" for action in captured_actions)


def test_submission_task_save_failure_removes_published_inputs_and_records_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    marker_writes: list[list[str]] = []
    create_marker = recovery_store.create_marker

    def record_marker(**kwargs):
        marker_writes.append([action.path for action in kwargs["actions"]])
        return create_marker(**kwargs)

    monkeypatch.setattr(
        repository, "save_compare_task", lambda _task: (_ for _ in ()).throw(OSError("task save failed"))
    )
    monkeypatch.setattr(recovery_store, "create_marker", record_marker)

    with pytest.raises(OSError, match="task save failed"):
        asyncio.run(
            application.submit_uploads(
                task_id="TTASK_SAVE_FAIL",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert list(artifact_store.task_root("TTASK_SAVE_FAIL").rglob("*.pdf")) == []
    assert marker_writes
    assert any("uploads" in path for path in marker_writes[0])
    assert not recovery_store.marker_path("TTASK_SAVE_FAIL").exists()


def test_submission_task_save_post_commit_failure_preserves_inputs_and_marks_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    save = repository.save_compare_task
    marker_actions: list[list[RecoveryAction]] = []
    create_marker = recovery_store.create_marker

    def save_then_raise(task: CompareTask):
        save(task)
        raise OSError("task manifest failed after commit")

    def record_marker(**kwargs):
        marker_actions.append(kwargs["actions"])
        return create_marker(**kwargs)

    monkeypatch.setattr(repository, "save_compare_task", save_then_raise)
    monkeypatch.setattr(recovery_store, "create_marker", record_marker)
    task_id = "TTASK_POST_COMMIT_FAIL"

    with pytest.raises(OSError, match="after commit"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    task = repository.load_compare_task(task_id)
    assert (task.status, task.terminal_reason) == ("FAILED", "SUBMISSION_FAILED")
    assert Path(task.original_pdf_path).is_file()
    assert Path(task.compare_pdf_path).is_file()
    assert marker_actions
    assert all(action.scope == "attempt" for action in marker_actions[0])
    assert list((artifact_store.task_root(task_id) / "staging").rglob("*.pdf")) == []


def test_submission_task_commit_read_error_conservatively_preserves_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application, repository, _runner, artifact_store, _recovery_store = _submission_application(tmp_path)
    save = repository.save_compare_task
    load = repository.load_compare_task
    reads = 0

    def save_then_raise(task: CompareTask):
        save(task)
        raise OSError("task save post-commit error")

    def fail_first_read(task_id: str):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise OSError("authoritative read unavailable")
        return load(task_id)

    monkeypatch.setattr(repository, "save_compare_task", save_then_raise)
    monkeypatch.setattr(repository, "load_compare_task", fail_first_read)
    caplog.set_level("CRITICAL", logger="app.application.compare_tasks")
    task_id = "TTASK_COMMIT_READ_FAIL"

    with pytest.raises(OSError, match="post-commit"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    task = load(task_id)
    assert (task.status, task.terminal_reason) == ("FAILED", "SUBMISSION_FAILED")
    assert Path(task.original_pdf_path).is_file() and Path(task.compare_pdf_path).is_file()
    assert list((artifact_store.task_root(task_id) / "staging").rglob("*.pdf")) == []
    assert task_id in caplog.text
    assert "authoritative read unavailable" in caplog.text


def test_submission_overflow_ledger_keeps_predeclared_second_file_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    captured_actions: list[RecoveryAction] = []
    create_marker = recovery_store.create_marker

    def record_marker(**kwargs):
        captured_actions.extend(kwargs["actions"])
        return create_marker(**kwargs)

    monkeypatch.setattr(recovery_store, "create_marker", record_marker)
    oversized = UploadFile(filename="original.pdf", file=io.BytesIO(b"%PDF" + b"x" * (2 * 1024 * 1024)))

    with pytest.raises(FileValidationError, match="超过 1MB"):
        asyncio.run(
            application.submit_uploads(
                task_id="TOVERFLOW_LEDGER",
                original_file=oversized,
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert captured_actions
    assert any(Path(action.path).name.startswith("compare_") for action in captured_actions)
    assert any(action.action == "rmdir" for action in captured_actions)


def test_submission_job_create_failure_keeps_inputs_and_marks_submission_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, runner, artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(
        "app.infrastructure.task_runner.TaskJob",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("job create failed")),
    )
    task_id = "TJOB_CREATE_FAIL"

    with pytest.raises(OSError, match="job create failed"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    task = repository.load_compare_task(task_id)
    assert (task.status, task.terminal_reason, task.stage) == ("FAILED", "SUBMISSION_FAILED", "提交失败")
    assert Path(task.original_pdf_path).exists() and Path(task.compare_pdf_path).exists()
    assert list((artifact_store.task_root(task_id) / "staging").rglob("*.pdf")) == []
    assert not recovery_store.marker_path(task_id).exists()


def test_submission_enqueue_failure_keeps_inputs_and_marks_submission_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, runner, artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(
        runner.job_repository,
        "enqueue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("enqueue failed")),
    )
    task_id = "TENQUEUE_FAIL"

    with pytest.raises(OSError, match="enqueue failed"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    task = repository.load_compare_task(task_id)
    assert (task.status, task.terminal_reason, task.stage) == ("FAILED", "SUBMISSION_FAILED", "提交失败")
    assert Path(task.original_pdf_path).exists() and Path(task.compare_pdf_path).exists()
    assert list((artifact_store.task_root(task_id) / "staging").rglob("*.pdf")) == []
    assert not recovery_store.marker_path(task_id).exists()


def test_submission_worker_start_failure_after_enqueue_keeps_durable_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_workers_before = [thread.name for thread in threading.enumerate() if thread.name.startswith("task-runner-")]
    assert live_workers_before == []
    application, repository, runner, artifact_store, recovery_store = _submission_application(tmp_path)
    runner.autostart = True
    monkeypatch.setattr(runner, "start", lambda: (_ for _ in ()).throw(OSError("worker start failed")))

    task = asyncio.run(
        application.submit_uploads(
            task_id="TSTART_FAIL",
            original_file=_pdf_upload("original.pdf"),
            compare_file=_pdf_upload("compare.pdf"),
            compare_options=CompareOptions(),
            owner=ADMIN,
        )
    )

    stored = repository.load_compare_task(task.task_id)
    job = runner.load_active_job(task.task_id, task_type="compare")
    assert (stored.status, stored.active_job_id) == ("PROCESSING", job.job_id)
    assert job.status == "QUEUED"
    assert Path(stored.original_pdf_path).exists() and Path(stored.compare_pdf_path).exists()
    assert list((artifact_store.task_root(task.task_id) / "staging").rglob("*.pdf")) == []
    assert not recovery_store.marker_path(task.task_id).exists()


def test_submission_partial_worker_start_failure_keeps_active_binding_until_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, runner, artifact_store, recovery_store = _submission_application(tmp_path)
    runner.autostart = True
    claimed = threading.Event()
    finish = threading.Event()
    worker_done = threading.Event()
    worker_errors: list[BaseException] = []
    worker_threads: list[threading.Thread] = []

    def worker() -> None:
        try:
            job = runner.coordinator.claim_next(worker_id="partial-start-worker", lease_seconds=30)
            assert job is not None
            claimed.set()
            assert finish.wait(timeout=5)
            result = repository.load_compare_task(job.task_id)
            runner.coordinator.commit_success(
                job.job_id,
                worker_id="partial-start-worker",
                result=result,
            )
        except BaseException as exc:
            worker_errors.append(exc)
        finally:
            worker_done.set()

    def start_worker_then_fail() -> None:
        thread = threading.Thread(target=worker, name="controlled-partial-start-worker")
        worker_threads.append(thread)
        thread.start()
        assert claimed.wait(timeout=5)
        raise OSError("worker start failed after claim")

    monkeypatch.setattr(runner, "start", start_worker_then_fail)

    task = asyncio.run(
        application.submit_uploads(
            task_id="TPARTIAL_START_FAIL",
            original_file=_pdf_upload("original.pdf"),
            compare_file=_pdf_upload("compare.pdf"),
            compare_options=CompareOptions(),
            owner=ADMIN,
        )
    )

    stored = repository.load_compare_task(task.task_id)
    running_job = runner.load_job(stored.active_job_id)
    assert (stored.status, stored.active_job_id) == ("PROCESSING", running_job.job_id)
    assert (running_job.status, running_job.lease_owner) == ("RUNNING", "partial-start-worker")

    finish.set()
    assert worker_done.wait(timeout=5)
    worker_threads[0].join(timeout=5)
    assert worker_errors == []
    terminal_task = repository.load_compare_task(task.task_id)
    terminal_job = runner.load_job(terminal_task.terminal_job_id)
    assert (terminal_task.status, terminal_task.active_job_id) == ("COMPLETED", terminal_job.job_id)
    assert (terminal_task.terminal_job_id, terminal_job.status) == (terminal_job.job_id, "SUCCEEDED")
    assert Path(terminal_task.original_pdf_path).is_file() and Path(terminal_task.compare_pdf_path).is_file()
    assert list((artifact_store.task_root(task.task_id) / "staging").rglob("*.pdf")) == []
    assert not recovery_store.marker_path(task.task_id).exists()


def test_submission_worker_start_failure_after_immediate_terminal_claim_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, runner, artifact_store, recovery_store = _submission_application(tmp_path)
    runner.autostart = True

    def finish_then_fail_start() -> None:
        claimed = runner.coordinator.claim_next(worker_id="immediate-worker", lease_seconds=30)
        assert claimed is not None
        result = repository.load_compare_task(claimed.task_id)
        runner.coordinator.commit_success(
            claimed.job_id,
            worker_id="immediate-worker",
            result=result,
        )
        raise OSError("worker start failed after terminal commit")

    monkeypatch.setattr(runner, "start", finish_then_fail_start)

    task = asyncio.run(
        application.submit_uploads(
            task_id="TSTART_TERMINAL",
            original_file=_pdf_upload("original.pdf"),
            compare_file=_pdf_upload("compare.pdf"),
            compare_options=CompareOptions(),
            owner=ADMIN,
        )
    )

    stored = repository.load_compare_task(task.task_id)
    terminal_job = runner.load_job(stored.terminal_job_id)
    assert (stored.status, terminal_job.status) == ("COMPLETED", "SUCCEEDED")
    assert Path(stored.original_pdf_path).is_file() and Path(stored.compare_pdf_path).is_file()
    assert list((artifact_store.task_root(task.task_id) / "staging").rglob("*.pdf")) == []
    assert not recovery_store.marker_path(task.task_id).exists()


def test_submission_does_not_catch_keyboard_interrupt_as_business_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    marker_calls = 0

    async def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt("stop process")

    def record_marker(**_kwargs):
        nonlocal marker_calls
        marker_calls += 1
        raise AssertionError("process interrupt must not create a recovery marker")

    monkeypatch.setattr("app.application.compare_tasks.stream_upload_to_path", interrupt)
    monkeypatch.setattr(recovery_store, "create_marker", record_marker)

    with pytest.raises(KeyboardInterrupt, match="stop process"):
        asyncio.run(
            application.submit_uploads(
                task_id="TINTERRUPT",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert marker_calls == 0


def test_submission_cancelled_error_cleans_scoped_partial_and_reraises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)

    async def write_partial_then_cancel(_upload, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"partial")
        raise asyncio.CancelledError("request cancelled")

    monkeypatch.setattr("app.application.compare_tasks.stream_upload_to_path", write_partial_then_cancel)
    task_id = "TCANCELLED_UPLOAD"

    with pytest.raises(asyncio.CancelledError, match="request cancelled"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert list(artifact_store.task_root(task_id).rglob("*.pdf")) == []
    assert not recovery_store.marker_path(task_id).exists()


def test_submission_cleanup_failure_keeps_primary_error_and_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(
        repository, "save_compare_task", lambda _task: (_ for _ in ()).throw(OSError("primary task save"))
    )
    execute = recovery_store._execute_action

    def fail_one(marker, action):
        if action.action == "unlink":
            raise OSError("cleanup secondary")
        execute(marker, action)

    monkeypatch.setattr(recovery_store, "_execute_action", fail_one)

    with pytest.raises(OSError, match="primary task save"):
        asyncio.run(
            application.submit_uploads(
                task_id="TCLEANUP_FAIL",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    marker = recovery_store.load_marker("TCLEANUP_FAIL")
    assert marker.primary_error == "primary task save"
    assert marker.attempts == 1
    assert marker.actions


def test_submission_compensation_failure_marks_existing_task_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(
        runner.job_repository,
        "enqueue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("enqueue primary")),
    )
    monkeypatch.setattr(recovery_store, "recover_marker", lambda _marker: False)
    task_id = "TCOMPENSATION_INCOMPLETE"

    with pytest.raises(OSError, match="enqueue primary"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    task = repository.load_compare_task(task_id)
    summary = next(error for error in task.errors if "COMPENSATION_INCOMPLETE" in error)
    assert "enqueue primary" in summary
    assert str(recovery_store.marker_path(task_id)) in summary
    assert recovery_store.marker_path(task_id).exists()


def test_submission_compensation_summary_repairs_failed_state_after_initial_update_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(
        runner.job_repository,
        "enqueue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("enqueue primary")),
    )
    monkeypatch.setattr(recovery_store, "recover_marker", lambda _marker: False)
    update = repository.update_compare_task
    updates = 0

    def fail_first_update(*args, **kwargs):
        nonlocal updates
        updates += 1
        if updates == 1:
            raise OSError("initial status update transient")
        return update(*args, **kwargs)

    monkeypatch.setattr(repository, "update_compare_task", fail_first_update)
    task_id = "TCOMPENSATION_REPAIRS_STATUS"

    with pytest.raises(OSError, match="enqueue primary"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    task = repository.load_compare_task(task_id)
    assert (task.status, task.terminal_reason, task.stage) == (
        "FAILED",
        "SUBMISSION_FAILED",
        "提交失败",
    )
    assert "enqueue primary" in task.errors
    assert any("COMPENSATION_INCOMPLETE" in error and "enqueue primary" in error for error in task.errors)
    assert recovery_store.marker_path(task_id).exists()


def test_submission_compensation_summary_failure_logs_secondary_and_keeps_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application, repository, runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(
        runner.job_repository,
        "enqueue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("enqueue primary")),
    )
    monkeypatch.setattr(recovery_store, "recover_marker", lambda _marker: False)
    update = repository.update_compare_task
    updates = 0

    def fail_second_update(*args, **kwargs):
        nonlocal updates
        updates += 1
        if updates == 2:
            raise OSError("summary persistence secondary")
        return update(*args, **kwargs)

    monkeypatch.setattr(repository, "update_compare_task", fail_second_update)
    caplog.set_level("ERROR", logger="app.application.compare_tasks")
    task_id = "TCOMPENSATION_SUMMARY_FAIL"

    with pytest.raises(OSError, match="enqueue primary"):
        asyncio.run(
            application.submit_uploads(
                task_id=task_id,
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert recovery_store.marker_path(task_id).exists()
    assert task_id in caplog.text
    assert "enqueue primary" in caplog.text
    assert "summary persistence secondary" in caplog.text
    assert str(recovery_store.marker_path(task_id)) in caplog.text


def test_submission_rmdir_cleanup_failure_logs_secondary_and_keeps_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application, repository, _runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(repository, "save_compare_task", lambda _task: (_ for _ in ()).throw(OSError("primary save")))
    execute = recovery_store._execute_action

    def fail_rmdir(marker, action):
        if action.action == "rmdir":
            raise OSError("rmdir secondary")
        execute(marker, action)

    monkeypatch.setattr(recovery_store, "_execute_action", fail_rmdir)
    caplog.set_level("ERROR", logger="app.infrastructure.recovery_store")

    with pytest.raises(OSError, match="primary save"):
        asyncio.run(
            application.submit_uploads(
                task_id="TRMDIR_FAIL",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    marker = recovery_store.load_marker("TRMDIR_FAIL")
    assert marker.primary_error == "primary save"
    assert marker.attempts == 1
    assert "task_id=TRMDIR_FAIL" in caplog.text
    assert f"attempt_id={marker.attempt_id}" in caplog.text
    assert "action=rmdir" in caplog.text
    assert "rmdir secondary" in caplog.text


def test_submission_compensation_preserves_other_attempt_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, _runner, artifact_store, _recovery_store = _submission_application(tmp_path)
    task_root = artifact_store.task_root("TPRESERVE_OTHER")
    other_attempt = task_root / "staging" / "another-attempt" / "keep.pdf"
    unrelated = task_root / "uploads" / "other_contract.pdf"
    other_attempt.parent.mkdir(parents=True, exist_ok=True)
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    other_attempt.write_bytes(b"other-attempt")
    unrelated.write_bytes(b"other-artifact")
    monkeypatch.setattr(
        repository, "save_compare_task", lambda _task: (_ for _ in ()).throw(OSError("task save failed"))
    )

    with pytest.raises(OSError, match="task save failed"):
        asyncio.run(
            application.submit_uploads(
                task_id="TPRESERVE_OTHER",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert other_attempt.read_bytes() == b"other-attempt"
    assert unrelated.read_bytes() == b"other-artifact"


def test_submission_marker_write_failure_logs_critical_and_preserves_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application, repository, _runner, _artifact_store, recovery_store = _submission_application(tmp_path)
    monkeypatch.setattr(repository, "save_compare_task", lambda _task: (_ for _ in ()).throw(OSError("primary save")))
    monkeypatch.setattr(
        recovery_store,
        "create_marker",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("marker unavailable")),
    )
    caplog.set_level("CRITICAL", logger="app.application.compare_tasks")

    with pytest.raises(OSError, match="primary save"):
        asyncio.run(
            application.submit_uploads(
                task_id="TMARKER_FAIL",
                original_file=_pdf_upload("original.pdf"),
                compare_file=_pdf_upload("compare.pdf"),
                compare_options=CompareOptions(),
                owner=ADMIN,
            )
        )

    assert "TMARKER_FAIL" in caplog.text
    assert "primary save" in caplog.text
    assert "marker unavailable" in caplog.text
    assert "unlink" in caplog.text


def test_submission_recovery_lock_double_failure_preserves_unpersisted_primary_and_finals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application, repository, _runner, artifact_store, recovery_store = _submission_application(tmp_path)
    task_id = "TRECOVERY_LOCK_UNPERSISTED"
    monkeypatch.setattr(
        repository,
        "save_compare_task",
        lambda _task: (_ for _ in ()).throw(OSError("task save primary")),
    )
    recovery_store._lock_timeout_seconds = 0.01
    compensate = application._compensate_submission
    outcomes: list[bool] = []

    def record_outcome(**kwargs) -> bool:
        outcome = compensate(**kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(application, "_compensate_submission", record_outcome)
    caplog.set_level("CRITICAL", logger="app.application.compare_tasks")

    with _hold_recovery_marker_lock(recovery_store, task_id):
        with pytest.raises(RecoveryMarkerLockTimeout):
            asyncio.run(
                application.submit_uploads(
                    task_id=task_id,
                    original_file=_pdf_upload("original.pdf"),
                    compare_file=_pdf_upload("compare.pdf"),
                    compare_options=CompareOptions(),
                    owner=ADMIN,
                )
            )

    assert outcomes == [False]
    assert len(list((artifact_store.task_root(task_id) / "uploads").glob("*.pdf"))) == 0
    assert not recovery_store.marker_path(task_id).exists()
    assert "Submission recovery marker creation failed" in caplog.text
    assert "Submission recovery fallback cleanup failed" in caplog.text
    assert "Recovery marker lock timed out" in caplog.text
    assert "actions=" in caplog.text


def test_submission_recovery_lock_double_failure_marks_persisted_task_with_unavailable_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application, repository, runner, artifact_store, recovery_store = _submission_application(tmp_path)
    task_id = "TRECOVERY_LOCK_PERSISTED"
    monkeypatch.setattr(
        runner.job_repository,
        "enqueue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("enqueue primary")),
    )
    recovery_store._lock_timeout_seconds = 0.01
    compensate = application._compensate_submission
    outcomes: list[bool] = []

    def record_outcome(**kwargs) -> bool:
        outcome = compensate(**kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(application, "_compensate_submission", record_outcome)
    caplog.set_level("CRITICAL", logger="app.application.compare_tasks")

    with _hold_recovery_marker_lock(recovery_store, task_id):
        with pytest.raises(RecoveryMarkerLockTimeout):
            asyncio.run(
                application.submit_uploads(
                    task_id=task_id,
                    original_file=_pdf_upload("original.pdf"),
                    compare_file=_pdf_upload("compare.pdf"),
                    compare_options=CompareOptions(),
                    owner=ADMIN,
                )
            )

    assert outcomes == [False]
    with pytest.raises(FileNotFoundError):
        repository.load_compare_task(task_id)
    assert len(list((artifact_store.task_root(task_id) / "uploads").glob("*.pdf"))) == 0
    assert not recovery_store.marker_path(task_id).exists()
    assert "Submission recovery marker creation failed" in caplog.text
    assert "Submission recovery fallback cleanup failed" in caplog.text


def test_api_compare_rejects_damaged_pdf_before_task_creation(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/compare",
        files={
            "original_file": ("damaged.pdf", b"%PDF-not-a-real-document", "application/pdf"),
            "compare_file": ("compare.pdf", b"%PDF-not-a-real-document", "application/pdf"),
        },
    )

    assert response.status_code == 400
    assert "PDF 文件已损坏或格式无效" in response.json()["detail"]
    assert not list(settings.tasks_dir.rglob("task.json"))
    assert not list(settings.tasks_dir.rglob("job.json"))


def test_api_compare_rejects_encrypted_pdf_before_task_creation(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    pdf = fitz.open()
    pdf.new_page()
    try:
        encrypted = pdf.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
        )
    finally:
        pdf.close()

    client = TestClient(app)
    response = client.post(
        "/api/compare",
        files={
            "original_file": ("encrypted.pdf", encrypted, "application/pdf"),
            "compare_file": ("compare.pdf", encrypted, "application/pdf"),
        },
    )

    assert response.status_code == 400
    assert "PDF 文件已加密" in response.json()["detail"]
    assert not list(settings.tasks_dir.rglob("task.json"))
    assert not list(settings.tasks_dir.rglob("job.json"))


def test_api_compare_rejects_owner_only_encrypted_pdf_before_task_creation(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    pdf = fitz.open()
    pdf.new_page()
    try:
        encrypted = pdf.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="",
        )
    finally:
        pdf.close()

    client = TestClient(app)
    response = client.post(
        "/api/compare",
        files={
            "original_file": ("encrypted.pdf", encrypted, "application/pdf"),
            "compare_file": ("compare.pdf", encrypted, "application/pdf"),
        },
    )

    assert response.status_code == 400
    assert "PDF 文件已加密" in response.json()["detail"]
    assert not list(settings.tasks_dir.rglob("task.json"))
    assert not list(settings.tasks_dir.rglob("job.json"))


def test_api_compare_persists_enabled_exclusion_options(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(original, ["1. Payment", "Buyer shall pay within 30 days."])
    make_pdf(compare, ["1. Payment", "Buyer shall pay within 45 days."])

    try:
        client = TestClient(app)
        with original.open("rb") as original_file, compare.open("rb") as compare_file:
            response = client.post(
                "/api/compare",
                data={
                    "ignore_punctuation": "true",
                    "ignore_headers_footers": "true",
                    "ignore_stamps": "true",
                    "signing_region_mode": "off",
                },
                files={
                    "original_file": ("original.pdf", original_file, "application/pdf"),
                    "compare_file": ("compare.pdf", compare_file, "application/pdf"),
                },
            )
    finally:
        default_task_runner.autostart = original_autostart

    assert response.status_code == 200, response.text
    payload = response.json()
    task_id = payload["task_id"]
    assert "compare_options" not in payload
    task = load_task(task_id)
    assert task.compare_options.ignore_punctuation is False
    assert task.compare_options.ignore_headers_footers is True
    assert task.compare_options.ignore_stamps is True
    assert task.compare_options.signing_region_mode == "off"
    job = default_task_runner.latest_job(task_id, task_type="compare")
    assert job.payload["compare_options"] == {
        "ignore_punctuation": False,
        "ignore_headers_footers": True,
        "ignore_stamps": True,
        "signing_region_mode": "off",
    }


def test_api_compare_defaults_signing_region_mode_to_full(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(original, ["1. Payment", "Buyer shall pay within 30 days."])
    make_pdf(compare, ["1. Payment", "Buyer shall pay within 45 days."])

    try:
        client = TestClient(app)
        with original.open("rb") as original_file, compare.open("rb") as compare_file:
            response = client.post(
                "/api/compare",
                files={
                    "original_file": ("original.pdf", original_file, "application/pdf"),
                    "compare_file": ("compare.pdf", compare_file, "application/pdf"),
                },
            )
    finally:
        default_task_runner.autostart = original_autostart

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    task = load_task(task_id)
    assert task.compare_options.signing_region_mode == "full"
    job = default_task_runner.latest_job(task_id, task_type="compare")
    assert job.payload["compare_options"]["signing_region_mode"] == "full"


def test_api_compare_rejects_invalid_signing_region_mode(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(original, ["1. Payment"])
    make_pdf(compare, ["1. Payment"])

    client = TestClient(app)
    with original.open("rb") as original_file, compare.open("rb") as compare_file:
        response = client.post(
            "/api/compare",
            data={"signing_region_mode": "summary"},
            files={
                "original_file": ("original.pdf", original_file, "application/pdf"),
                "compare_file": ("compare.pdf", compare_file, "application/pdf"),
            },
        )

    assert response.status_code == 422


def test_compare_options_support_signing_region_mode() -> None:
    from app.models import CompareOptions

    options = CompareOptions(signing_region_mode="off")

    assert options.signing_region_mode == "off"


def test_compare_options_default_signing_region_mode_full() -> None:
    from app.models import CompareOptions

    options = CompareOptions()

    assert options.signing_region_mode == "full"


def test_compare_progress_stream_sends_current_snapshot(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TPROGRESS_SNAPSHOT",
            status="COMPLETED",
            stage="已完成",
            progress_percent=100,
        )
    )

    client = TestClient(app)
    with client.stream("GET", "/api/compare/TPROGRESS_SNAPSHOT/progress") as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert lines == [
        'data: {"task_id": "TPROGRESS_SNAPSHOT", "stage": "已完成", "progress_percent": 100, "status": "COMPLETED", "revision": 1}'
    ]


def test_root_is_not_a_backend_page() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 404


def test_openapi_exposes_compare_but_not_extract_routes() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert any(path.startswith("/api/compare") for path in paths)
    assert all("/api/extract" not in path for path in paths)


def test_api_schema_has_no_field_extraction_product_types() -> None:
    assert not hasattr(api_schemas, "ExtractionTaskResponse")
    assert not hasattr(api_schemas, "ExtractionFieldRequest")


def test_compare_execution_api_gets_and_cancels_queued_job(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    task_id = "TEXEC_CANCEL"
    job_id = f"compare:{task_id}"
    save_task(
        CompareTask(
            task_id=task_id,
            active_job_id=job_id,
            original_pdf_path="a.pdf",
            compare_pdf_path="b.pdf",
        )
    )
    job = default_task_runner.job_repository.enqueue(
        TaskJob(job_id=job_id, task_id=task_id, task_type="compare", payload={"task_id": task_id})
    )

    client = TestClient(app)
    execution_response = client.get(f"/api/compare/{task_id}/execution")
    assert execution_response.status_code == 200
    assert execution_response.json()["job_id"] == job.job_id
    assert execution_response.json()["status"] == "QUEUED"

    cancel_response = client.post(f"/api/compare/{task_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "CANCELLED"
    assert load_task(task_id).stage == "已取消"


def test_compare_execution_api_repeats_cancel_after_success_without_mutation(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    task_id = "TEXEC_SUCCESS_CANCEL_REPLAY"
    job_id = f"compare:{task_id}:1"
    save_task(CompareTask(task_id=task_id, active_job_id=job_id))
    job = default_task_runner.coordinator.enqueue(
        TaskJob(job_id=job_id, task_id=task_id, task_type="compare", execution_no=1)
    )
    claimed = default_task_runner.coordinator.claim_next(worker_id="api-success-worker", lease_seconds=30)
    assert claimed is not None
    terminal_task, terminal_job = default_task_runner.coordinator.commit_success(
        job.job_id,
        worker_id="api-success-worker",
        result=CompareTask(task_id=task_id),
    )

    client = TestClient(app)
    first = client.post(f"/api/compare/{task_id}/cancel")
    second = client.post(f"/api/compare/{task_id}/cancel")

    stored_task = load_task(task_id)
    stored_job = default_task_runner.coordinator.load(job.job_id)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "SUCCEEDED"
    assert second.json() == first.json()
    assert stored_job == terminal_job
    assert stored_task.revision == terminal_task.revision
    assert stored_task.report_revision == terminal_task.report_revision


def test_compare_execution_api_retries_failed_job(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    task_id = "TEXEC_RETRY"
    original = tmp_path / "a.pdf"
    compare = tmp_path / "b.pdf"
    original.write_bytes(b"original")
    compare.write_bytes(b"compare")
    save_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            stage="失败",
            progress_percent=100,
            original_pdf_path=str(original),
            compare_pdf_path=str(compare),
            errors=["failed"],
        )
    )
    job = default_task_runner.coordinator._persist_then_replace(
        TaskJob(
            job_id=f"compare:{task_id}:1",
            task_id=task_id,
            task_type="compare",
            status="FAILED",
            execution_no=1,
            payload={"task_id": task_id, "original_path": "a.pdf", "compare_path": "b.pdf"},
            attempt=1,
            max_attempts=1,
            last_error="failed",
        )
    )

    try:
        client = TestClient(app)
        assert client.get(f"/api/compare/{task_id}").json()["retry_eligible"] is True
        records = client.get("/api/compare/records").json()["records"]
        assert next(item for item in records if item["task_id"] == task_id)["retry_eligible"] is True
        retry_response = client.post(f"/api/compare/{task_id}/retry")
    finally:
        default_task_runner.autostart = original_autostart

    assert retry_response.status_code == 200
    assert retry_response.json()["job_id"] == f"compare:{task_id}:2"
    assert retry_response.json()["execution_no"] == 2
    assert retry_response.json()["status"] == "QUEUED"
    assert retry_response.json()["attempt"] == 0
    retried_task = load_task(task_id)
    assert retried_task.status == "PROCESSING"
    assert retried_task.stage == "排队中"
    assert retried_task.errors == []
    assert retried_task.active_job_id == f"compare:{task_id}:2"
    assert default_task_runner.job_repository.load(job.job_id).status == "FAILED"
    assert (settings.tasks_dir / task_id / "jobs" / "1.json").is_file()
    assert (settings.tasks_dir / task_id / "jobs" / "2.json").is_file()


@pytest.mark.parametrize(
    ("terminal_reason", "create_original", "create_compare"),
    [
        ("CANCELLED", True, True),
        ("COMPLETED", True, True),
        ("SUBMISSION_FAILED", False, True),
    ],
)
def test_compare_task_application_rejects_ineligible_retry_transition(
    tmp_path: Path,
    terminal_reason: str,
    create_original: bool,
    create_compare: bool,
) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    if create_original:
        original.write_bytes(b"original")
    if create_compare:
        compare.write_bytes(b"compare")
    repository = LocalJsonTaskRepository(settings)
    runner = QueuedTaskRunner(
        job_repository=LocalJsonTaskJobRepository(settings),
        app_settings=settings,
        autostart=False,
    )
    application = CompareTaskApplication(repository=repository, runner=runner)
    task_id = f"TAPP_RETRY_{terminal_reason}"
    repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            status="COMPLETED" if terminal_reason == "COMPLETED" else "FAILED",
            terminal_reason="NONE" if terminal_reason == "COMPLETED" else terminal_reason,
            original_pdf_path=str(original),
            compare_pdf_path=str(compare),
        )
    )
    failed_compare_job(runner, task_id)

    with pytest.raises(TaskTransitionConflict):
        application.retry_compare(task_id)

    expected_status = "COMPLETED" if terminal_reason == "COMPLETED" else "FAILED"
    assert repository.load_compare_task(task_id).status == expected_status
    assert runner.latest_job(task_id, task_type="compare").status == "FAILED"


@pytest.mark.parametrize(
    ("terminal_reason", "create_inputs"),
    [("EXECUTION_FAILED", True), ("SUBMISSION_FAILED", True)],
)
def test_compare_task_application_allows_eligible_retry_transition(
    tmp_path: Path,
    terminal_reason: str,
    create_inputs: bool,
) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    if create_inputs:
        original.write_bytes(b"original")
        compare.write_bytes(b"compare")
    repository = LocalJsonTaskRepository(settings)
    runner = QueuedTaskRunner(
        job_repository=LocalJsonTaskJobRepository(settings),
        app_settings=settings,
        autostart=False,
    )
    application = CompareTaskApplication(repository=repository, runner=runner)
    task_id = f"TAPP_RETRY_{terminal_reason}"
    repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason=terminal_reason,
            original_pdf_path=str(original),
            compare_pdf_path=str(compare),
        )
    )
    failed_compare_job(runner, task_id)

    job = application.retry_compare(task_id)

    assert job.status == "QUEUED"
    retried_task = repository.load_compare_task(task_id)
    assert retried_task.status == "PROCESSING"
    assert retried_task.active_job_id == job.job_id


@pytest.mark.parametrize(
    ("terminal_reason", "create_original", "create_compare"),
    [
        ("CANCELLED", True, True),
        ("SUBMISSION_FAILED", True, False),
    ],
)
def test_compare_execution_api_rejects_ineligible_retry_transition(
    tmp_path: Path,
    terminal_reason: str,
    create_original: bool,
    create_compare: bool,
) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    if create_original:
        original.write_bytes(b"original")
    if create_compare:
        compare.write_bytes(b"compare")
    task_id = f"TAPI_RETRY_{terminal_reason}"
    save_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason=terminal_reason,
            original_pdf_path=str(original),
            compare_pdf_path=str(compare),
        )
    )
    failed_compare_job(default_task_runner, task_id)

    try:
        client = TestClient(app)
        assert client.get(f"/api/compare/{task_id}").json()["retry_eligible"] is False
        records = client.get("/api/compare/records").json()["records"]
        assert next(item for item in records if item["task_id"] == task_id)["retry_eligible"] is False
        response = client.post(f"/api/compare/{task_id}/retry")
    finally:
        default_task_runner.autostart = original_autostart

    assert response.status_code == 409
    assert load_task(task_id).status == "FAILED"
    assert default_task_runner.latest_job(task_id, task_type="compare").status == "FAILED"


def test_compare_execution_api_retries_eligible_submission_failure(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    original.write_bytes(b"original")
    compare.write_bytes(b"compare")
    task_id = "TAPI_RETRY_ELIGIBLE_SUBMISSION"
    save_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason="SUBMISSION_FAILED",
            original_pdf_path=str(original),
            compare_pdf_path=str(compare),
        )
    )
    failed_compare_job(default_task_runner, task_id)

    try:
        client = TestClient(app)
        assert client.get(f"/api/compare/{task_id}").json()["retry_eligible"] is True
        records = client.get("/api/compare/records").json()["records"]
        assert next(item for item in records if item["task_id"] == task_id)["retry_eligible"] is True
        response = client.post(f"/api/compare/{task_id}/retry")
    finally:
        default_task_runner.autostart = original_autostart

    assert response.status_code == 200
    assert response.json()["status"] == "QUEUED"
    assert load_task(task_id).status == "PROCESSING"


def test_compare_execution_api_rebuilds_first_job_for_submission_failure_without_job(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    original.write_bytes(b"original")
    compare.write_bytes(b"compare")
    task_id = "TAPI_RETRY_SUBMISSION_NO_JOB"
    save_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason="SUBMISSION_FAILED",
            original_pdf_path=str(original),
            compare_pdf_path=str(compare),
            original_filename="original-name.pdf",
            compare_filename="compare-name.pdf",
            compare_options={"ignore_stamps": True, "ignore_headers_footers": True},
        )
    )

    try:
        client = TestClient(app)
        assert client.get(f"/api/compare/{task_id}").json()["retry_eligible"] is True
        records = client.get("/api/compare/records").json()["records"]
        assert next(item for item in records if item["task_id"] == task_id)["retry_eligible"] is True
        response = client.post(f"/api/compare/{task_id}/retry")
        repeated = client.post(f"/api/compare/{task_id}/retry")
    finally:
        default_task_runner.autostart = original_autostart

    assert response.status_code == 200
    assert response.json()["job_id"] == f"compare:{task_id}:1"
    assert response.json()["execution_no"] == 1
    assert repeated.status_code == 409
    jobs = default_task_runner.jobs_for_task(task_id, task_type="compare")
    assert len(jobs) == 1
    assert jobs[0].payload == {
        "task_id": task_id,
        "original_path": str(original),
        "compare_path": str(compare),
        "original_filename": "original-name.pdf",
        "compare_filename": "compare-name.pdf",
        "compare_options": {
            "ignore_punctuation": False,
            "ignore_stamps": True,
            "ignore_headers_footers": True,
            "signing_region_mode": "full",
        },
    }
    retried_task = load_task(task_id)
    assert (retried_task.status, retried_task.terminal_reason) == ("PROCESSING", "NONE")
    assert retried_task.active_job_id == f"compare:{task_id}:1"


@pytest.mark.parametrize("job_status", [None, "SUCCEEDED", "RUNNING"])
def test_execution_failure_retry_projection_matches_job_constraints(
    tmp_path: Path,
    job_status: str | None,
) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    original.write_bytes(b"original")
    compare.write_bytes(b"compare")
    task_id = f"TAPI_EXEC_JOB_{job_status or 'NONE'}"
    task = CompareTask(
        task_id=task_id,
        status="FAILED",
        terminal_reason="EXECUTION_FAILED",
        original_pdf_path=str(original),
        compare_pdf_path=str(compare),
    )
    save_task(task)
    if job_status is not None:
        default_task_runner.coordinator._persist_then_replace(
            TaskJob(
                job_id=f"compare:{task_id}:1",
                task_id=task_id,
                task_type="compare",
                status=job_status,
                execution_no=1,
                payload={"task_id": task_id},
            )
        )

    try:
        client = TestClient(app)
        detail = client.get(f"/api/compare/{task_id}").json()
        records = client.get("/api/compare/records").json()["records"]
        response = client.post(f"/api/compare/{task_id}/retry")
    finally:
        default_task_runner.autostart = original_autostart

    assert detail["retry_eligible"] is False
    assert next(item for item in records if item["task_id"] == task_id)["retry_eligible"] is False
    assert response.status_code == 409


def test_execution_failure_with_missing_input_is_not_retryable_even_with_failed_job(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    task_id = "TAPI_EXEC_MISSING_INPUT"
    save_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason="EXECUTION_FAILED",
            original_pdf_path=str(original),
            compare_pdf_path=str(tmp_path / "missing.pdf"),
        )
    )
    failed_compare_job(default_task_runner, task_id)

    try:
        client = TestClient(app)
        assert client.get(f"/api/compare/{task_id}").json()["retry_eligible"] is False
        response = client.post(f"/api/compare/{task_id}/retry")
    finally:
        default_task_runner.autostart = original_autostart

    assert response.status_code == 409


def test_compare_execution_api_rejects_retry_for_processing_task(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    task_id = "TEXEC_RETRY_CONFLICT"
    save_task(CompareTask(task_id=task_id, status="PROCESSING"))
    default_task_runner.job_repository.enqueue(
        TaskJob(job_id=f"compare:{task_id}", task_id=task_id, task_type="compare", payload={"task_id": task_id})
    )

    client = TestClient(app)
    response = client.post(f"/api/compare/{task_id}/retry")

    assert response.status_code == 409


def test_compare_task_api_projects_terminal_reason_and_revisions(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TSTATE_FIELDS",
            status="FAILED",
            terminal_reason="EXECUTION_FAILED",
            report_revision=3,
        )
    )

    client = TestClient(app)
    payload = client.get("/api/compare/TSTATE_FIELDS").json()

    assert payload["terminal_reason"] == "EXECUTION_FAILED"
    assert payload["revision"] >= 1
    assert payload["report_revision"] == 3
    assert payload["retry_eligible"] is False
    records = client.get("/api/compare/records").json()["records"]
    assert next(item for item in records if item["task_id"] == "TSTATE_FIELDS")["retry_eligible"] is False


def test_task_execution_presenter_includes_execution_identity_and_error_code() -> None:
    job = TaskJob(
        job_id="compare:TEXECUTION_FIELDS:4",
        task_id="TEXECUTION_FIELDS",
        task_type="compare",
        execution_no=4,
        error_code="LEASE_EXPIRED_MAX_ATTEMPTS",
    )

    payload = task_execution_response(job).model_dump()

    assert payload["execution_no"] == 4
    assert payload["error_code"] == "LEASE_EXPIRED_MAX_ATTEMPTS"


@pytest.mark.parametrize("error_type", [TaskTransitionConflict, TaskStaleLeaseError])
def test_task_state_conflicts_map_to_http_409(error_type: type[Exception]) -> None:
    mapped = http_error(error_type("state changed"))

    assert mapped.status_code == 409


def test_compare_records_list_uses_compare_tasks_only(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TOLDER",
            status="COMPLETED",
            created_at="2026-05-20T10:00:00+00:00",
            updated_at="2026-05-20T10:30:00+00:00",
            original_filename="old-a.pdf",
            compare_filename="old-b.pdf",
            diff_count=1,
        )
    )
    save_task(
        CompareTask(
            task_id="TNEWER",
            status="PROCESSING",
            created_at="2026-05-21T09:00:00+00:00",
            updated_at="2026-05-21T09:05:00+00:00",
            original_filename="new-a.pdf",
            compare_filename="new-b.pdf",
            diff_count=3,
        )
    )
    extraction_dir = settings.tasks_dir / "TEXT001"
    extraction_dir.mkdir(parents=True)
    (extraction_dir / "task.json").write_text(
        json.dumps({"task_id": "TEXT001", "task_type": "extraction", "status": "COMPLETED"}),
        encoding="utf-8",
    )

    client = TestClient(app)
    response = client.get("/api/compare/records")

    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert [record["task_id"] for record in records] == ["TNEWER", "TOLDER"]
    assert records[0]["report_url"] == ""
    assert records[1]["report_url"] == "/api/compare/TOLDER/report"
    assert records[0]["terminal_reason"] == "NONE"
    assert records[1]["terminal_reason"] == "NONE"
    assert records[0]["report_revision"] == 0
    assert records[1]["report_revision"] == 1
    assert all(record["task_id"] != "TEXT001" for record in records)


def test_compare_records_list_supports_pagination_and_created_time_filter(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_compare_task_fixture(
        task_id="TMAY20",
        status="COMPLETED",
        created_at="2026-05-20T08:00:00+00:00",
        updated_at="2026-05-24T10:00:00+00:00",
        original_filename="may20-a.pdf",
        compare_filename="may20-b.pdf",
    )
    save_compare_task_fixture(
        task_id="TMAY21",
        status="COMPLETED",
        created_at="2026-05-21T08:00:00+00:00",
        updated_at="2026-05-24T10:00:00+00:00",
        original_filename="may21-a.pdf",
        compare_filename="may21-b.pdf",
    )
    save_compare_task_fixture(
        task_id="TMAY22",
        status="PROCESSING",
        created_at="2026-05-22T08:00:00+00:00",
        updated_at="2026-05-22T10:00:00+00:00",
        original_filename="may22-a.pdf",
        compare_filename="may22-b.pdf",
    )

    client = TestClient(app)
    response = client.get("/api/compare/records?page=1&page_size=1&start_date=2026-05-21&end_date=2026-05-22")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [record["task_id"] for record in payload["records"]] == ["TMAY22"]
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["page_size"] == 1
    assert payload["total_pages"] == 2

    second_page = client.get("/api/compare/records?page=2&page_size=1&start_date=2026-05-21&end_date=2026-05-22")

    assert second_page.status_code == 200, second_page.text
    assert [record["task_id"] for record in second_page.json()["records"]] == ["TMAY21"]


def test_compare_records_list_loads_only_requested_page_task_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_storage(tmp_path)
    for index in range(4):
        save_compare_task_fixture(
            task_id=f"TBOUNDED{index}",
            created_at=f"2026-05-{20 + index:02d}T08:00:00+00:00",
            updated_at=f"2026-05-{20 + index:02d}T10:00:00+00:00",
            original_filename=f"original-{index}.pdf",
            compare_filename=f"compare-{index}.pdf",
            diffs=[DiffItem(diff_id=f"D{index}", diff_type="ADD", original_text="large contract payload")],
        )

    loads: list[str] = []
    original_load = LocalJsonTaskRepository.load_compare_task

    def record_load(self: LocalJsonTaskRepository, task_id: str) -> CompareTask:
        loads.append(task_id)
        return original_load(self, task_id)

    monkeypatch.setattr(LocalJsonTaskRepository, "load_compare_task", record_load)

    response = TestClient(app).get("/api/compare/records?page=2&page_size=1")

    assert response.status_code == 200, response.text
    assert [record["task_id"] for record in response.json()["records"]] == ["TBOUNDED2"]
    assert loads == ["TBOUNDED2"]


def save_compare_task_fixture(**kwargs) -> None:
    task = _owned_task(CompareTask(**kwargs))
    save_task(task)
    task_json_path(task.task_id).write_text(
        json.dumps(to_jsonable(task), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_api_updates_diff_review_and_quality_summary(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    task = CompareTask(
        task_id="TREVIEW",
        status="COMPLETED",
        original_filename="review-a.pdf",
        compare_filename="review-b.pdf",
        diffs=[
            DiffItem(
                diff_id="D001",
                diff_type="MODIFY",
                title="付款",
                original_text="30 days",
                compare_text="45 days",
                source_type="clause",
                match_score=62,
                match_method="same_clause_no_low_similarity",
                review_flags=["SAME_CLAUSE_NO_LOW_SIMILARITY"],
                original_evidence=[
                    EvidenceBox(
                        page_no=1,
                        bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                        method="block_fallback",
                        text="30 days",
                        confidence=0.46,
                        evidence_quality="LOW",
                    )
                ],
            )
        ],
    )
    save_task(task)

    client = TestClient(app)
    response = client.patch(
        "/api/compare/TREVIEW/diffs/D001/review",
        json={
            "review_status": "CONFIRMED",
            "review_comment": "业务确认属实",
            "reviewed_by": "legal",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["diff"]["review_status"] == "CONFIRMED"
    assert payload["diff"]["review_comment"] == "业务确认属实"
    assert payload["diff"]["reviewed_by"] == ADMIN.sub
    assert payload["review_stats"]["reviewed_count"] == 1
    assert payload["review_stats"]["confirmed_count"] == 1

    persisted = load_task("TREVIEW")
    assert persisted.diffs[0].review_status == "CONFIRMED"
    assert persisted.confirmed_count == 1

    quality = client.get("/api/compare/TREVIEW/quality")
    assert quality.status_code == 200
    quality_payload = quality.json()
    assert quality_payload["review_stats"]["confirmed_count"] == 1
    assert quality_payload["review_stats"]["total_count"] == 1
    assert quality_payload["review_stats"]["review_unit"] == "audit_item"
    assert quality_payload["evidence_quality_counts"]["LOW"] == 1
    assert quality_payload["low_confidence_diffs"][0]["diff_id"] == "D001"
    assert quality_payload["low_similarity_diffs"][0]["diff_id"] == "D001"


def _mixed_review_diff(diff_id: str = "DREVIEW") -> DiffItem:
    return DiffItem(
        diff_id=diff_id,
        diff_type="MODIFY",
        title="混合差异",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                text="old",
                highlight_type="MODIFY",
            ),
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=5, y0=6, x1=7, y1=8),
                text="removed",
                highlight_type="DELETE",
            ),
        ],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                text="new",
                highlight_type="MODIFY",
            ),
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=9, y0=10, x1=11, y1=12),
                text="added",
                highlight_type="ADD",
            ),
        ],
    )


def test_task_read_returns_normalized_audit_items_without_persisting_legacy_projection(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    diff = _mixed_review_diff()
    diff.review_status = "CONFIRMED"
    diff.review_comment = "legacy"
    diff.reviewed_by = "legacy-user"
    save_task(CompareTask(task_id="TLEGACYAUDIT", status="COMPLETED", diffs=[diff]))

    response = TestClient(app).get("/api/compare/TLEGACYAUDIT")

    assert response.status_code == 200, response.text
    audit_items = response.json()["audit_items"]
    assert {item["audit_item_id"] for item in audit_items} == {
        "DREVIEW:ADD",
        "DREVIEW:DELETE",
        "DREVIEW:MODIFY",
    }
    assert {item["review_status"] for item in audit_items} == {"CONFIRMED"}
    assert response.json()["reviewed_count"] == 3
    assert response.json()["confirmed_count"] == 3
    assert load_task("TLEGACYAUDIT").audit_item_reviews == {}


def test_read_paths_project_partial_canonical_reviews_without_persisting(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    diff = _mixed_review_diff()
    diff.review_status = "CONFIRMED"
    diff.review_comment = "legacy"
    diff.review_flags = ["SAME_CLAUSE_NO_LOW_SIMILARITY"]
    diff.match_score = 40
    save_task(
        CompareTask(
            task_id="TPARTIALREAD",
            status="COMPLETED",
            revision=7,
            diffs=[diff],
            audit_item_reviews={
                "DREVIEW:ADD": AuditItemReview(review_status="FALSE_POSITIVE", review_comment="canonical")
            },
        )
    )
    before = load_task("TPARTIALREAD")
    client = TestClient(app)

    diff_response = client.get("/api/compare/TPARTIALREAD/diffs")
    task_response = client.get("/api/compare/TPARTIALREAD")
    quality_response = client.get("/api/compare/TPARTIALREAD/quality")

    assert diff_response.status_code == task_response.status_code == quality_response.status_code == 200
    assert diff_response.json()["diffs"][0]["review_status"] == "NEEDS_REVIEW"
    assert diff_response.json()["diffs"][0]["review_comment"] == ""
    assert {item["review_status"] for item in task_response.json()["audit_items"]} == {
        "CONFIRMED",
        "FALSE_POSITIVE",
    }
    assert quality_response.json()["low_confidence_diffs"][0]["review_status"] == "NEEDS_REVIEW"
    assert quality_response.json()["low_similarity_diffs"][0]["review_status"] == "NEEDS_REVIEW"

    persisted = load_task("TPARTIALREAD")
    assert persisted.revision == before.revision
    assert persisted.diffs[0].review_status == "CONFIRMED"
    assert persisted.diffs[0].review_comment == "legacy"
    assert set(persisted.audit_item_reviews) == {"DREVIEW:ADD"}


def test_single_audit_item_review_persists_normalized_map_and_returns_item_stats_revision(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    diff = _mixed_review_diff()
    diff.review_status = "CONFIRMED"
    task = CompareTask(
        task_id="TITEMREVIEW",
        status="COMPLETED",
        report_revision=4,
        diffs=[diff],
        audit_item_reviews={"DREVIEW:ADD": AuditItemReview(review_status="FALSE_POSITIVE")},
    )
    save_task(task)

    response = TestClient(app).patch(
        "/api/compare/TITEMREVIEW/audit-items/DREVIEW:DELETE/review",
        json={"review_status": "IGNORED", "review_comment": " ignore me ", "reviewed_by": "spoof"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"task_id", "audit_item", "review_stats", "report_revision"}
    assert payload["task_id"] == "TITEMREVIEW"
    assert payload["audit_item"]["audit_item_id"] == "DREVIEW:DELETE"
    assert payload["audit_item"]["review_status"] == "IGNORED"
    assert payload["audit_item"]["review_comment"] == "ignore me"
    assert payload["audit_item"]["reviewed_by"] == ADMIN.sub
    assert payload["report_revision"] == 5
    assert payload["review_stats"] == {
        "total_count": 3,
        "reviewed_count": 3,
        "confirmed_count": 1,
        "false_positive_count": 1,
        "manual_review_count": 0,
        "ignored_count": 1,
        "review_unit": "audit_item",
    }

    persisted = load_task("TITEMREVIEW")
    assert set(persisted.audit_item_reviews) == {
        "DREVIEW:ADD",
        "DREVIEW:DELETE",
        "DREVIEW:MODIFY",
    }
    assert persisted.audit_item_reviews["DREVIEW:MODIFY"].review_status == "CONFIRMED"
    assert persisted.diffs[0].review_status == "NEEDS_REVIEW"


def test_single_audit_item_review_keeps_siblings_independent_and_unreviewed_omitted(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TSIBLINGREVIEW",
            status="COMPLETED",
            diffs=[_mixed_review_diff()],
            audit_item_reviews={"DREVIEW:ADD": AuditItemReview(review_status="CONFIRMED")},
        )
    )

    response = TestClient(app).patch(
        "/api/compare/TSIBLINGREVIEW/audit-items/DREVIEW:DELETE/review",
        json={"review_status": "FALSE_POSITIVE"},
    )

    assert response.status_code == 200, response.text
    persisted = load_task("TSIBLINGREVIEW")
    assert persisted.audit_item_reviews["DREVIEW:ADD"].review_status == "CONFIRMED"
    assert persisted.audit_item_reviews["DREVIEW:DELETE"].review_status == "FALSE_POSITIVE"
    assert "DREVIEW:MODIFY" not in persisted.audit_item_reviews
    assert persisted.diffs[0].review_status == "NEEDS_REVIEW"


def test_diff_review_bulk_updates_all_children_once_and_projects_equal_status(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TBULKREVIEW",
            status="COMPLETED",
            report_revision=8,
            diffs=[_mixed_review_diff()],
        )
    )

    response = TestClient(app).patch(
        "/api/compare/TBULKREVIEW/diffs/DREVIEW/review",
        json={"review_status": "CONFIRMED", "review_comment": "bulk"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["diff"]["review_status"] == "CONFIRMED"
    assert payload["review_stats"]["total_count"] == 3
    assert payload["review_stats"]["reviewed_count"] == 3
    persisted = load_task("TBULKREVIEW")
    assert persisted.report_revision == 9
    assert len(persisted.audit_item_reviews) == 3
    assert {review.review_status for review in persisted.audit_item_reviews.values()} == {"CONFIRMED"}


def test_repeated_review_requests_each_increment_report_revision_once(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TREPEATREVIEW",
            status="COMPLETED",
            report_revision=0,
            diffs=[_mixed_review_diff()],
        )
    )
    client = TestClient(app)

    first = client.patch(
        "/api/compare/TREPEATREVIEW/audit-items/DREVIEW:ADD/review",
        json={"review_status": "CONFIRMED"},
    )
    assert first.status_code == 200
    assert first.json()["report_revision"] == 1
    second = client.patch(
        "/api/compare/TREPEATREVIEW/audit-items/DREVIEW:ADD/review",
        json={"review_status": "CONFIRMED"},
    )

    assert second.status_code == 200
    assert second.json()["report_revision"] == 2


def test_unknown_audit_item_review_does_not_mutate_task(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(CompareTask(task_id="TUNKNOWNITEM", status="COMPLETED", diffs=[_mixed_review_diff()]))
    before = load_task("TUNKNOWNITEM")

    response = TestClient(app).patch(
        "/api/compare/TUNKNOWNITEM/audit-items/unknown:ADD/review",
        json={"review_status": "CONFIRMED"},
    )

    assert response.status_code == 404
    after = load_task("TUNKNOWNITEM")
    assert after.revision == before.revision
    assert after.report_revision == before.report_revision
    assert after.audit_item_reviews == {}


def test_concurrent_audit_item_reviews_do_not_lose_sibling_updates(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TCONCURRENTREVIEW",
            status="COMPLETED",
            report_revision=0,
            diffs=[_mixed_review_diff()],
        )
    )

    def submit(item_id: str, status: str) -> int:
        with TestClient(app) as client:
            return client.patch(
                f"/api/compare/TCONCURRENTREVIEW/audit-items/{item_id}/review",
                json={"review_status": status},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                lambda args: submit(*args),
                [("DREVIEW:ADD", "CONFIRMED"), ("DREVIEW:DELETE", "FALSE_POSITIVE")],
            )
        )

    assert statuses == [200, 200]
    persisted = load_task("TCONCURRENTREVIEW")
    assert persisted.audit_item_reviews["DREVIEW:ADD"].review_status == "CONFIRMED"
    assert persisted.audit_item_reviews["DREVIEW:DELETE"].review_status == "FALSE_POSITIVE"
    assert persisted.report_revision == 2


def test_review_persistence_failure_does_not_partially_mutate_task(tmp_path: Path, monkeypatch) -> None:
    configure_storage(tmp_path)
    repository = LocalJsonTaskRepository(settings)
    repository.save_compare_task(
        _owned_task(
            CompareTask(
                task_id="TFAILEDREVIEWWRITE",
                status="COMPLETED",
                report_revision=2,
                diffs=[_mixed_review_diff()],
            )
        )
    )
    service = CompareReviewService(repository=repository)
    original = repository.load_compare_task("TFAILEDREVIEWWRITE")

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(repository, "_write_task", fail_write)

    with pytest.raises(OSError, match="disk full"):
        service.update_audit_item_review(original, "DREVIEW:ADD", "CONFIRMED")

    persisted = LocalJsonTaskRepository(settings).load_compare_task("TFAILEDREVIEWWRITE")
    assert persisted.audit_item_reviews == {}
    assert persisted.audit_item_reviews_normalized is False
    assert persisted.report_revision == 2
    assert persisted.diffs[0].review_status == "UNREVIEWED"


@pytest.mark.parametrize(
    "request_payload", [{"review_status": "UNREVIEWED"}, {"review_status": "UNREVIEWED", "review_comment": ""}]
)
def test_unreviewed_review_always_removes_canonical_entry_but_keeps_normalized_marker(
    tmp_path: Path,
    request_payload: dict[str, str],
) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TRESETREVIEW",
            status="COMPLETED",
            report_revision=4,
            diffs=[_mixed_review_diff()],
            audit_item_reviews={
                "DREVIEW:ADD": AuditItemReview(
                    review_status="IGNORED",
                    review_comment="旧" * 80_000,
                )
            },
            audit_item_reviews_normalized=True,
        )
    )

    response = TestClient(app).patch(
        "/api/compare/TRESETREVIEW/audit-items/DREVIEW:ADD/review",
        json=request_payload,
    )

    assert response.status_code == 200, response.text
    assert response.json()["report_revision"] == 5
    assert response.json()["audit_item"]["review_status"] == "UNREVIEWED"
    assert response.json()["audit_item"]["review_comment"] == ""
    assert response.json()["review_stats"]["reviewed_count"] == 0
    persisted = load_task("TRESETREVIEW")
    assert persisted.audit_item_reviews == {}
    assert persisted.audit_item_reviews_normalized is True
    assert persisted.report_revision == 5
    assert persisted.diffs[0].review_status == "UNREVIEWED"


def test_bulk_unreviewed_review_removes_all_child_entries_and_updates_stats_once(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TRESETBULKREVIEW",
            status="COMPLETED",
            report_revision=7,
            diffs=[_mixed_review_diff()],
            audit_item_reviews={
                f"DREVIEW:{diff_type}": AuditItemReview(
                    review_status="IGNORED",
                    review_comment=f"{diff_type} historical comment",
                )
                for diff_type in ("ADD", "DELETE", "MODIFY")
            },
            audit_item_reviews_normalized=True,
        )
    )

    response = TestClient(app).patch(
        "/api/compare/TRESETBULKREVIEW/diffs/DREVIEW/review",
        json={"review_status": "UNREVIEWED"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["diff"]["review_status"] == "UNREVIEWED"
    assert response.json()["review_stats"]["reviewed_count"] == 0
    persisted = load_task("TRESETBULKREVIEW")
    assert persisted.audit_item_reviews == {}
    assert persisted.audit_item_reviews_normalized is True
    assert persisted.report_revision == 8
    assert persisted.diffs[0].review_status == "UNREVIEWED"


def test_api_exposes_ocr_quality_summary(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TOCRAPI",
            status="COMPLETED",
            diff_count=1,
            ocr_quality_summary=TaskOcrQualitySummary(
                status="LOW_TEXT_CONFIDENCE",
                requires_review=True,
                page_count_by_status={"LOW_TEXT_CONFIDENCE": 1},
                risk_page_count=1,
                affected_diff_count=1,
                profiles=[
                    PageOcrQualityProfile(
                        side="original",
                        page_no=1,
                        status="LOW_TEXT_CONFIDENCE",
                        score=0.75,
                        reasons=["LOW_AVG_CONFIDENCE"],
                        affected_diff_ids=["D001"],
                    )
                ],
            ),
        )
    )

    client = TestClient(app)
    response = client.get("/api/compare/TOCRAPI")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ocr_quality_summary"]["status"] == "LOW_TEXT_CONFIDENCE"
    assert payload["ocr_quality_summary"]["risk_page_count"] == 1
    assert payload["ocr_quality_summary"]["profiles"][0]["affected_diff_ids"] == ["D001"]


def test_compare_task_response_includes_ocr_remediation_summary() -> None:
    task = CompareTask(task_id="task-api", status="COMPLETED")
    task.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="OK",
        attempted_action_count=1,
        successful_action_count=1,
        unresolved_action_count=0,
        risk_reduced_diff_count=1,
        actions=[
            OcrRemediationAction(
                action_id="original:1:diff-1:RELOCATE_EVIDENCE",
                action_type="RELOCATE_EVIDENCE",
                reason="EVIDENCE_UNRELIABLE",
                side="original",
                page_no=1,
                diff_id="diff-1",
                status="SUCCEEDED",
                before_quality={"max_confidence": 0.46, "methods": ["block_fallback"]},
                after_quality={"max_confidence": 0.98, "methods": ["text_exact"]},
                changed_evidence=True,
                changed_diff_text=False,
                review_flags_added=["OCR_REMEDIATION_EVIDENCE_RELOCATED"],
            )
        ],
    )

    response = compare_task_response(task)

    summary = response.ocr_remediation_summary
    assert summary is not None
    assert summary.status == "OK"
    assert summary.attempted_action_count == 1
    assert summary.successful_action_count == 1
    assert summary.unresolved_action_count == 0
    assert summary.risk_reduced_diff_count == 1

    action = summary.actions[0]
    assert action.action_id == "original:1:diff-1:RELOCATE_EVIDENCE"
    assert action.action_type == "RELOCATE_EVIDENCE"
    assert action.reason == "EVIDENCE_UNRELIABLE"
    assert action.side == "original"
    assert action.page_no == 1
    assert action.diff_id == "diff-1"
    assert action.status == "SUCCEEDED"
    assert action.before_quality == {"max_confidence": 0.46, "methods": ["block_fallback"]}
    assert action.after_quality == {"max_confidence": 0.98, "methods": ["text_exact"]}
    assert action.changed_evidence is True
    assert action.changed_diff_text is False
    assert action.review_flags_added == ["OCR_REMEDIATION_EVIDENCE_RELOCATED"]


def test_compare_task_response_projects_item_level_ocr_and_remediation_context() -> None:
    task = CompareTask(
        task_id="task-audit-context",
        status="COMPLETED",
        diffs=[DiffItem(diff_id="DCTX", diff_type="ADD", compare_text="added")],
        ocr_quality_summary=TaskOcrQualitySummary(
            requires_review=True,
            profiles=[
                PageOcrQualityProfile(
                    side="compare",
                    page_no=2,
                    status="UNRELIABLE",
                    reasons=["OCR_EMPTY"],
                    affected_diff_ids=["DCTX"],
                )
            ],
        ),
        ocr_remediation_summary=TaskOcrRemediationSummary(
            actions=[
                OcrRemediationAction(
                    action_id="compare:2:DCTX:ESCALATE_MANUAL_REVIEW",
                    action_type="ESCALATE_MANUAL_REVIEW",
                    reason="PAGE_UNRELIABLE",
                    status="MANUAL_REVIEW_REQUIRED",
                    diff_id="DCTX",
                    side="compare",
                    page_no=2,
                )
            ]
        ),
    )

    item = compare_task_response(task).audit_items[0]

    assert item.ocr_context.affected is True
    assert item.ocr_context.scope == "DIFF"
    assert item.ocr_context.statuses == ["UNRELIABLE"]
    assert item.ocr_context.reasons == ["OCR_EMPTY"]
    assert item.remediation_context.action_ids == ["compare:2:DCTX:ESCALATE_MANUAL_REVIEW"]
    assert item.remediation_context.scope == "DIFF"
    assert item.remediation_context.statuses == ["MANUAL_REVIEW_REQUIRED"]
    assert item.remediation_context.requires_manual_review is True


def test_compare_api_serializes_canonical_audit_and_diff_structural_evidence_fields(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TSTRUCTURALEVIDENCE",
            status="COMPLETED",
            diffs=[
                DiffItem(
                    diff_id="DTYPED",
                    diff_type="ADD",
                    source_type="table",
                    section_type="appendix",
                    section_path=["附件一", "报价表"],
                    match_confidence="LOW",
                    structural_flags=["TABLE_STRUCTURE", "ROW_REORDERED"],
                    compare_text="新增报价行",
                    compare_evidence=[
                        EvidenceBox(
                            page_no=2,
                            bbox=BBox(x0=10, y0=20, x1=110, y1=40),
                            text="新增报价行",
                            highlight_type="ADD",
                        )
                    ],
                ),
                DiffItem(
                    diff_id="DLEGACYDEFAULT",
                    diff_type="DELETE",
                    original_text="历史无定位差异",
                ),
            ],
        )
    )
    client = TestClient(app)

    task_response = client.get("/api/compare/TSTRUCTURALEVIDENCE")
    diffs_response = client.get("/api/compare/TSTRUCTURALEVIDENCE/diffs")

    assert task_response.status_code == diffs_response.status_code == 200
    items = {item["diff_id"]: item for item in task_response.json()["audit_items"]}
    typed = items["DTYPED"]
    assert typed["section_type"] == "appendix"
    assert typed["section_path"] == ["附件一", "报价表"]
    assert typed["match_confidence"] == "LOW"
    assert typed["structural_flags"] == ["TABLE_STRUCTURE", "ROW_REORDERED"]
    assert isinstance(typed["section_path"], list)
    assert isinstance(typed["structural_flags"], list)

    legacy = items["DLEGACYDEFAULT"]
    assert legacy["section_type"] == ""
    assert legacy["section_path"] == []
    assert legacy["match_confidence"] == ""
    assert legacy["structural_flags"] == []
    assert legacy["evidence_state"] == "UNLOCATED"

    diffs = {diff["diff_id"]: diff for diff in diffs_response.json()["diffs"]}
    assert diffs["DTYPED"]["section_type"] == "appendix"
    assert diffs["DTYPED"]["section_path"] == ["附件一", "报价表"]
    assert diffs["DTYPED"]["match_confidence"] == "LOW"
    assert diffs["DTYPED"]["structural_flags"] == ["TABLE_STRUCTURE", "ROW_REORDERED"]
    assert diffs["DLEGACYDEFAULT"]["section_type"] == ""
    assert diffs["DLEGACYDEFAULT"]["section_path"] == []
    assert diffs["DLEGACYDEFAULT"]["match_confidence"] == ""
    assert diffs["DLEGACYDEFAULT"]["structural_flags"] == []


def test_review_api_accepts_comment_at_limit_and_rejects_longer_comment(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TREVIEWCOMMENTLIMIT",
            status="COMPLETED",
            diffs=[DiffItem(diff_id="DCOMMENT", diff_type="ADD", compare_text="added")],
        )
    )
    client = TestClient(app)
    allowed_comment = "复" * api_schemas.MAX_REVIEW_COMMENT_LENGTH

    accepted = client.patch(
        "/api/compare/TREVIEWCOMMENTLIMIT/audit-items/DCOMMENT:ADD/review",
        json={"review_status": "CONFIRMED", "review_comment": allowed_comment},
    )
    rejected = client.patch(
        "/api/compare/TREVIEWCOMMENTLIMIT/audit-items/DCOMMENT:ADD/review",
        json={"review_status": "CONFIRMED", "review_comment": f"{allowed_comment}超"},
    )

    assert api_schemas.MAX_REVIEW_COMMENT_LENGTH == 2000
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["audit_item"]["review_comment"] == allowed_comment
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["type"] == "string_too_long"


def test_review_state_updates_preserve_oversized_legacy_comments_when_omitted_and_allow_explicit_clear(
    tmp_path: Path,
) -> None:
    configure_storage(tmp_path)
    legacy_comment = "旧" * 80_000
    save_task(
        CompareTask(
            task_id="TLEGACYLONGCOMMENT",
            status="COMPLETED",
            diffs=[_mixed_review_diff()],
            audit_item_reviews={
                "DREVIEW:ADD": AuditItemReview(review_status="CONFIRMED", review_comment=legacy_comment),
                "DREVIEW:DELETE": AuditItemReview(review_status="CONFIRMED", review_comment="delete comment"),
                "DREVIEW:MODIFY": AuditItemReview(review_status="CONFIRMED", review_comment="modify comment"),
            },
            audit_item_reviews_normalized=True,
        )
    )
    client = TestClient(app)

    omitted = client.patch(
        "/api/compare/TLEGACYLONGCOMMENT/audit-items/DREVIEW:ADD/review",
        json={"review_status": "IGNORED"},
    )
    bulk_omitted = client.patch(
        "/api/compare/TLEGACYLONGCOMMENT/diffs/DREVIEW/review",
        json={"review_status": "NEEDS_REVIEW"},
    )

    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["audit_item"]["review_comment"] == legacy_comment
    assert bulk_omitted.status_code == 200, bulk_omitted.text
    persisted = load_task("TLEGACYLONGCOMMENT")
    assert persisted.audit_item_reviews["DREVIEW:ADD"].review_comment == legacy_comment
    assert persisted.audit_item_reviews["DREVIEW:DELETE"].review_comment == "delete comment"
    assert persisted.audit_item_reviews["DREVIEW:MODIFY"].review_comment == "modify comment"

    cleared = client.patch(
        "/api/compare/TLEGACYLONGCOMMENT/audit-items/DREVIEW:ADD/review",
        json={"review_status": "CONFIRMED", "review_comment": ""},
    )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["audit_item"]["review_comment"] == ""
    assert load_task("TLEGACYLONGCOMMENT").audit_item_reviews["DREVIEW:ADD"].review_comment == ""


def test_compare_api_projects_legacy_nonfinite_match_values_without_mutating_storage(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    diff = DiffItem(diff_id="DNONFINITE", diff_type="ADD", compare_text="added")
    diff.match_score = float("nan")
    diff.match_confidence = float("inf")  # type: ignore[assignment]
    save_task(CompareTask(task_id="TNONFINITEMATCH", status="COMPLETED", diffs=[diff]))
    client = TestClient(app, raise_server_exceptions=False)

    task_response = client.get("/api/compare/TNONFINITEMATCH")
    diffs_response = client.get("/api/compare/TNONFINITEMATCH/diffs")
    review_response = client.patch(
        "/api/compare/TNONFINITEMATCH/diffs/DNONFINITE/review",
        json={"review_status": "CONFIRMED"},
    )

    for response in (task_response, diffs_response, review_response):
        assert response.status_code == 200, response.text
        json.loads(
            response.content,
            parse_constant=lambda value: (_ for _ in ()).throw(AssertionError(f"invalid JSON number: {value}")),
        )
    assert task_response.json()["audit_items"][0]["match_confidence"] is None
    assert diffs_response.json()["diffs"][0]["match_score"] is None
    assert diffs_response.json()["diffs"][0]["match_confidence"] is None
    assert review_response.json()["diff"]["match_score"] is None
    assert review_response.json()["diff"]["match_confidence"] is None
    persisted = load_task("TNONFINITEMATCH")
    assert math.isnan(persisted.diffs[0].match_score)
    assert math.isinf(persisted.diffs[0].match_confidence)


def test_diff_and_audit_api_responses_filter_invalid_historical_evidence_without_mutation(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    valid = EvidenceBox(
        page_no=2,
        bbox=BBox(x0=10, y0=20, x1=30, y1=40),
        text="valid evidence",
        highlight_type="ADD",
    )
    invalid_add = [
        EvidenceBox(
            page_no=0,
            bbox=BBox(x0=1, y0=2, x1=3, y1=4),
            text="page zero",
            highlight_type="ADD",
        ),
        EvidenceBox(
            page_no=-1,
            bbox=BBox(x0=1, y0=2, x1=3, y1=4),
            text="negative page",
            highlight_type="ADD",
        ),
        EvidenceBox(
            page_no=2,
            bbox=BBox(x0=1, y0=2, x1=1, y1=4),
            text="zero area",
            highlight_type="ADD",
        ),
        EvidenceBox(
            page_no=2,
            bbox=BBox(
                x0=0,
                y0=0,
                x1=0,
                y1=0,
                normalized=NormalizedBBox(x0=0, y0=0, x1=1, y1=1),
            ),
            text="normalized only",
            highlight_type="ADD",
        ),
        EvidenceBox(
            page_no=2,
            bbox=BBox(x0=1, y0=2, x1=float("inf"), y1=4),
            text="infinite coordinate",
            highlight_type="ADD",
        ),
        EvidenceBox(
            page_no=2,
            bbox=BBox(x0=1, y0=2, x1=float("nan"), y1=4),
            text="nan coordinate",
            highlight_type="ADD",
        ),
    ]
    invalid_delete = [item.model_copy(update={"highlight_type": "DELETE"}, deep=True) for item in invalid_add]
    save_task(
        CompareTask(
            task_id="THISTORICALINVALIDEVIDENCE",
            status="COMPLETED",
            diffs=[
                DiffItem(
                    diff_id="DMIXED",
                    diff_type="ADD",
                    compare_evidence=[valid, *invalid_add],
                ),
                DiffItem(
                    diff_id="DALLINVALID",
                    diff_type="DELETE",
                    original_evidence=invalid_delete,
                ),
            ],
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    task_response = client.get("/api/compare/THISTORICALINVALIDEVIDENCE")
    diffs_response = client.get("/api/compare/THISTORICALINVALIDEVIDENCE/diffs")
    diff_review_response = client.patch(
        "/api/compare/THISTORICALINVALIDEVIDENCE/diffs/DALLINVALID/review",
        json={"review_status": "CONFIRMED"},
    )
    item_review_response = client.patch(
        "/api/compare/THISTORICALINVALIDEVIDENCE/audit-items/DALLINVALID:DELETE/review",
        json={"review_status": "NEEDS_REVIEW"},
    )

    for response in [task_response, diffs_response, diff_review_response, item_review_response]:
        assert response.status_code == 200, response.text
        json.loads(
            response.content,
            parse_constant=lambda value: (_ for _ in ()).throw(AssertionError(f"invalid JSON number: {value}")),
        )
        assert b"NaN" not in response.content
        assert b"Infinity" not in response.content

    task_items = {item["diff_id"]: item for item in task_response.json()["audit_items"]}
    assert [item["text"] for item in task_items["DMIXED"]["compare_evidence"]] == ["valid evidence"]
    assert task_items["DALLINVALID"]["original_evidence"] == []
    assert task_items["DALLINVALID"]["evidence_state"] == "UNLOCATED"
    assert "page zero" in task_items["DALLINVALID"]["original_text"]

    diffs = {diff["diff_id"]: diff for diff in diffs_response.json()["diffs"]}
    assert [item["text"] for item in diffs["DMIXED"]["compare_evidence"]] == ["valid evidence"]
    assert diffs["DALLINVALID"]["original_evidence"] == []
    assert diff_review_response.json()["diff"]["original_evidence"] == []
    assert item_review_response.json()["audit_item"]["original_evidence"] == []

    persisted = load_task("THISTORICALINVALIDEVIDENCE")
    assert len(persisted.diffs[0].compare_evidence) == 7
    assert len(persisted.diffs[1].original_evidence) == 6
    assert any(math.isnan(item.bbox.x1) for item in persisted.diffs[1].original_evidence)


def test_quality_summary_includes_ocr_quality_counts(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TOCRQUALITYAPI",
            status="COMPLETED",
            ocr_quality_summary=TaskOcrQualitySummary(
                status="TABLE_RISK",
                requires_review=True,
                page_count_by_status={"TABLE_RISK": 1},
                risk_page_count=1,
                affected_diff_count=2,
                profiles=[
                    PageOcrQualityProfile(
                        side="compare",
                        page_no=2,
                        status="TABLE_RISK",
                        score=0.8,
                        reasons=["TABLE_CELL_UNMATCHED"],
                    )
                ],
            ),
        )
    )

    client = TestClient(app)
    response = client.get("/api/compare/TOCRQUALITYAPI/quality")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ocr_quality_summary"]["status"] == "TABLE_RISK"
    assert payload["ocr_risk_page_count"] == 1
    assert payload["ocr_affected_diff_count"] == 2


def test_api_hides_legacy_task_without_owner(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    task_dir = settings.tasks_dir / "TOCRLEGACYAPI"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "TOCRLEGACYAPI",
                "status": "COMPLETED",
            }
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    task_response = client.get("/api/compare/TOCRLEGACYAPI")

    assert task_response.status_code == 404, task_response.text

    quality_response = client.get("/api/compare/TOCRLEGACYAPI/quality")

    assert quality_response.status_code == 404, quality_response.text


def test_compare_quality_summary_includes_ocr_remediation_counts() -> None:
    task = CompareTask(task_id="task-quality", status="COMPLETED")
    task.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="MANUAL_REVIEW_REQUIRED",
        attempted_action_count=2,
        unresolved_action_count=2,
        manual_review_required_count=1,
    )

    summary = CompareQualityService().build_summary(task)

    assert summary["ocr_remediation_summary"]["status"] == "MANUAL_REVIEW_REQUIRED"
    assert summary["ocr_remediation_action_count"] == 2
    assert summary["ocr_remediation_unresolved_count"] == 2
    assert summary["manual_review_required_count"] == 1


def test_api_report_excludes_ignored_audit_item_after_review(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(original, ["1. Payment", "Buyer shall pay within 30 days.", "2. Delivery"])
    make_pdf(compare, ["1. Payment", "Buyer shall pay within 45 days.", "2. Delivery"])
    save_task(
        CompareTask(
            task_id="TIGNOREREPORT",
            status="COMPLETED",
            original_filename="original.pdf",
            compare_filename="compare.pdf",
            original_pdf_path=str(original),
            compare_pdf_path=str(compare),
            diffs=[
                DiffItem(
                    diff_id="D001",
                    diff_type="MODIFY",
                    title="混合付款",
                    original_evidence=[
                        EvidenceBox(
                            page_no=1,
                            bbox=BBox(x0=72, y0=88, x1=180, y1=105),
                            text="30 days",
                            highlight_type="MODIFY",
                        ),
                        EvidenceBox(
                            page_no=1,
                            bbox=BBox(x0=72, y0=120, x1=180, y1=140),
                            text="旧签署说明",
                            highlight_type="DELETE",
                        ),
                    ],
                    compare_evidence=[
                        EvidenceBox(
                            page_no=1,
                            bbox=BBox(x0=72, y0=88, x1=180, y1=105),
                            text="45 days",
                            highlight_type="MODIFY",
                        ),
                        EvidenceBox(
                            page_no=1,
                            bbox=BBox(x0=72, y0=140, x1=180, y1=160),
                            text="新增发票说明",
                            highlight_type="ADD",
                        ),
                    ],
                ),
                DiffItem(
                    diff_id="D002",
                    diff_type="ADD",
                    title="交付条款",
                    compare_evidence=[
                        EvidenceBox(
                            page_no=1,
                            bbox=BBox(x0=72, y0=110, x1=180, y1=130),
                            text="2. Delivery",
                            highlight_type="ADD",
                        ),
                    ],
                ),
            ],
        )
    )

    client = TestClient(app)
    review_response = client.patch(
        "/api/compare/TIGNOREREPORT/audit-items/D001:DELETE/review",
        json={"review_status": "IGNORED", "reviewed_by": "legal"},
    )
    assert review_response.status_code == 200, review_response.text

    report_response = client.get("/api/compare/TIGNOREREPORT/report")

    assert report_response.status_code == 200, report_response.text
    with fitz.open(stream=report_response.content, filetype="pdf") as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)
    assert "D001:DELETE" not in report_text
    assert "旧签署说明" not in report_text
    assert "新增发票说明" in report_text
    assert "30 days" in report_text
    assert "45 days" in report_text
    assert "交付条款" in report_text


def test_api_rejects_review_for_processing_task(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TPROCESSING",
            status="PROCESSING",
            diffs=[DiffItem(diff_id="D001", diff_type="ADD", compare_text="新增")],
        )
    )

    client = TestClient(app)
    response = client.patch(
        "/api/compare/TPROCESSING/diffs/D001/review",
        json={"review_status": "CONFIRMED"},
    )

    assert response.status_code == 409


def test_api_review_missing_diff_returns_404(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(CompareTask(task_id="TMISSINGDIFF", status="COMPLETED"))

    client = TestClient(app)
    response = client.patch(
        "/api/compare/TMISSINGDIFF/diffs/D404/review",
        json={"review_status": "IGNORED"},
    )

    assert response.status_code == 404


def test_concurrent_real_report_requests_generate_once_without_changing_report_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_storage(tmp_path)
    save_task(CompareTask(task_id="TAPICONCURRENTREPORT", status="COMPLETED", report_revision=3))
    generator_entered = threading.Event()
    release_generator = threading.Event()
    generator = ApiRecordingReportGenerator(entered=generator_entered, release=release_generator)
    report_store = default_compare_task_application.report_store
    monkeypatch.setattr(report_store, "generator", generator)
    start = threading.Barrier(10)

    def download() -> tuple[int, bytes]:
        start.wait(timeout=5)
        with TestClient(app) as client:
            response = client.get("/api/compare/TAPICONCURRENTREPORT/report")
            return response.status_code, response.content

    pool = ThreadPoolExecutor(max_workers=10)
    futures = [pool.submit(download) for _index in range(10)]
    try:
        assert generator_entered.wait(timeout=5)
        assert report_store.lock_registry.wait_for_ref_count("TAPICONCURRENTREPORT", 3, 10, timeout=5)
        assert len(generator.calls) == 1
        final_path = settings.tasks_dir / "TAPICONCURRENTREPORT" / "reports" / "contract_compare_report-r3.pdf"
        assert not final_path.exists()
        blocked_task = load_task("TAPICONCURRENTREPORT")
        assert blocked_task.report_revision == 3
        assert blocked_task.report_pdf_path is None
        release_generator.set()
        responses = [future.result(timeout=10) for future in futures]
    finally:
        release_generator.set()
        pool.shutdown(wait=True)

    assert {status for status, _content in responses} == {200}
    assert {content for _status, content in responses} == {b"%PDF-api-revision-3"}
    assert len(generator.calls) == 1
    persisted = load_task("TAPICONCURRENTREPORT")
    assert persisted.report_revision == 3
    assert Path(persisted.report_pdf_path or "").name == "contract_compare_report-r3.pdf"
    assert report_store.lock_registry.ref_count("TAPICONCURRENTREPORT", 3) == 0


def test_review_creates_next_revision_report_and_preserves_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TAPIREVIEWREPORT",
            status="COMPLETED",
            report_revision=1,
            diffs=[_mixed_review_diff()],
        )
    )
    generator = ApiRecordingReportGenerator()
    monkeypatch.setattr(default_compare_task_application.report_store, "generator", generator)
    client = TestClient(app)

    first = client.get("/api/compare/TAPIREVIEWREPORT/report")
    review = client.patch(
        "/api/compare/TAPIREVIEWREPORT/audit-items/DREVIEW:ADD/review",
        json={"review_status": "CONFIRMED"},
    )
    second = client.get("/api/compare/TAPIREVIEWREPORT/report")

    assert first.status_code == 200
    assert review.status_code == 200
    assert review.json()["report_revision"] == 2
    assert second.status_code == 200
    reports_dir = settings.tasks_dir / "TAPIREVIEWREPORT" / "reports"
    first_path = reports_dir / "contract_compare_report-r1.pdf"
    second_path = reports_dir / "contract_compare_report-r2.pdf"
    assert first_path.read_bytes() == b"%PDF-api-revision-1"
    assert second_path.read_bytes() == b"%PDF-api-revision-2"
    assert load_task("TAPIREVIEWREPORT").report_revision == 2
    assert Path(load_task("TAPIREVIEWREPORT").report_pdf_path or "") == second_path
    assert [revision for revision, _path in generator.calls] == [1, 2]


def test_report_path_persistence_failure_keeps_published_report_and_prior_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_storage(tmp_path)
    save_task(CompareTask(task_id="TAPIPERSISTFAIL", status="COMPLETED", report_revision=2))
    generator = ApiRecordingReportGenerator()
    monkeypatch.setattr(default_compare_task_application.report_store, "generator", generator)
    prior_task = load_task("TAPIPERSISTFAIL").model_copy(update={"report_revision": 1})
    prior_path = default_compare_task_application.report_store.ensure_report(prior_task)
    prior_manifest = prior_path.with_suffix(".manifest.json")
    prior_report_bytes = prior_path.read_bytes()
    prior_manifest_bytes = prior_manifest.read_bytes()
    monkeypatch.setattr(
        default_compare_task_application.repository,
        "update_compare_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("task report path persistence failed")),
    )

    response = TestClient(app).get("/api/compare/TAPIPERSISTFAIL/report")

    assert response.status_code == 500
    current_path = settings.tasks_dir / "TAPIPERSISTFAIL" / "reports" / "contract_compare_report-r2.pdf"
    assert current_path.read_bytes() == b"%PDF-api-revision-2"
    assert current_path.with_suffix(".manifest.json").is_file()
    assert prior_path.read_bytes() == prior_report_bytes
    assert prior_manifest.read_bytes() == prior_manifest_bytes
    persisted = load_task("TAPIPERSISTFAIL")
    assert persisted.report_revision == 2
    assert persisted.report_pdf_path is None


def test_cors_allows_frontend_dev_origin() -> None:
    client = TestClient(app)
    response = client.options(
        "/api/compare",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
