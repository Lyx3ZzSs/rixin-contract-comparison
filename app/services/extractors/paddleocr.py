from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import fitz
import httpx

from app.config import settings
from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult


class PaddleOCRExtractor:
    name = "paddleocr"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        path = Path(path)
        if not path.exists():
            raise DocumentExtractionError(f"文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise DocumentExtractionError("仅支持 PDF 文件。")
        if not settings.paddleocr_access_token:
            raise DocumentExtractionError("未配置 PADDLEOCR_ACCESS_TOKEN，无法调用 PaddleOCR。")

        payload = self._request_ocr(path)
        raw_path = self._save_raw_result(payload, task_id, path) if settings.save_ocr_raw_result and task_id else ""
        document = self.payload_to_document(payload, path)
        return ExtractionResult(document=document, extractor_used=self.name, raw_result_path=raw_path)

    def _request_ocr(self, path: Path) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {settings.paddleocr_access_token}"}
        with httpx.Client(timeout=settings.paddleocr_timeout_seconds) as client:
            with path.open("rb") as file_obj:
                response = client.post(
                    settings.paddleocr_job_url,
                    headers=headers,
                    data={"model": settings.paddleocr_model},
                    files={"file": (path.name, file_obj, "application/pdf")},
                )
            response.raise_for_status()
            job_payload = response.json()
            job_id = self._job_id(job_payload)
            result_payload = self._wait_for_result(client, headers, job_id)
            json_url = self._json_result_url(result_payload)
            if not json_url:
                return result_payload
            result_response = client.get(json_url)
            result_response.raise_for_status()
            downloaded = self._parse_result_text(result_response.text)
            return {"job": job_payload, "result": result_payload, "downloaded": downloaded}

    def _wait_for_result(self, client: httpx.Client, headers: dict[str, str], job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + settings.paddleocr_max_wait_seconds
        job_url = f"{settings.paddleocr_job_url.rstrip('/')}/{job_id}"
        last_payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = client.get(job_url, headers=headers)
            response.raise_for_status()
            last_payload = response.json()
            data = last_payload.get("data") if isinstance(last_payload.get("data"), dict) else {}
            status = str(
                self._first_value(last_payload, "state", "status", "jobStatus")
                or self._first_value(data, "state", "status", "jobStatus")
                or ""
            ).lower()
            if status in {"done", "success", "succeeded", "completed", "finished"}:
                return last_payload
            if status in {"failed", "fail", "error", "cancelled", "canceled"}:
                raise DocumentExtractionError(f"PaddleOCR 任务失败: {last_payload}")
            time.sleep(settings.paddleocr_poll_interval_seconds)
        raise DocumentExtractionError(f"PaddleOCR 任务超时: {last_payload or job_id}")

    def payload_to_document(self, payload: dict[str, Any], source_path: str | Path) -> Document:
        source_path = Path(source_path)
        page_sizes = self._pdf_page_sizes(source_path)
        page_results = self._page_results(payload)
        page_count = max(len(page_sizes), len(page_results), 1)
        pages: list[Page] = []

        for page_index in range(1, page_count + 1):
            width, height = page_sizes[page_index - 1] if page_index - 1 < len(page_sizes) else (595.0, 842.0)
            blocks = self._blocks_from_page(page_index, width, height, page_results[page_index - 1] if page_index - 1 < len(page_results) else {})
            pages.append(Page(page_no=page_index, width=width, height=height, blocks=blocks))

        if not any(block.text.strip() for page in pages for block in page.blocks):
            raise DocumentExtractionError("PaddleOCR 未返回可用于对比的文本。")

        return Document(filename=source_path.name, path=str(source_path), page_count=page_count, pages=pages)

    def _blocks_from_page(self, page_no: int, width: float, height: float, page_payload: Any) -> list[TextBlock]:
        pruned = page_payload.get("prunedResult") or page_payload.get("pruned_result") or page_payload if isinstance(page_payload, dict) else {}
        texts = self._list_value(pruned, "rec_texts", "recTexts", "texts", "text")
        scores = self._list_value(pruned, "rec_scores", "recScores", "scores", "confidence")
        boxes = self._list_value(pruned, "dt_polys", "dtPolys", "polys", "boxes", "rec_boxes", "recBoxes")
        if not texts and isinstance(pruned.get("ocrResults") if isinstance(pruned, dict) else None, list):
            return self._blocks_from_page(page_no, width, height, {"prunedResult": pruned["ocrResults"]})
        if not texts and isinstance(page_payload, list):
            return self._blocks_from_result_items(page_no, width, height, page_payload)

        blocks: list[TextBlock] = []
        for index, text_value in enumerate(texts, start=1):
            text = str(text_value).strip()
            if not text:
                continue
            box_value = boxes[index - 1] if index - 1 < len(boxes) else None
            bbox = self._bbox_from_any(box_value, width, height)
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}_paddle_b{index}",
                    page_no=page_no,
                    text=text,
                    bbox=bbox,
                    block_type="ocr_line",
                    confidence=self._optional_float(scores[index - 1] if index - 1 < len(scores) else None),
                )
            )
        return sorted(blocks, key=lambda block: (block.bbox.y0, block.bbox.x0))

    def _blocks_from_result_items(self, page_no: int, width: float, height: float, items: list[Any]) -> list[TextBlock]:
        blocks: list[TextBlock] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            text = str(self._first_value(item, "text", "content", "recText", "rec_text") or "").strip()
            if not text:
                continue
            bbox = self._bbox_from_any(self._first_value(item, "bbox", "box", "poly", "points"), width, height)
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}_paddle_b{index}",
                    page_no=page_no,
                    text=text,
                    bbox=bbox,
                    block_type="ocr_line",
                    confidence=self._optional_float(self._first_value(item, "score", "confidence", "recScore", "rec_score")),
                )
            )
        return sorted(blocks, key=lambda block: (block.bbox.y0, block.bbox.x0))

    def _page_results(self, payload: Any) -> list[Any]:
        candidates = payload.get("downloaded") if isinstance(payload, dict) else payload
        if candidates is None:
            candidates = payload
        if isinstance(candidates, dict):
            for key in ("pages", "ocrResults", "ocr_results", "results"):
                value = candidates.get(key)
                if isinstance(value, list):
                    return [self._unwrap_page_result(item) for item in value]
            result = candidates.get("result")
            if result is not None:
                return self._page_results(result)
            return [self._unwrap_page_result(candidates)]
        if isinstance(candidates, list):
            pages: list[Any] = []
            for item in candidates:
                if isinstance(item, dict):
                    result = item.get("result") or item
                    page_values = result.get("ocrResults") or result.get("ocr_results") or result.get("pages")
                    if isinstance(page_values, list):
                        pages.extend(self._unwrap_page_result(page) for page in page_values)
                    else:
                        pages.append(self._unwrap_page_result(result))
            return pages
        return []

    def _unwrap_page_result(self, item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        return item.get("prunedResult") or item.get("pruned_result") or item

    def _parse_result_text(self, text: str) -> Any:
        text = text.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return items

    def _pdf_page_sizes(self, path: Path) -> list[tuple[float, float]]:
        try:
            pdf = fitz.open(path)
        except Exception:
            return []
        try:
            return [(float(page.rect.width), float(page.rect.height)) for page in pdf]
        finally:
            pdf.close()

    def _bbox_from_any(self, value: Any, width: float, height: float) -> BBox:
        if isinstance(value, dict):
            if all(key in value for key in ("x0", "y0", "x1", "y1")):
                return self._clamp_bbox(value["x0"], value["y0"], value["x1"], value["y1"], width, height)
            if all(key in value for key in ("left", "top", "right", "bottom")):
                return self._clamp_bbox(value["left"], value["top"], value["right"], value["bottom"], width, height)
            if all(key in value for key in ("x", "y", "width", "height")):
                return self._clamp_bbox(value["x"], value["y"], value["x"] + value["width"], value["y"] + value["height"], width, height)
        if isinstance(value, list) and len(value) >= 4:
            if all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in value):
                xs = [float(point[0]) for point in value]
                ys = [float(point[1]) for point in value]
                return self._clamp_bbox(min(xs), min(ys), max(xs), max(ys), width, height)
            return self._clamp_bbox(value[0], value[1], value[2], value[3], width, height)
        return BBox(x0=0, y0=0, x1=width, y1=height)

    def _clamp_bbox(self, x0: Any, y0: Any, x1: Any, y1: Any, width: float, height: float) -> BBox:
        left = float(x0)
        top = float(y0)
        right = float(x1)
        bottom = float(y1)
        if max(left, top, right, bottom) <= 1.0:
            left *= width
            right *= width
            top *= height
            bottom *= height
        left = max(0.0, min(left, width))
        top = max(0.0, min(top, height))
        right = max(left, min(right, width))
        bottom = max(top, min(bottom, height))
        return BBox(x0=left, y0=top, x1=right, y1=bottom)

    def _job_id(self, payload: dict[str, Any]) -> str:
        value = self._first_value(payload, "jobId", "job_id", "id")
        if value:
            return str(value)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        value = self._first_value(data, "jobId", "job_id", "id")
        if value:
            return str(value)
        raise DocumentExtractionError(f"PaddleOCR 未返回 jobId: {payload}")

    def _json_result_url(self, payload: dict[str, Any]) -> str:
        result_url = payload.get("resultUrl") or payload.get("result_url") or {}
        if isinstance(result_url, dict):
            value = result_url.get("jsonUrl") or result_url.get("json_url")
            if value:
                return str(value)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return self._json_result_url(data) if data else str(payload.get("jsonUrl") or payload.get("json_url") or "")

    def _first_value(self, payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    def _list_value(self, payload: dict[str, Any], *keys: str) -> list[Any]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                return [value]
        return []

    def _optional_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    def _save_raw_result(self, payload: dict[str, Any], task_id: str | None, source_path: Path) -> str:
        if not task_id:
            return ""
        directory = settings.ocr_dir / task_id
        directory.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", source_path.stem)[:80] or "document"
        path = directory / f"{stem}_paddleocr_raw.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
