from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import unquote

import fitz
import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import api_schemas
from app.api_errors import http_error
from app.api_presenters import compare_task_response, task_execution_response
from app.config import settings
from app.errors import TaskStaleLeaseError, TaskTransitionConflict
from app.infrastructure.task_runner import TaskJob, default_task_runner
from app.main import app
from app.models import (
    BBox,
    CompareTask,
    DiffItem,
    EvidenceBox,
    OcrRemediationAction,
    PageOcrQualityProfile,
    TaskOcrRemediationSummary,
    TaskOcrQualitySummary,
)
from app.services.review_service import CompareQualityService
from app.utils.json_utils import load_task, save_task as persist_task, task_json_path, to_jsonable

from auth_helpers import ADMIN


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


def wait_for_compare_task(client: TestClient, task_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/compare/{task_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] != "PROCESSING":
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Compare task did not finish: {task_id}")


def test_api_compare_contracts(tmp_path: Path) -> None:
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
        'data: {"task_id": "TPROGRESS_SNAPSHOT", "stage": "已完成", "progress_percent": 100, "status": "COMPLETED"}'
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
    save_task(CompareTask(task_id=task_id, original_pdf_path="a.pdf", compare_pdf_path="b.pdf"))
    job = default_task_runner.job_repository.enqueue(
        TaskJob(job_id=f"compare:{task_id}", task_id=task_id, task_type="compare", payload={"task_id": task_id})
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


def test_compare_execution_api_retries_failed_job(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    task_id = "TEXEC_RETRY"
    save_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            stage="失败",
            progress_percent=100,
            original_pdf_path=str(tmp_path / "a.pdf"),
            compare_pdf_path=str(tmp_path / "b.pdf"),
            errors=["failed"],
        )
    )
    job = default_task_runner.job_repository.enqueue(
        TaskJob(
            job_id=f"compare:{task_id}",
            task_id=task_id,
            task_type="compare",
            payload={"task_id": task_id, "original_path": "a.pdf", "compare_path": "b.pdf"},
            attempt=1,
            max_attempts=1,
        )
    )
    default_task_runner.job_repository.mark_failed(job.job_id, worker_id="", error="failed", retry_delay_seconds=0)

    try:
        client = TestClient(app)
        retry_response = client.post(f"/api/compare/{task_id}/retry")
    finally:
        default_task_runner.autostart = original_autostart

    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "QUEUED"
    assert retry_response.json()["attempt"] == 0
    retried_task = load_task(task_id)
    assert retried_task.status == "PROCESSING"
    assert retried_task.stage == "排队中"
    assert retried_task.errors == []


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

    payload = TestClient(app).get("/api/compare/TSTATE_FIELDS").json()

    assert payload["terminal_reason"] == "EXECUTION_FAILED"
    assert payload["revision"] >= 1
    assert payload["report_revision"] == 3


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
    assert quality_payload["evidence_quality_counts"]["LOW"] == 1
    assert quality_payload["low_confidence_diffs"][0]["diff_id"] == "D001"
    assert quality_payload["low_similarity_diffs"][0]["diff_id"] == "D001"


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
