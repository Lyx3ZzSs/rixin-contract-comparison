from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import settings
from app.models import Document
from app.models_extraction import ExtractionFieldDef, ExtractionFieldValue, ExtractionTask
from app.services.extractors import DocumentExtractionError
from app.services.extractors.factory import build_document_extractor
from app.utils.json_utils import save_extraction_task

MAX_TEXT_CHARS = 30000


class ExtractionService:
    def extract(
        self,
        file_path: str,
        field_defs: list[ExtractionFieldDef],
        task_id: str,
        filename: str = "",
    ) -> ExtractionTask:
        task = ExtractionTask(
            task_id=task_id,
            filename=filename,
            file_path=file_path,
            fields=field_defs,
        )
        save_extraction_task(task)

        try:
            extractor = build_document_extractor()
            result = extractor.extract(file_path, task_id=task_id)
            task.extractor_used = result.extractor_used

            full_text = self._assemble_full_text(result.document)
            results = self._extract_fields_via_llm(full_text, field_defs)
            task.results = results
            task.status = "COMPLETED"
        except Exception as exc:
            task.status = "FAILED"
            task.errors.append(str(exc))
        finally:
            from datetime import UTC, datetime

            task.updated_at = datetime.now(UTC).isoformat()
            save_extraction_task(task)

        return task

    def _assemble_full_text(self, document: Document) -> str:
        parts: list[str] = []
        for page in document.pages:
            for block in page.blocks:
                text = block.text.strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)

    def _extract_fields_via_llm(
        self, full_text: str, field_defs: list[ExtractionFieldDef]
    ) -> list[ExtractionFieldValue]:
        if not self._is_llm_configured():
            return [
                ExtractionFieldValue(
                    field_id=f.id,
                    field_name=f.name,
                    status="error",
                    value="",
                )
                for f in field_defs
            ]

        messages = self._build_extraction_prompt(full_text, field_defs)
        url = self._chat_completions_url(settings.ai_llm_base_url)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.ai_llm_api_key:
            headers["Authorization"] = f"Bearer {settings.ai_llm_api_key}"

        timeout = getattr(settings, "ai_extraction_timeout_seconds", 120)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json={"model": settings.ai_llm_model, "messages": messages})
            response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]
        return self._parse_extraction_response(content, field_defs)

    def _is_llm_configured(self) -> bool:
        return bool(settings.ai_llm_base_url and settings.ai_llm_model and settings.ai_llm_api_key)

    def _chat_completions_url(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _build_extraction_prompt(
        self, full_text: str, field_defs: list[ExtractionFieldDef]
    ) -> list[dict[str, str]]:
        truncated = len(full_text) > MAX_TEXT_CHARS
        text = full_text[:MAX_TEXT_CHARS]
        if truncated:
            text += "\n\n[注意：文档内容已截断，仅包含前部分内容]"

        fields_json = json.dumps(
            [{"id": f.id, "name": f.name, "description": f.description} for f in field_defs],
            ensure_ascii=False,
        )

        system_prompt = (
            "你是一名专业的合同数据提取专家。你的任务是从合同文本中准确提取指定的字段信息。\n"
            "规则：\n"
            "1. 严格按照给定的字段定义提取数据\n"
            "2. 如果某个字段在文本中找不到，value 设为空字符串，confidence 设为 0\n"
            "3. confidence 表示提取结果的可靠程度：1.0 为完全确定，0.5 为部分推测，0 为未找到\n"
            "4. source_snippet 应包含原文中支持该提取结果的关键片段\n"
            "5. 只返回 JSON，不要返回其他内容，不要使用 Markdown 代码块"
        )

        user_prompt = (
            "请从以下合同文本中提取指定字段的值。\n\n"
            f"需要提取的字段：\n{fields_json}\n\n"
            f"合同全文：\n---\n{text}\n---\n\n"
            '请以如下 JSON 格式返回提取结果（不要包含 ```json 标记）：\n'
            '{"fields": [{"field_id": "字段ID", "value": "提取到的值", '
            '"confidence": 0.95, "source_snippet": "原文片段"}]}'
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _parse_extraction_response(
        self, content: str, field_defs: list[ExtractionFieldDef]
    ) -> list[ExtractionFieldValue]:
        payload = self._extract_json(content)
        fields_data = payload.get("fields", [])

        field_map = {f.id: f for f in field_defs}
        results: list[ExtractionFieldValue] = []

        for item in fields_data:
            fid = item.get("field_id", "")
            field_def = field_map.get(fid)
            if not field_def:
                continue
            results.append(
                ExtractionFieldValue(
                    field_id=fid,
                    field_name=field_def.name,
                    value=str(item.get("value", "")),
                    confidence=float(item.get("confidence", 0.0)),
                    source_snippet=str(item.get("source_snippet", "")),
                    status="found" if item.get("value") else "not_found",
                )
            )

        found_ids = {r.field_id for r in results}
        for f in field_defs:
            if f.id not in found_ids:
                results.append(
                    ExtractionFieldValue(
                        field_id=f.id,
                        field_name=f.name,
                        status="not_found",
                    )
                )

        return results

    def _extract_json(self, content: str) -> dict[str, Any]:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                raise ValueError("LLM 返回内容不是有效 JSON。") from None
            return json.loads(match.group(0))
