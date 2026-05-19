from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import fitz
import httpx

from app.config import settings
from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult


class PaddleOCRExtractor:
    name = "paddleocr"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        path = Path(path)
        if not path.exists():
            raise DocumentExtractionError(f"文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise DocumentExtractionError("仅支持 PDF 文件。")

        payload = self._predict_pdf(path)
        raw_path = self._save_raw_result(payload, task_id, path) if settings.save_ocr_raw_result and task_id else ""
        document = self.payload_to_document(payload, path)
        return ExtractionResult(document=document, extractor_used=self.name, raw_result_path=raw_path)

    def _predict_pdf(self, path: Path) -> list[dict[str, Any]]:
        url = self._ocr_url()
        headers = {"Content-Type": "application/json"}
        if settings.paddleocr_access_token:
            headers["Authorization"] = f"Bearer {settings.paddleocr_access_token}"
        body = self._request_body(path)
        try:
            with httpx.Client(timeout=settings.paddleocr_timeout_seconds) as client:
                response = client.post(url, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            raise DocumentExtractionError(f"远端 PP-OCRv5 请求失败 ({url}, HTTP {exc.response.status_code}): {detail}") from exc
        except httpx.ConnectError as exc:
            raise DocumentExtractionError(f"无法连接远端 PP-OCRv5 服务 ({url}): {exc}") from exc
        except httpx.TimeoutException as exc:
            raise DocumentExtractionError(f"远端 PP-OCRv5 请求超时 ({url}): {exc}") from exc
        except ValueError as exc:
            raise DocumentExtractionError(f"远端 PP-OCRv5 返回内容不是 JSON: {exc}") from exc
        if payload.get("errorCode") not in (0, None):
            raise DocumentExtractionError(f"远端 PP-OCRv5 识别失败: {payload.get('errorMsg') or payload}")
        return self._normalize_remote_payload(payload)

    def _ocr_url(self) -> str:
        base = settings.paddleocr_job_url.strip().rstrip("/")
        if not base:
            raise DocumentExtractionError("未配置 PADDLEOCR_JOB_URL，无法调用远端 PP-OCRv5。")
        return base if base.endswith("/ocr") else f"{base}/ocr"

    def _request_body(self, path: Path) -> dict[str, Any]:
        return {
            "file": base64.b64encode(path.read_bytes()).decode("ascii"),
            "fileType": 0,
            "useDocOrientationClassify": settings.paddleocr_use_doc_orientation_classify,
            "useDocUnwarping": settings.paddleocr_use_doc_unwarping,
            "useTextlineOrientation": settings.paddleocr_use_textline_orientation,
            "textRecScoreThresh": settings.paddleocr_text_rec_score_thresh,
            "returnWordBox": settings.paddleocr_return_word_box,
            "visualize": False,
        }

    def _normalize_remote_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise DocumentExtractionError(f"远端 PP-OCRv5 返回缺少 result: {payload}")
        ocr_results = result.get("ocrResults") or result.get("ocr_results")
        if not isinstance(ocr_results, list):
            raise DocumentExtractionError(f"远端 PP-OCRv5 返回缺少 ocrResults: {payload}")
        pages = self._remote_pages(result.get("dataInfo"))
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(ocr_results):
            pruned = self._unwrap_page_result(item)
            if not isinstance(pruned, dict):
                continue
            page_payload = dict(pruned)
            page_payload.setdefault("page_index", index)
            if index < len(pages):
                image_width, image_height = pages[index]
                preprocessor = dict(page_payload.get("doc_preprocessor_res") or {})
                preprocessor.setdefault("output_img_shape", [image_height, image_width, 3])
                page_payload["doc_preprocessor_res"] = preprocessor
            normalized.append(page_payload)
        return normalized

    def _remote_pages(self, data_info: Any) -> list[tuple[int, int]]:
        if not isinstance(data_info, dict):
            return []
        if data_info.get("type") == "image":
            width = data_info.get("width")
            height = data_info.get("height")
            if isinstance(width, int) and isinstance(height, int):
                return [(width, height)]
        pages = data_info.get("pages")
        if not isinstance(pages, list):
            return []
        result: list[tuple[int, int]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            width = page.get("width")
            height = page.get("height")
            if isinstance(width, int) and isinstance(height, int):
                result.append((width, height))
        return result

    def _result_to_payload(self, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return dict(result)
        value = getattr(result, "json", None)
        if isinstance(value, dict):
            return dict(value)
        if hasattr(result, "items"):
            return dict(result.items())
        raise DocumentExtractionError(f"PaddleOCR 返回了无法解析的结果类型: {type(result).__name__}")

    def payload_to_document(self, payload: Any, source_path: str | Path) -> Document:
        source_path = Path(source_path)
        page_sizes = self._pdf_page_sizes(source_path)
        page_results = self._page_results(payload)
        page_count = max(len(page_sizes), len(page_results), 1)
        pages: list[Page] = []

        for fallback_index in range(page_count):
            page_payload = page_results[fallback_index] if fallback_index < len(page_results) else {}
            page_no = self._page_no(page_payload, fallback_index)
            width, height = page_sizes[page_no - 1] if 0 <= page_no - 1 < len(page_sizes) else (595.0, 842.0)
            blocks = self._blocks_from_page(page_no, width, height, page_payload)
            pages.append(Page(page_no=page_no, width=width, height=height, blocks=blocks))

        pages.sort(key=lambda page: page.page_no)
        if not any(block.text.strip() for page in pages for block in page.blocks):
            raise DocumentExtractionError("PaddleOCR 未返回可用于对比的文本。")

        return Document(filename=source_path.name, path=str(source_path), page_count=page_count, pages=pages)

    def _blocks_from_page(self, page_no: int, width: float, height: float, page_payload: Any) -> list[TextBlock]:
        pruned = self._unwrap_page_result(page_payload)
        if isinstance(pruned, list):
            return self._blocks_from_result_items(page_no, width, height, pruned)
        if not isinstance(pruned, dict):
            return []

        texts = self._list_value(pruned, "rec_texts", "recTexts", "texts", "text")
        scores = self._list_value(pruned, "rec_scores", "recScores", "scores", "confidence")
        boxes = self._list_value(pruned, "rec_polys", "dt_polys", "rec_boxes", "dtPolys", "polys", "boxes", "recBoxes")
        if not texts and isinstance(pruned.get("ocrResults"), list):
            return self._blocks_from_page(page_no, width, height, {"prunedResult": pruned["ocrResults"]})

        image_width, image_height = self._image_size(pruned, width, height)
        blocks: list[TextBlock] = []
        for index, text_value in enumerate(texts, start=1):
            text = str(text_value).strip()
            if not text:
                continue
            box_value = boxes[index - 1] if index - 1 < len(boxes) else None
            bbox = self._bbox_from_any(box_value, width, height, image_width, image_height)
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}_paddle_b{index}",
                    page_no=page_no,
                    text=text,
                    bbox=bbox,
                    block_type="ocr_line",
                    confidence=self._optional_float(scores[index - 1] if index - 1 < len(scores) else None),
                    char_boxes=self._char_boxes_for_line(
                        pruned,
                        line_index=index - 1,
                        page_no=page_no,
                        line_text=text,
                        width=width,
                        height=height,
                        image_width=image_width,
                        image_height=image_height,
                    ),
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
            bbox = self._bbox_from_any(
                self._first_value(item, "bbox", "box", "poly", "points"),
                width,
                height,
                width,
                height,
            )
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

    def _char_boxes_for_line(
        self,
        payload: dict[str, Any],
        line_index: int,
        page_no: int,
        line_text: str,
        width: float,
        height: float,
        image_width: float,
        image_height: float,
    ) -> list[CharBox]:
        words = self._line_item(payload.get("text_word"), line_index)
        regions = self._line_item(payload.get("text_word_region"), line_index)
        boxes = self._line_item(payload.get("text_word_boxes"), line_index)
        coordinates = regions if regions else boxes
        if not isinstance(words, list) or not isinstance(coordinates, (list, tuple)) or not words:
            return []

        char_boxes: list[CharBox] = []
        cursor = 0
        for word_index, word_value in enumerate(words):
            word = str(word_value)
            if word == "":
                continue
            coordinate = coordinates[word_index] if word_index < len(coordinates) else None
            bbox = self._bbox_from_any(coordinate, width, height, image_width, image_height)
            start = self._find_text_index(line_text, word, cursor)
            if start is None:
                start = cursor
            for offset, char in enumerate(word):
                char_boxes.append(CharBox(char=char, page_no=page_no, bbox=bbox, text_index=start + offset))
            cursor = start + len(word)
        return char_boxes

    def _find_text_index(self, text: str, value: str, start: int) -> int | None:
        index = text.find(value, max(0, start))
        if index >= 0:
            return index
        index = text.find(value)
        return index if index >= 0 else None

    def _page_results(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return [self._unwrap_page_result(item) for item in payload]
        if isinstance(payload, dict):
            for key in ("downloaded", "pages", "ocrResults", "ocr_results", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [self._unwrap_page_result(item) for item in value]
                if isinstance(value, dict):
                    return self._page_results(value)
            result = payload.get("result")
            if result is not None:
                return self._page_results(result)
            return [self._unwrap_page_result(payload)]
        return []

    def _unwrap_page_result(self, item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        if "prunedResult" in item or "pruned_result" in item:
            return item.get("prunedResult") or item.get("pruned_result")
        result = item.get("result")
        if isinstance(result, dict):
            page_values = result.get("ocrResults") or result.get("ocr_results") or result.get("pages")
            if isinstance(page_values, list) and len(page_values) == 1:
                return self._unwrap_page_result(page_values[0])
            return result
        return item

    def _page_no(self, page_payload: Any, fallback_index: int) -> int:
        if isinstance(page_payload, dict):
            page_index = page_payload.get("page_index")
            if page_index is not None:
                try:
                    return max(1, int(page_index) + 1)
                except (TypeError, ValueError):
                    pass
        return fallback_index + 1

    def _pdf_page_sizes(self, path: Path) -> list[tuple[float, float]]:
        try:
            pdf = fitz.open(path)
        except Exception:
            return []
        try:
            return [(float(page.rect.width), float(page.rect.height)) for page in pdf]
        finally:
            pdf.close()

    def _image_size(self, payload: dict[str, Any], width: float, height: float) -> tuple[float, float]:
        preprocessor = payload.get("doc_preprocessor_res")
        if isinstance(preprocessor, dict):
            shape = preprocessor.get("output_img_shape")
            image = preprocessor.get("output_img")
            if shape is None and hasattr(image, "shape"):
                shape = image.shape
            if isinstance(shape, (list, tuple)) and len(shape) >= 2:
                return float(shape[1]), float(shape[0])
        return width, height

    def _bbox_from_any(
        self,
        value: Any,
        width: float,
        height: float,
        image_width: float,
        image_height: float,
    ) -> BBox:
        value = self._plain_value(value)
        if isinstance(value, dict):
            if all(key in value for key in ("x0", "y0", "x1", "y1")):
                return self._clamp_bbox(value["x0"], value["y0"], value["x1"], value["y1"], width, height, image_width, image_height)
            if all(key in value for key in ("left", "top", "right", "bottom")):
                return self._clamp_bbox(value["left"], value["top"], value["right"], value["bottom"], width, height, image_width, image_height)
            if all(key in value for key in ("x", "y", "width", "height")):
                return self._clamp_bbox(
                    value["x"],
                    value["y"],
                    value["x"] + value["width"],
                    value["y"] + value["height"],
                    width,
                    height,
                    image_width,
                    image_height,
                )
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            if all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in value):
                xs = [float(point[0]) for point in value]
                ys = [float(point[1]) for point in value]
                return self._clamp_bbox(min(xs), min(ys), max(xs), max(ys), width, height, image_width, image_height)
            return self._clamp_bbox(value[0], value[1], value[2], value[3], width, height, image_width, image_height)
        return BBox(x0=0, y0=0, x1=width, y1=height)

    def _clamp_bbox(
        self,
        x0: Any,
        y0: Any,
        x1: Any,
        y1: Any,
        width: float,
        height: float,
        image_width: float,
        image_height: float,
    ) -> BBox:
        left = float(x0)
        top = float(y0)
        right = float(x1)
        bottom = float(y1)
        if max(left, top, right, bottom) <= 1.0:
            left *= width
            right *= width
            top *= height
            bottom *= height
        elif image_width > 0 and image_height > 0 and (image_width != width or image_height != height):
            left *= width / image_width
            right *= width / image_width
            top *= height / image_height
            bottom *= height / image_height
        left = max(0.0, min(left, width))
        top = max(0.0, min(top, height))
        right = max(left, min(right, width))
        bottom = max(top, min(bottom, height))
        return BBox(x0=left, y0=top, x1=right, y1=bottom)

    def _line_item(self, value: Any, line_index: int) -> Any:
        value = self._plain_value(value)
        if isinstance(value, list) and line_index < len(value):
            return self._plain_value(value[line_index])
        return None

    def _plain_value(self, value: Any) -> Any:
        if hasattr(value, "tolist"):
            return value.tolist()
        return value

    def _first_value(self, payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    def _list_value(self, payload: dict[str, Any], *keys: str) -> list[Any]:
        for key in keys:
            value = payload.get(key)
            if hasattr(value, "tolist"):
                value = value.tolist()
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

    def _save_raw_result(self, payload: Any, task_id: str | None, source_path: Path) -> str:
        if not task_id:
            return ""
        directory = settings.ocr_dir / task_id
        directory.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", source_path.stem)[:80] or "document"
        path = directory / f"{stem}_paddleocr_raw.json"
        path.write_text(json.dumps(self._jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _jsonable(self, value: Any) -> Any:
        if hasattr(value, "tolist"):
            return value.tolist()
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if key == "output_img":
                    result["output_img_shape"] = list(child.shape) if hasattr(child, "shape") else None
                    continue
                if key == "vis_fonts":
                    continue
                result[str(key)] = self._jsonable(child)
            return result
        if isinstance(value, (list, tuple)):
            return [self._jsonable(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
