from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.models_extraction import ExtractionFieldDef
from app.services.ppocrv5_llm_extraction import (
    ExtractionFilePreprocessor,
    PPOCRV5LLMExtractionClient,
    PPOCRV5LLMExtractionError,
)


def configure_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ppocrv5_url", "https://ocr.example.test")
    monkeypatch.setattr(settings, "ppocrv5_access_token", "ocr-secret")
    monkeypatch.setattr(settings, "ai_llm_base_url", "https://llm.example.test/v1")
    monkeypatch.setattr(settings, "ai_llm_api_key", "llm-secret")
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


def ocr_payload(*texts: str, data_type: str = "document") -> dict:
    data_info = {"type": data_type, "pages": [{"width": 595, "height": 842}]}
    if data_type == "image":
        data_info = {"type": "image", "width": 400, "height": 300}
    return {
        "errorCode": 0,
        "result": {
            "dataInfo": data_info,
            "ocrResults": [
                {
                    "prunedResult": {
                        "rec_texts": list(texts),
                        "rec_scores": [0.99 for _ in texts],
                        "rec_polys": [[[0, 0], [100, 0], [100, 20], [0, 20]] for _ in texts],
                    }
                }
            ],
        },
    }


def llm_payload(content: object) -> dict:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {"choices": [{"message": {"content": text}}]}


def test_ppocrv5_llm_extracts_fields_from_pdf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_extraction(monkeypatch)
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    calls: list[tuple[str, dict, dict]] = []

    class FakeClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict):
            calls.append((url, headers, json))
            if url.endswith("/ocr"):
                return FakeResponse(ocr_payload("甲方：日新公司"))
            assert url.endswith("/chat/completions")
            assert "甲方：日新公司" in json["messages"][1]["content"]
            return FakeResponse(
                llm_payload(
                    {
                        "party-a-name": {
                            "value": "日新公司",
                            "confidence": 0.92,
                            "evidence": "甲方：日新公司",
                        }
                    }
                )
            )

    monkeypatch.setattr("app.services.extractors.ppocrv5.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.httpx.Client", FakeClient)

    result = PPOCRV5LLMExtractionClient().extract_fields(
        source,
        [ExtractionFieldDef(id="party-a-name", name="甲方名称", description="甲方名称")],
        task_id="TEXTRACT",
    )

    assert [url.rsplit("/", 1)[-1] for url, _, _ in calls] == ["ocr", "completions"]
    assert calls[0][1]["Authorization"] == "Bearer ocr-secret"
    assert calls[0][2]["fileType"] == 0
    assert calls[1][1]["Authorization"] == "Bearer llm-secret"
    assert calls[1][2]["model"] == "contract-model"
    assert result.results[0].field_id == "party-a-name"
    assert result.results[0].value == "日新公司"
    assert result.results[0].confidence == 0.92
    assert result.results[0].source_snippet == "甲方：日新公司"
    assert result.results[0].status == "found"


def test_ppocrv5_llm_uses_image_file_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_extraction(monkeypatch)
    source = tmp_path / "contract.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    ocr_bodies: list[dict] = []

    class FakeClient:
        def __init__(self, timeout: int):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict):
            if url.endswith("/ocr"):
                ocr_bodies.append(json)
                return FakeResponse(ocr_payload("甲方：日新公司", data_type="image"))
            return FakeResponse(llm_payload({"甲方名称": "日新公司"}))

    monkeypatch.setattr("app.services.extractors.ppocrv5.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.httpx.Client", FakeClient)

    result = PPOCRV5LLMExtractionClient().extract_fields(
        source,
        [ExtractionFieldDef(id="party-a-name", name="甲方名称")],
    )

    assert ocr_bodies[0]["fileType"] == 1
    assert result.results[0].value == "日新公司"


def test_ppocrv5_llm_parses_markdown_json_and_list_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_extraction(monkeypatch)
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\n")

    class FakeClient:
        def __init__(self, timeout: int):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict):
            if url.endswith("/ocr"):
                return FakeResponse(ocr_payload("乙方：南瑞继保电气有限公司"))
            return FakeResponse(
                llm_payload(
                    '```json\n{"fields":[{"field_id":"party-b-name","value":"南瑞继保电气有限公司","confidence":0.9,"evidence":"乙方：南瑞继保电气有限公司"}]}\n```'
                )
            )

    monkeypatch.setattr("app.services.extractors.ppocrv5.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.httpx.Client", FakeClient)

    result = PPOCRV5LLMExtractionClient().extract_fields(
        source,
        [ExtractionFieldDef(id="party-b-name", name="乙方名称")],
    )

    assert result.results[0].value == "南瑞继保电气有限公司"
    assert result.results[0].status == "found"


