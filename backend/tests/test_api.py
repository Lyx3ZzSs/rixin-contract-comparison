from __future__ import annotations

import asyncio
import io
from pathlib import Path

import fitz
import pytest
from fastapi import UploadFile

from app.application.compare_tasks import CompareTaskApplication, requires_diagnostic_retention
from app.config import Settings
from app.infrastructure.artifact_store import ArtifactStore
from app.infrastructure.report_store import ReportStore
from app.infrastructure.task_repository import SQLiteTaskRepository
from app.infrastructure.task_runner import QueuedTaskRunner
from app.models import CompareTask, TaskOcrQualitySummary
from app.utils.file_utils import FileValidationError

from auth_helpers import ADMIN


def _pdf_bytes() -> bytes:
    document = fitz.open()
    document.new_page()
    payload = document.tobytes()
    document.close()
    return payload


def _application(
    tmp_path: Path,
) -> tuple[CompareTaskApplication, SQLiteTaskRepository, QueuedTaskRunner, ArtifactStore]:
    settings = Settings(storage_dir=tmp_path / "storage", task_runner_max_workers=1)
    repository = SQLiteTaskRepository(settings)
    runner = QueuedTaskRunner(repository=repository, app_settings=settings, autostart=False)
    artifacts = ArtifactStore(settings)
    application = CompareTaskApplication(repository=repository, runner=runner, artifact_store=artifacts)
    return application, repository, runner, artifacts


def _upload(filename: str, payload: bytes | None = None) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(payload if payload is not None else _pdf_bytes()))


def test_submission_creates_sqlite_record_before_file_processing_and_preserves_names(tmp_path: Path) -> None:
    application, repository, _runner, artifacts = _application(tmp_path)

    task = asyncio.run(
        application.submit_uploads(
            task_id="T1",
            original_file=_upload("原 合同.pdf"),
            compare_file=_upload("新 合同.pdf"),
            compare_options=None,
            owner=ADMIN,
        )
    )

    assert task.status == "PROCESSING"
    assert artifacts.upload_path("T1", "original", "原 合同.pdf").is_file()
    assert artifacts.upload_path("T1", "compare", "新 合同.pdf").is_file()
    assert repository.load_compare_task("T1").original_filename == "原 合同.pdf"


def test_invalid_pdf_submission_remains_traceable(tmp_path: Path) -> None:
    application, repository, _runner, _artifacts = _application(tmp_path)

    with pytest.raises(FileValidationError):
        asyncio.run(
            application.submit_uploads(
                task_id="T1",
                original_file=_upload("原合同.pdf", b"not-pdf"),
                compare_file=_upload("新合同.pdf"),
                compare_options=None,
                owner=ADMIN,
            )
        )

    failed = repository.load_compare_task("T1")
    assert failed.status == "FAILED"
    assert failed.terminal_reason == "SUBMISSION_FAILED"
    assert failed.execution_last_error


def test_unsafe_filename_is_rejected_before_any_task_or_file_is_created(tmp_path: Path) -> None:
    application, repository, _runner, _artifacts = _application(tmp_path)

    with pytest.raises(FileValidationError, match="文件名"):
        asyncio.run(
            application.submit_uploads(
                task_id="T1",
                original_file=_upload("../原合同.pdf"),
                compare_file=_upload("新合同.pdf"),
                compare_options=None,
                owner=ADMIN,
            )
        )

    with pytest.raises(FileNotFoundError):
        repository.load_compare_task("T1")


def test_retry_reuses_task_id_without_persisted_job_history(tmp_path: Path) -> None:
    application, repository, _runner, artifacts = _application(tmp_path)
    original = artifacts.upload_path("T1", "original", "a.pdf")
    compare = artifacts.upload_path("T1", "compare", "b.pdf")
    original.parent.mkdir(parents=True)
    compare.parent.mkdir(parents=True)
    original.write_bytes(_pdf_bytes())
    compare.write_bytes(_pdf_bytes())
    repository.save_compare_task(
        CompareTask(
            task_id="T1",
            status="FAILED",
            terminal_reason="EXECUTION_FAILED",
            original_filename="a.pdf",
            compare_filename="b.pdf",
        )
    )

    job = application.retry_compare("T1")

    assert job.task_id == "T1"
    assert repository.load_compare_task("T1").execution_no == 1
    assert not list(artifacts.task_root("T1").glob("jobs/*.json"))


def test_low_quality_completed_task_retains_diagnostics() -> None:
    task = CompareTask(
        task_id="T1",
        status="COMPLETED",
        ocr_quality_summary=TaskOcrQualitySummary(status="UNRELIABLE", requires_review=True),
    )

    assert requires_diagnostic_retention(task) is True
    assert requires_diagnostic_retention(CompareTask(task_id="T2", status="COMPLETED")) is False


def test_report_keeps_only_current_revision_without_manifest(tmp_path: Path) -> None:
    class Generator:
        def generate(self, _task: CompareTask, output_path: str | Path) -> Path:
            path = Path(output_path)
            path.write_bytes(b"report")
            return path

    artifacts = ArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    reports = ReportStore(artifact_store=artifacts, generator=Generator())
    old = artifacts.report_pdf_path("T1", 1)
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")

    current = reports.ensure_report(CompareTask(task_id="T1", status="COMPLETED", report_revision=2))

    assert current.name == "report-r2.pdf"
    assert not old.exists()
    assert not list(current.parent.glob("*.manifest.json"))
