from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings
from app.main import app
from app.models import CompareTask
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
    settings.highlighted_dir = settings.storage_dir / "highlighted"
    settings.screenshots_dir = settings.storage_dir / "screenshots"
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.document_extractor = "auto"
    settings.ai_llm_base_url = ""
    settings.ai_llm_api_key = ""
    settings.ai_llm_model = ""
    settings.ensure_storage()


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
    assert payload["diff_count"] >= 1
    assert payload["extractor_used"] == "pymupdf"
    assert "preview_url" not in payload
    assert payload["original_pdf_url"] == f"/api/compare/{task_id}/original"
    assert payload["compare_pdf_url"] == f"/api/compare/{task_id}/compare"
    assert payload["report_url"] == f"/api/compare/{task_id}/report"
    assert payload["report_filename"].endswith("差异分析报告.pdf")
    assert payload["original_highlight_pdf_url"] == f"/api/compare/{task_id}/highlight/original"

    task_response = client.get(f"/api/compare/{task_id}")
    assert task_response.status_code == 200
    assert "preview_url" not in task_response.json()
    assert task_response.json()["extractor_used"] == "pymupdf"
    assert task_response.json()["original_pdf_url"] == f"/api/compare/{task_id}/original"
    assert task_response.json()["compare_pdf_url"] == f"/api/compare/{task_id}/compare"
    assert task_response.json()["report_url"] == f"/api/compare/{task_id}/report"
    assert task_response.json()["compare_highlight_pdf_url"] == f"/api/compare/{task_id}/highlight/compare"

    diffs_response = client.get(f"/api/compare/{task_id}/diffs")
    assert diffs_response.status_code == 200
    first_diff = diffs_response.json()["diffs"][0]
    assert "original_screenshot_url" in first_diff
    assert "original_evidence" in first_diff
    assert "compare_evidence" in first_diff
    assert first_diff["original_evidence"][0]["method"] == "char_exact"
    assert first_diff["compare_evidence"][0]["method"] == "char_exact"
    assert first_diff["ai_analysis"] is None
    report_response = client.get(f"/api/compare/{task_id}/report")
    assert report_response.status_code == 200
    assert not report_response.headers["content-disposition"].lower().startswith("inline")
    assert "差异分析报告.pdf" in unquote(report_response.headers["content-disposition"])
    refreshed_task = client.get(f"/api/compare/{task_id}").json()
    assert refreshed_task["report_filename"].endswith("差异分析报告.pdf")
    task = load_task(task_id)
    assert task.report_ai_analysis is None
    assert refreshed_task["report_ai_analysis"] is None
    assert refreshed_task["original_page_screenshots"]
    assert refreshed_task["compare_page_screenshots"]
    refreshed_diff = client.get(f"/api/compare/{task_id}/diffs").json()["diffs"][0]
    assert refreshed_diff["ai_analysis"] is None
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
    assert client.get("/api/compare/missing-task/original").status_code == 404
    assert client.get(f"/api/compare/{task_id}/preview").status_code == 404


def test_root_is_not_a_backend_page() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 404


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
        def __init__(self, timeout: int):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

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

    monkeypatch.setattr("app.services.extractors.ppocrv5.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.httpx.Client", FakeClient)

    client = TestClient(app)
    response = client.post(
        "/api/extract",
        files={"file": ("contract.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
        data={"fields": '[{"id":"party-a-name","name":"甲方名称","type":"文本","description":"甲方名称"}]'},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "COMPLETED"
    assert payload["extractor_used"] == "ppocrv5_llm"
    assert payload["results"][0]["value"] == "日新公司"


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