def test_ppocrv5_llm_marks_missing_fields_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_extraction(monkeypatch)
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\n")

    class FakeClient:
        def __init__(self, timeout: int):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict):
            if url.endswith("/ocr"):
                return FakeResponse(ocr_payload("合同正文"))
            return FakeResponse(llm_payload({"甲方名称": {"value": "未知", "confidence": 0, "evidence": ""}}))

    monkeypatch.setattr("app.services.extractors.ppocrv5.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.httpx.Client", FakeClient)

    result = PPOCRV5LLMExtractionClient().extract_fields(
        source,
        [ExtractionFieldDef(id="party-a-name", name="甲方名称")],
    )

    assert result.results[0].status == "not_found"
    assert result.results[0].value == ""
    assert result.results[0].confidence == 0.0


def test_ppocrv5_llm_requires_llm_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setattr(settings, "ai_llm_base_url", "")
    monkeypatch.setattr(settings, "ai_llm_api_key", "")
    monkeypatch.setattr(settings, "ai_llm_model", "")

    with pytest.raises(PPOCRV5LLMExtractionError, match="未配置 AI_LLM_BASE_URL"):
        PPOCRV5LLMExtractionClient().extract_fields(source, [ExtractionFieldDef(id="party-a-name", name="甲方名称")])


def test_ppocrv5_llm_rejects_empty_ocr_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_extraction(monkeypatch)
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\n")

    class FakeClient:
        def __init__(self, timeout: int):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict):
            return FakeResponse(ocr_payload(""))

    monkeypatch.setattr("app.services.extractors.ppocrv5.httpx.Client", FakeClient)

    with pytest.raises(PPOCRV5LLMExtractionError, match="未返回可用于字段提取的文本"):
        PPOCRV5LLMExtractionClient().extract_fields(source, [ExtractionFieldDef(id="party-a-name", name="甲方名称")])


def test_ppocrv5_llm_rejects_non_json_llm_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_extraction(monkeypatch)
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\n")

    class FakeClient:
        def __init__(self, timeout: int):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict):
            if url.endswith("/ocr"):
                return FakeResponse(ocr_payload("甲方：日新公司"))
            return FakeResponse(llm_payload("无法处理"))

    monkeypatch.setattr("app.services.extractors.ppocrv5.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.httpx.Client", FakeClient)

    with pytest.raises(PPOCRV5LLMExtractionError, match="LLM 返回内容不是 JSON"):
        PPOCRV5LLMExtractionClient().extract_fields(source, [ExtractionFieldDef(id="party-a-name", name="甲方名称")])


def test_word_preprocessor_converts_with_libreoffice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "contract.docx"
    source.write_bytes(b"word")

    def fake_run(command, check, capture_output, text, timeout):
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / "contract.pdf").write_bytes(b"%PDF-1.7\n")

        class Completed:
            returncode = 0
            stderr = ""
            stdout = ""

        return Completed()

    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.shutil.which", lambda name: "/usr/bin/libreoffice")
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.subprocess.run", fake_run)

    prepared = ExtractionFilePreprocessor().prepare(source)

    assert prepared.file_type == 0
    assert prepared.path.name == "contract.pdf"
    assert prepared.converted_file_path.endswith("contract.pdf")


def test_word_preprocessor_requires_libreoffice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "contract.docx"
    source.write_bytes(b"word")
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.shutil.which", lambda name: None)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.sys.platform", "linux")
    monkeypatch.setattr(settings, "libreoffice_path", "")

    with pytest.raises(PPOCRV5LLMExtractionError, match="LIBREOFFICE_PATH"):
        ExtractionFilePreprocessor().prepare(source)


def test_find_libreoffice_detects_macos_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.shutil.which", lambda name: None)
    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.sys.platform", "darwin")
    monkeypatch.setattr(settings, "libreoffice_path", "")

    macos_standard_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    original_is_file = Path.is_file

    def fake_is_file(self):
        if str(self) == macos_standard_path:
            return True
        return original_is_file(self)

    monkeypatch.setattr("app.services.ppocrv5_llm_extraction.Path.is_file", fake_is_file)

    preprocessor = ExtractionFilePreprocessor()
    assert preprocessor._find_libreoffice_executable() == macos_standard_path


def test_find_libreoffice_uses_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "custom_soffice"
    fake_bin.write_bytes(b"binary")
    monkeypatch.setattr(settings, "libreoffice_path", str(fake_bin))

    preprocessor = ExtractionFilePreprocessor()
    assert preprocessor._find_libreoffice_executable() == str(fake_bin)
