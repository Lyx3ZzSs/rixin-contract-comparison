from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings
from app.infrastructure.task_runner import TaskJob, default_task_runner
from app.main import app
from app.models import AIAnalysis, BBox, CompareTask, DiffItem, EvidenceBox
from app.models_extraction import ExtractionFieldDef, ExtractionFieldValue, ExtractionTask
from app.utils.json_utils import load_extraction_task, load_task, save_extraction_task, save_task


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
    settings.highlighted_dir = settings.storage_dir / "highlighted"
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.debug_dir = settings.storage_dir / "debug"
    settings.task_jobs_dir = settings.storage_dir / "task_jobs"
    settings.task_repository_backend = "local_json"
    settings.document_extractor = "auto"
    settings.ai_llm_base_url = ""
    settings.ai_llm_api_key = ""
    settings.ai_llm_model = ""
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


def wait_for_extraction_task(client: TestClient, task_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/extract/{task_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] != "PROCESSING":
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Extraction task did not finish: {task_id}")


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
    assert task_response.json()["compare_highlight_pdf_url"] == f"/api/compare/{task_id}/highlight/compare"
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
    assert first_diff["ai_analysis"]["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
    report_response = client.get(f"/api/compare/{task_id}/report")
    assert report_response.status_code == 200
    assert not report_response.headers["content-disposition"].lower().startswith("inline")
    assert "差异分析报告.pdf" in unquote(report_response.headers["content-disposition"])
    refreshed_task = client.get(f"/api/compare/{task_id}").json()
    assert refreshed_task["report_filename"].endswith("差异分析报告.pdf")
    task = load_task(task_id)
    assert task.report_ai_analysis is None
    assert refreshed_task["report_ai_analysis"] is None
    assert "original_page_screenshots" not in refreshed_task
    assert "compare_page_screenshots" not in refreshed_task
    refreshed_diff = client.get(f"/api/compare/{task_id}/diffs").json()["diffs"][0]
    assert refreshed_diff["ai_analysis"]["raw_response"]["source"] == "rule_based"
    original_preview_response = client.get(f"/api/compare/{task_id}/original")
    compare_preview_response = client.get(f"/api/compare/{task_id}/compare")
    assert original_preview_response.status_code == 200
    assert compare_preview_response.status_code == 200
    assert "application/pdf" in original_preview_response.headers["content-type"]
    assert "application/pdf" in compare_preview_response.headers["content-type"]
    assert original_preview_response.headers["content-disposition"].lower().startswith("inline")
    assert compare_preview_response.headers["content-disposition"].lower().startswith("inline")
    assert client.get(f"/api/compare/{task_id}/highlight/original").status_code == 200
    assert client.get(f"/api/compare/{task_id}/highlight/compare").status_code == 200
    assert client.get(f"/api/compare/{task_id}/screenshot/example.png").status_code == 404
    assert client.get("/api/compare/missing-task/original").status_code == 404
    assert client.get(f"/api/compare/{task_id}/preview").status_code == 404


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


def test_extraction_execution_api_gets_and_cancels_queued_job(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    task_id = "EEXEC_CANCEL"
    save_extraction_task(ExtractionTask(task_id=task_id, filename="extract.pdf"))
    job = default_task_runner.job_repository.enqueue(
        TaskJob(job_id=f"extraction:{task_id}", task_id=task_id, task_type="extraction", payload={"task_id": task_id})
    )

    client = TestClient(app)
    execution_response = client.get(f"/api/extract/{task_id}/execution")
    assert execution_response.status_code == 200
    assert execution_response.json()["job_id"] == job.job_id
    assert execution_response.json()["status"] == "QUEUED"

    cancel_response = client.post(f"/api/extract/{task_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "CANCELLED"
    assert load_extraction_task(task_id).stage == "已取消"


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
            high_risk_count=0,
            medium_risk_count=1,
            low_risk_count=0,
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
            high_risk_count=1,
            medium_risk_count=1,
            low_risk_count=1,
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
                ai_analysis=AIAnalysis(risk_level="MEDIUM"),
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


def test_extraction_records_list_uses_extraction_tasks_only(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TCOMPARE",
            status="COMPLETED",
            original_filename="old-a.pdf",
            compare_filename="old-b.pdf",
        )
    )
    save_extraction_task(
        ExtractionTask(
            task_id="EOLDER",
            status="COMPLETED",
            created_at="2026-05-20T10:00:00+00:00",
            updated_at="2026-05-20T10:30:00+00:00",
            filename="older.pdf",
            file_path=str(settings.uploads_dir / "EOLDER" / "source_older.pdf"),
            extractor_used="ppocrv5_llm",
            fields=[
                ExtractionFieldDef(id="party-a-name", name="甲方名称"),
                ExtractionFieldDef(id="party-b-name", name="乙方名称"),
            ],
            results=[
                ExtractionFieldValue(field_id="party-a-name", field_name="甲方名称", value="日新", confidence=0.9, status="found"),
                ExtractionFieldValue(field_id="party-b-name", field_name="乙方名称", status="not_found"),
            ],
        )
    )
    save_extraction_task(
        ExtractionTask(
            task_id="ENEWER",
            status="FAILED",
            created_at="2026-05-21T09:00:00+00:00",
            updated_at="2026-05-21T09:05:00+00:00",
            filename="newer.pdf",
            extractor_used="ppocrv5_llm",
            fields=[ExtractionFieldDef(id="amount", name="合同金额")],
            results=[
                ExtractionFieldValue(field_id="amount", field_name="合同金额", status="error"),
            ],
        )
    )

    client = TestClient(app)
    response = client.get("/api/extract/records")

    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert [record["task_id"] for record in records] == ["ENEWER", "EOLDER"]
    assert records[0]["field_count"] == 1
    assert records[0]["found_count"] == 0
    assert records[0]["not_found_count"] == 0
    assert records[0]["error_count"] == 1
    assert records[1]["field_count"] == 2
    assert records[1]["found_count"] == 1
    assert records[1]["not_found_count"] == 1
    assert records[1]["error_count"] == 0
    assert records[1]["file_url"] == "/api/extract/EOLDER/file"
    assert all(record["task_id"] != "TCOMPARE" for record in records)


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


def test_api_extract_accepts_png_with_ppocrv5_llm(monkeypatch, tmp_path: Path) -> None:
    configure_storage(tmp_path)
    monkeypatch.setattr(settings, "ppocrv5_url", "https://ocr.example.test")
    monkeypatch.setattr(settings, "ai_llm_base_url", "https://llm.example.test/v1")
    monkeypatch.setattr(settings, "ai_llm_api_key", "secret")
    monkeypatch.setattr(settings, "ai_llm_model", "contract-model")
    monkeypatch.setattr(settings, "save_extraction_raw_result", False)

    class FakeResponse:
        text = ""
        status_code = 200

        def __init__(self, payload: dict):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        def post(self, url: str, headers: dict, json: dict):
            if url.endswith("/ocr"):
                assert json["fileType"] == 1
                return FakeResponse(
                    {
                        "errorCode": 0,
                        "result": {
                            "dataInfo": {"type": "image", "width": 400, "height": 300},
                            "ocrResults": [{"prunedResult": {"rec_texts": ["甲方：日新公司"], "rec_scores": [0.99]}}],
                        },
                    }
                )
            assert url.endswith("/chat/completions")
            assert "甲方：日新公司" in json["messages"][1]["content"]
            return FakeResponse({"choices": [{"message": {"content": '{"甲方名称":"日新公司"}'}}]})

    fake_client = FakeClient()
    monkeypatch.setattr("app.clients._ocr_client", fake_client)
    monkeypatch.setattr("app.clients._llm_client", fake_client)

    client = TestClient(app)
    response = client.post(
        "/api/extract",
        files={"file": ("contract.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
        data={"fields": '[{"id":"party-a-name","name":"甲方名称","type":"文本","description":"甲方名称"}]'},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    task_id = payload["task_id"]
    assert "schema_version" not in payload
    assert "revision" not in payload
    assert "file_path" not in payload
    assert "raw_result_path" not in payload
    assert "converted_file_path" not in payload

    polled = wait_for_extraction_task(client, task_id)
    assert polled["status"] == "COMPLETED"
    assert polled["extractor_used"] == "ppocrv5_llm"
    assert polled["results"][0]["value"] == "日新公司"
    assert "file_path" not in polled
    assert "raw_result_path" not in polled


def test_api_extract_rejects_unsupported_file(tmp_path: Path) -> None:
    configure_storage(tmp_path)

    client = TestClient(app)
    response = client.post(
        "/api/extract",
        files={"file": ("contract.txt", b"plain text", "text/plain")},
        data={"fields": '[{"id":"party-a-name","name":"甲方名称","type":"文本","description":"甲方名称"}]'},
    )

    assert response.status_code == 400
    assert "仅支持 PDF、Word、PNG、JPG、JPEG、BMP 文件" in response.json()["detail"]


def test_api_extract_preview_converts_word_to_pdf(monkeypatch, tmp_path: Path) -> None:
    configure_storage(tmp_path)

    def fake_convert_word_to_pdf(self, path: Path) -> Path:
        output_dir = path.parent / "converted"
        output_dir.mkdir(parents=True, exist_ok=True)
        converted = output_dir / f"{path.stem}.pdf"
        converted.write_bytes(b"%PDF-1.4\npreview")
        return converted

    monkeypatch.setattr(
        "app.services.ppocrv5_llm_extraction.ExtractionFilePreprocessor._convert_word_to_pdf",
        fake_convert_word_to_pdf,
    )

    client = TestClient(app)
    response = client.post(
        "/api/extract/preview",
        files={
            "file": (
                "contract.docx",
                b"word",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200, response.text
    assert "application/pdf" in response.headers["content-type"]
    assert response.content.startswith(b"%PDF-1.4")


def test_api_extract_preview_rejects_images(tmp_path: Path) -> None:
    configure_storage(tmp_path)

    client = TestClient(app)
    response = client.post(
        "/api/extract/preview",
        files={"file": ("contract.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
    )

    assert response.status_code == 400
    assert "仅支持 PDF 或 Word 文件预览" in response.json()["detail"]
