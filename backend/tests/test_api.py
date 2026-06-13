from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote

import fitz
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings
from app.infrastructure.task_runner import TaskJob, default_task_runner
from app.main import app
from app.models import BBox, CompareTask, DiffItem, EvidenceBox
from app.models_extraction import ExtractionTask
from app.utils.json_utils import load_task, save_extraction_task, save_task


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
    make_pdf(original, ["1. Payment", "Buyer shall pay within 30 days."])
    make_pdf(compare, ["1. Payment", "Buyer shall pay within 45 days."])

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
    assert "revision" not in payload
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


def test_api_compare_ignores_legacy_exclusion_options(tmp_path: Path) -> None:
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
    assert task.compare_options.ignore_headers_footers is False
    assert task.compare_options.ignore_stamps is False
    job = default_task_runner.latest_job(task_id, task_type="compare")
    assert "compare_options" not in job.payload


def test_compare_progress_stream_sends_current_snapshot(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(CompareTask(
        task_id="TPROGRESS_SNAPSHOT",
        status="COMPLETED",
        stage="已完成",
        progress_percent=100,
    ))

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
    save_extraction_task(ExtractionTask(task_id="TEXT001", filename="extract.pdf"))

    client = TestClient(app)
    response = client.get("/api/compare/records")

    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert [record["task_id"] for record in records] == ["TNEWER", "TOLDER"]
    assert records[0]["report_url"] == ""
    assert records[1]["report_url"] == "/api/compare/TOLDER/report"
    assert all(record["task_id"] != "TEXT001" for record in records)


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
    assert payload["diff"]["reviewed_by"] == "legal"
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











