from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.models_extraction import ExtractionFieldDef, ExtractionFieldValue
from app.services.extractors.base import DocumentExtractionError
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.utils.file_utils import EXTRACTION_IMAGE_EXTENSIONS


class PPOCRV5LLMExtractionError(ValueError):
    pass


@dataclass
class PreparedExtractionFile:
    path: Path
    file_type: int
    converted_file_path: str = ""


@dataclass
class PPOCRV5LLMExtractionResult:
    results: list[ExtractionFieldValue]
    raw_result_path: str = ""
    converted_file_path: str = ""


class ExtractionFilePreprocessor:
    def prepare(self, source_path: str | Path) -> PreparedExtractionFile:
        path = Path(source_path)
        extension = path.suffix.lower()
        if extension == ".pdf":
            return PreparedExtractionFile(path=path, file_type=0)
        if extension in EXTRACTION_IMAGE_EXTENSIONS:
            return PreparedExtractionFile(path=path, file_type=1)
        if extension in {".doc", ".docx"}:
            converted = self._convert_word_to_pdf(path)
            return PreparedExtractionFile(path=converted, file_type=0, converted_file_path=str(converted))
        raise PPOCRV5LLMExtractionError("仅支持 PDF、Word、PNG、JPG、JPEG、BMP 文件。")

    def _find_libreoffice_executable(self) -> str | None:
        if settings.libreoffice_path and Path(settings.libreoffice_path).is_file():
            return settings.libreoffice_path
        executable = shutil.which("libreoffice") or shutil.which("soffice")
        if executable:
            return executable
        if sys.platform == "darwin":
            macos_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
            if Path(macos_path).is_file():
                return macos_path
        return None

    def _convert_word_to_pdf(self, path: Path) -> Path:
        output_dir = path.parent / "converted"
        output_dir.mkdir(parents=True, exist_ok=True)
        executable = self._find_libreoffice_executable()
        if not executable:
            raise PPOCRV5LLMExtractionError(
                "未安装 LibreOffice，无法解析 Word 文件。"
                "请安装 LibreOffice 或设置 LIBREOFFICE_PATH 环境变量。"
            )

        command = [
            executable,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=settings.ppocrv5_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise PPOCRV5LLMExtractionError("Word 转 PDF 超时。") from exc
        except OSError as exc:
            raise PPOCRV5LLMExtractionError(f"Word 转 PDF 失败: {exc}") from exc

        converted = output_dir / f"{path.stem}.pdf"
        if completed.returncode != 0 or not converted.exists():
            detail = (completed.stderr or completed.stdout or "LibreOffice 未生成 PDF。").strip()
            raise PPOCRV5LLMExtractionError(f"Word 转 PDF 失败: {detail[:500]}")
        return converted


class PPOCRV5LLMExtractionClient:
    name = "ppocrv5_llm"

    def __init__(
        self,
        preprocessor: ExtractionFilePreprocessor | None = None,
        ocr_extractor: PPOCRV5Extractor | None = None,
    ) -> None:
        self.preprocessor = preprocessor or ExtractionFilePreprocessor()
        self.ocr_extractor = ocr_extractor or PPOCRV5Extractor()

    def extract_fields(
        self,
        file_path: str | Path,
        field_defs: list[ExtractionFieldDef],
        task_id: str | None = None,
    ) -> PPOCRV5LLMExtractionResult:
        if not field_defs:
            return PPOCRV5LLMExtractionResult(results=[])
        if not self._is_llm_configured():
            raise PPOCRV5LLMExtractionError("未配置 AI_LLM_BASE_URL、AI_LLM_API_KEY 或 AI_LLM_MODEL，无法执行合同字段提取。")

        prepared = self.preprocessor.prepare(file_path)
        try:
            ocr_payload = self.ocr_extractor.predict(prepared.path, file_type=prepared.file_type)
        except DocumentExtractionError as exc:
            raise PPOCRV5LLMExtractionError(str(exc)) from exc

        ocr_text = self._ocr_text(ocr_payload)
        if not ocr_text.strip():
            raise PPOCRV5LLMExtractionError("PP-OCRv5 未返回可用于字段提取的文本。")

        llm_request = self._llm_request_payload(ocr_text, field_defs)
        llm_payload = self._post_llm_json(llm_request)
        content = self._llm_content(llm_payload)
        parsed = self._parse_json_string(content)
        if parsed is None:
            raise PPOCRV5LLMExtractionError("LLM 返回内容不是 JSON。")

        results = self._parse_field_results(parsed, field_defs)
        raw_result_path = self._save_raw_result(
            {
                "prepared_file": str(prepared.path),
                "converted_file_path": prepared.converted_file_path,
                "ocr": ocr_payload,
                "ocr_text": ocr_text,
                "llm_request": self._request_log(llm_request),
                "llm_response": llm_payload,
                "parsed_result": parsed,
            },
            task_id,
            Path(file_path),
        )
        return PPOCRV5LLMExtractionResult(
            results=results,
            raw_result_path=raw_result_path,
            converted_file_path=prepared.converted_file_path,
        )

    def _is_llm_configured(self) -> bool:
        return bool(settings.ai_llm_base_url and settings.ai_llm_api_key and settings.ai_llm_model)

    def _ocr_text(self, payload: Any) -> str:
        pages = payload if isinstance(payload, list) else [payload]
        page_texts: list[str] = []
        for page in pages:
            texts = self._texts_from_ocr_page(page)
            if texts:
                page_texts.append("\n".join(texts))
        return "\n\n".join(page_texts)

    def _texts_from_ocr_page(self, value: Any) -> list[str]:
        value = self._unwrap_ocr_value(value)
        if isinstance(value, dict):
            direct = self._string_list_from_keys(value, "rec_texts", "recTexts", "texts")
            if direct:
                return direct
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                return [text.strip()]
            nested: list[str] = []
            for key in ("ocrResults", "ocr_results", "pages", "results", "items", "lines"):
                child = value.get(key)
                if isinstance(child, list):
                    for item in child:
                        nested.extend(self._texts_from_ocr_page(item))
            return nested
        if isinstance(value, list):
            texts: list[str] = []
            for item in value:
                texts.extend(self._texts_from_ocr_page(item))
            return texts
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _unwrap_ocr_value(self, value: Any) -> Any:
        while isinstance(value, dict):
            if "prunedResult" in value or "pruned_result" in value:
                value = value.get("prunedResult") or value.get("pruned_result")
                continue
            result = value.get("result")
            if isinstance(result, dict):
                value = result
                continue
            break
        return value

    def _string_list_from_keys(self, payload: dict[str, Any], *keys: str) -> list[str]:
        for key in keys:
            value = payload.get(key)
            if hasattr(value, "tolist"):
                value = value.tolist()
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
        return []

    def _llm_request_payload(self, ocr_text: str, field_defs: list[ExtractionFieldDef]) -> dict[str, Any]:
        field_payload = [
            {
                "id": field.id,
                "name": field.name,
                "type": field.type,
                "description": field.description,
                "semantic_extraction": field.semantic_extraction,
            }
            for field in field_defs
        ]
        instruction_parts = [
            settings.extraction_task_description,
            settings.extraction_rules_str,
            settings.extraction_output_format,
        ]
        if settings.extraction_few_shot_demo:
            instruction_parts.append(f"参考示例：{settings.extraction_few_shot_demo}")
        user_payload = {
            "fields": field_payload,
            "ocr_text": ocr_text,
        }
        return {
            "model": settings.ai_llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是专业的合同字段抽取助手。只基于用户提供的 OCR 文本抽取字段，"
                        "必须输出严格 JSON，不要输出 Markdown。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "\n\n".join(part for part in instruction_parts if part)
                        + "\n\n请按字段 id 或字段 name 返回 JSON。输入 JSON："
                        + json.dumps(user_payload, ensure_ascii=False)
                    ),
                },
            ],
            "temperature": 0.0,
        }

    def _post_llm_json(self, body: dict[str, Any]) -> dict[str, Any]:
        url = self._chat_completions_url(settings.ai_llm_base_url)
        headers = {"Content-Type": "application/json"}
        if settings.ai_llm_api_key:
            headers["Authorization"] = f"Bearer {settings.ai_llm_api_key}"
        try:
            with httpx.Client(timeout=settings.ai_extraction_timeout_seconds) as client:
                response = client.post(url, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise PPOCRV5LLMExtractionError(f"LLM 字段提取请求失败 ({url}, HTTP {status_code}): {detail}") from exc
        except httpx.ConnectError as exc:
            raise PPOCRV5LLMExtractionError(f"无法连接 LLM 字段提取服务 ({url}): {exc}") from exc
        except httpx.TimeoutException as exc:
            raise PPOCRV5LLMExtractionError(f"LLM 字段提取请求超时 ({url}): {exc}") from exc
        except ValueError as exc:
            raise PPOCRV5LLMExtractionError(f"LLM 字段提取返回内容不是 JSON: {exc}") from exc
        return payload

    def _chat_completions_url(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _llm_content(self, payload: dict[str, Any]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PPOCRV5LLMExtractionError(f"LLM 返回缺少 choices[0].message.content: {payload}") from exc
        return str(content)

    def _parse_json_string(self, value: str) -> Any:
        text = value.strip()
        if not text:
            return None
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
            if not match:
                return None
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None

    def _parse_field_results(self, payload: Any, field_defs: list[ExtractionFieldDef]) -> list[ExtractionFieldValue]:
        field_payload = self._field_payload(payload)
        results: list[ExtractionFieldValue] = []
        for field in field_defs:
            value_payload = self._lookup_field_value(field_payload, field)
            value, confidence, source_snippet = self._normalize_value_payload(value_payload)
            status = "found" if value and not self._looks_not_found(value) else "not_found"
            results.append(
                ExtractionFieldValue(
                    field_id=field.id,
                    field_name=field.name,
                    value="" if status == "not_found" else value,
                    confidence=self._clamp_confidence(confidence) if status == "found" else 0.0,
                    source_snippet=source_snippet,
                    status=status,
                )
            )
        return results

    def _field_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            for key in ("fields", "results", "answers", "data"):
                value = payload.get(key)
                if isinstance(value, (dict, list)):
                    return value
        return payload

    def _lookup_field_value(self, payload: Any, field: ExtractionFieldDef) -> Any:
        if isinstance(payload, dict):
            for key in (field.id, field.name, field.description):
                if key and key in payload:
                    return payload[key]
            normalized_keys = {self._normalize_key(key): value for key, value in payload.items()}
            for key in (field.id, field.name, field.description):
                normalized = self._normalize_key(key)
                if normalized and normalized in normalized_keys:
                    return normalized_keys[normalized]
        if isinstance(payload, list):
            targets = {
                normalized
                for normalized in (
                    self._normalize_key(field.id),
                    self._normalize_key(field.name),
                    self._normalize_key(field.description),
                )
                if normalized
            }
            for item in payload:
                if not isinstance(item, dict):
                    continue
                item_key = item.get("field_id") or item.get("fieldId") or item.get("id") or item.get("key") or item.get("name") or item.get("field_name")
                if self._normalize_key(item_key) in targets:
                    return item
        return None

    def _normalize_value_payload(self, value_payload: Any) -> tuple[str, float, str]:
        if value_payload is None:
            return "", 0.0, ""
        if isinstance(value_payload, dict):
            value = self._first_present(value_payload, "value", "answer", "result", "content", "text")
            confidence = self._optional_float(
                self._first_present(value_payload, "confidence", "score", "probability")
            )
            source_snippet = self._first_present(
                value_payload,
                "source_snippet",
                "sourceSnippet",
                "source",
                "evidence",
                "reasoning_content",
            )
            return self._stringify_value(value), confidence if confidence is not None else 1.0, self._stringify_value(source_snippet)
        return self._stringify_value(value_payload), 1.0, ""

    def _first_present(self, payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        return ""

    def _stringify_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        return json.dumps(value, ensure_ascii=False)

    def _optional_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    def _clamp_confidence(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))

    def _looks_not_found(self, value: str) -> bool:
        compact = re.sub(r"\s+", "", value or "").lower()
        return compact in {"", "无", "未知", "未找到", "不存在", "未提及", "none", "null", "n/a", "na"}

    def _normalize_key(self, value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    def _request_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        logged = dict(payload)
        messages = []
        for message in payload.get("messages", []):
            if not isinstance(message, dict):
                continue
            content = str(message.get("content", ""))
            messages.append({**message, "content": content[:4000]})
        logged["messages"] = messages
        return logged

    def _save_raw_result(self, payload: Any, task_id: str | None, source_path: Path) -> str:
        if not task_id or not settings.save_extraction_raw_result:
            return ""
        directory = settings.ocr_dir / task_id
        directory.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", source_path.stem)[:80] or "document"
        path = directory / f"{stem}_ppocrv5_llm_extraction_raw.json"
        path.write_text(json.dumps(self._jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _jsonable(self, value: Any) -> Any:
        if hasattr(value, "tolist"):
            return value.tolist()
        if isinstance(value, dict):
            return {str(key): self._jsonable(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._jsonable(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
