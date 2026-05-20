from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import fitz
import httpx

from app.config import settings
from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult


class PaddleOCRVLExtractor:
    name = "paddleocr_vl"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        path = Path(path)
        if not path.exists():
            raise DocumentExtractionError(f"文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise DocumentExtractionError("仅支持 PDF 文件。")

        if settings.paddleocr_vl_page_mode:
            payload = self._request_layout_by_page(path, task_id=task_id)
        else:
            payload = self._request_layout(path)
        raw_path = self._save_raw_result(payload, task_id, path) if settings.save_ocr_raw_result and task_id else ""
        document = self.payload_to_document(payload, path)
        return ExtractionResult(document=document, extractor_used=self.name, raw_result_path=raw_path)

    def _request_layout_by_page(self, path: Path, task_id: str | None = None) -> dict[str, Any]:
        try:
            source = fitz.open(path)
        except Exception as exc:
            raise DocumentExtractionError(f"无法按页读取 PDF，不能调用 PaddleOCR-VL-1.5: {exc}") from exc
        try:
            if source.page_count <= 1:
                return self._request_layout_bytes(path.read_bytes(), page_no=1)

            page_payloads: list[dict[str, Any]] = []
            for page_index in range(source.page_count):
                page_no = page_index + 1
                page_bytes = self._single_page_pdf_bytes(source, page_index)
                try:
                    page_payloads.append(self._request_layout_bytes(page_bytes, page_no=page_no))
                except DocumentExtractionError:
                    if page_payloads and settings.save_ocr_raw_result and task_id:
                        partial_payload = self._merge_page_payloads(page_payloads, source, include_page_responses=True)
                        self._save_raw_result(partial_payload, task_id, path, suffix="paddleocr_vl_partial_raw")
                    raise
            return self._merge_page_payloads(page_payloads, source, include_page_responses=True)
        finally:
            source.close()

    def _single_page_pdf_bytes(self, source: fitz.Document, page_index: int) -> bytes:
        document = fitz.open()
        try:
            document.insert_pdf(source, from_page=page_index, to_page=page_index)
            return document.tobytes()
        finally:
            document.close()

    def _merge_page_payloads(
        self,
        page_payloads: list[dict[str, Any]],
        source: fitz.Document,
        *,
        include_page_responses: bool = False,
    ) -> dict[str, Any]:
        layout_results: list[Any] = []
        remote_pages: list[dict[str, int]] = []
        for page_index, payload in enumerate(page_payloads):
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                continue
            page_results = result.get("layoutParsingResults") or result.get("layout_parsing_results")
            if isinstance(page_results, list) and page_results:
                layout_results.append(page_results[0])
            data_pages = self._remote_pages(result.get("dataInfo"))
            if data_pages:
                width, height = data_pages[0]
            else:
                page = source[page_index]
                width, height = int(page.rect.width), int(page.rect.height)
            remote_pages.append({"width": int(width), "height": int(height)})

        merged: dict[str, Any] = {
            "errorCode": 0,
            "errorMsg": "Success",
            "result": {
                "layoutParsingResults": layout_results,
                "dataInfo": {
                    "type": "pdf",
                    "numPages": len(layout_results),
                    "pages": remote_pages,
                },
            },
        }
        if include_page_responses:
            merged["pageResponses"] = page_payloads
        return merged

    def _request_layout(self, path: Path) -> dict[str, Any]:
        return self._request_layout_bytes(path.read_bytes())

    def _request_layout_bytes(self, file_bytes: bytes, page_no: int | None = None) -> dict[str, Any]:
        url = self._layout_url()
        headers = {"Content-Type": "application/json"}
        if settings.paddleocr_vl_access_token:
            headers["Authorization"] = f"Bearer {settings.paddleocr_vl_access_token}"
        body = self._request_body(file_bytes)
        try:
            payload = self._post_with_retry(url, headers, body)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise DocumentExtractionError(
                f"{self._request_context(page_no)}请求失败 ({url}, HTTP {status_code}): {detail}"
            ) from exc
        except httpx.ConnectError as exc:
            raise DocumentExtractionError(f"{self._request_context(page_no)}无法连接远端服务 ({url}): {exc}") from exc
        except httpx.TimeoutException as exc:
            raise DocumentExtractionError(f"{self._request_context(page_no)}请求超时 ({url}): {exc}") from exc
        except ValueError as exc:
            raise DocumentExtractionError(f"{self._request_context(page_no)}返回内容不是 JSON: {exc}") from exc
        if payload.get("errorCode") not in (0, None):
            raise DocumentExtractionError(f"{self._request_context(page_no)}解析失败: {payload.get('errorMsg') or payload}")
        return payload

    def _post_with_retry(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        attempts = max(1, settings.paddleocr_vl_retry_count + 1)
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=settings.paddleocr_vl_timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=body)
                    response.raise_for_status()
                    return response.json()
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt >= attempts - 1:
                    raise
        raise DocumentExtractionError("PaddleOCR-VL-1.5 请求重试后仍未返回结果。")

    def _request_context(self, page_no: int | None = None) -> str:
        if page_no is None:
            return "远端 PaddleOCR-VL-1.5 "
        return f"第 {page_no} 页 PaddleOCR-VL-1.5 "

    def _layout_url(self) -> str:
        base = settings.paddleocr_vl_url.strip().rstrip("/")
        if not base:
            raise DocumentExtractionError("未配置 PADDLEOCR_VL_URL，无法调用远端 PaddleOCR-VL-1.5。")
        return base if base.endswith("/layout-parsing") else f"{base}/layout-parsing"

    def _request_body(self, file_bytes: bytes) -> dict[str, Any]:
        body: dict[str, Any] = {
            "file": base64.b64encode(file_bytes).decode("ascii"),
            "fileType": 0,
            "useDocOrientationClassify": settings.paddleocr_vl_use_doc_orientation_classify,
            "useDocUnwarping": settings.paddleocr_vl_use_doc_unwarping,
            "useLayoutDetection": settings.paddleocr_vl_use_layout_detection,
            "useChartRecognition": settings.paddleocr_vl_use_chart_recognition,
            "useSealRecognition": settings.paddleocr_vl_use_seal_recognition,
            "useOcrForImageBlock": settings.paddleocr_vl_use_ocr_for_image_block,
            "formatBlockContent": settings.paddleocr_vl_format_block_content,
            "mergeLayoutBlocks": settings.paddleocr_vl_merge_layout_blocks,
            "prettifyMarkdown": settings.paddleocr_vl_prettify_markdown,
            "visualize": False,
        }
        if settings.paddleocr_vl_max_pixels > 0:
            body["maxPixels"] = settings.paddleocr_vl_max_pixels
        if settings.paddleocr_vl_max_new_tokens > 0:
            body["maxNewTokens"] = settings.paddleocr_vl_max_new_tokens
        return body

    def payload_to_document(self, payload: dict[str, Any], source_path: str | Path) -> Document:
        source_path = Path(source_path)
        page_sizes = self._pdf_page_sizes(source_path)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise DocumentExtractionError(f"PaddleOCR-VL-1.5 返回缺少 result: {payload}")
        page_results = result.get("layoutParsingResults") or result.get("layout_parsing_results")
        if not isinstance(page_results, list):
            raise DocumentExtractionError(f"PaddleOCR-VL-1.5 返回缺少 layoutParsingResults: {payload}")

        remote_sizes = self._remote_pages(result.get("dataInfo"))
        page_count = max(len(page_sizes), len(page_results), 1)
        pages: list[Page] = []
        for fallback_index in range(page_count):
            width, height = page_sizes[fallback_index] if fallback_index < len(page_sizes) else (595.0, 842.0)
            remote_width, remote_height = remote_sizes[fallback_index] if fallback_index < len(remote_sizes) else (width, height)
            page_payload = page_results[fallback_index] if fallback_index < len(page_results) else {}
            blocks = self._blocks_from_page(fallback_index + 1, width, height, remote_width, remote_height, page_payload)
            pages.append(Page(page_no=fallback_index + 1, width=width, height=height, blocks=blocks))

        if not any(block.text.strip() for page in pages for block in page.blocks):
            raise DocumentExtractionError("PaddleOCR-VL-1.5 未返回可用于对比的文本。")
        return Document(filename=source_path.name, path=str(source_path), page_count=page_count, pages=pages)

    def _blocks_from_page(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_width: float,
        remote_height: float,
        page_payload: Any,
    ) -> list[TextBlock]:
        if not isinstance(page_payload, dict):
            return []
        pruned = page_payload.get("prunedResult") or page_payload.get("pruned_result") or {}
        if not isinstance(pruned, dict):
            pruned = {}

        items = pruned.get("parsing_res_list") or pruned.get("parsingResList")
        if isinstance(items, list):
            blocks: list[TextBlock] = []
            for index, item in enumerate(items):
                block = self._block_from_parsing_item(page_no, width, height, remote_width, remote_height, item, index)
                if block is not None:
                    blocks.append(block)
            if blocks:
                return sorted(blocks, key=lambda block: (block.bbox.y0, block.bbox.x0))

        overall_blocks = self._blocks_from_overall_ocr(page_no, width, height, remote_width, remote_height, pruned)
        if overall_blocks:
            return overall_blocks
        markdown_block = self._block_from_markdown(page_no, width, height, page_payload)
        return [markdown_block] if markdown_block is not None else []

    def _block_from_parsing_item(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_width: float,
        remote_height: float,
        item: Any,
        index: int,
    ) -> TextBlock | None:
        if not isinstance(item, dict):
            return None
        text = self._clean_text(item.get("block_content") or item.get("content") or item.get("text") or "")
        if not text:
            return None
        block_label = str(item.get("block_label") or item.get("label") or "text")
        bbox = self._bbox_from_any(item.get("block_bbox") or item.get("bbox"), width, height, remote_width, remote_height)
        return TextBlock(
            block_id=f"p{page_no}_paddleocr_vl_b{index + 1}",
            page_no=page_no,
            text=text,
            bbox=bbox,
            block_type=block_label,
        )

    def _blocks_from_overall_ocr(
        self,
        page_no: int,
        width: float,
        height: float,
        remote_width: float,
        remote_height: float,
        pruned: dict[str, Any],
    ) -> list[TextBlock]:
        overall = pruned.get("overall_ocr_res")
        if not isinstance(overall, dict):
            return []
        texts = overall.get("rec_texts") if isinstance(overall.get("rec_texts"), list) else []
        boxes = overall.get("rec_boxes") or overall.get("rec_polys") or []
        blocks: list[TextBlock] = []
        for index, text_value in enumerate(texts):
            text = self._clean_text(text_value)
            if not text:
                continue
            bbox = self._bbox_from_any(boxes[index] if index < len(boxes) else None, width, height, remote_width, remote_height)
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}_paddleocr_vl_ocr_b{index + 1}",
                    page_no=page_no,
                    text=text,
                    bbox=bbox,
                    block_type="text",
                )
            )
        return sorted(blocks, key=lambda block: (block.bbox.y0, block.bbox.x0))

    def _block_from_markdown(self, page_no: int, width: float, height: float, page_payload: dict[str, Any]) -> TextBlock | None:
        markdown = page_payload.get("markdown")
        if not isinstance(markdown, dict):
            return None
        text = self._clean_text(markdown.get("text") or "")
        if not text:
            return None
        return TextBlock(
            block_id=f"p{page_no}_paddleocr_vl_markdown",
            page_no=page_no,
            text=text,
            bbox=BBox(x0=0, y0=0, x1=width, y1=height),
            block_type="markdown",
        )

    def _clean_text(self, value: Any) -> str:
        text = str(value or "")
        lines = [re.sub(r"^#+\s*", "", line).strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def _bbox_from_any(self, value: Any, width: float, height: float, remote_width: float, remote_height: float) -> BBox:
        if isinstance(value, dict):
            if all(key in value for key in ("x0", "y0", "x1", "y1")):
                return self._clamp_bbox(value["x0"], value["y0"], value["x1"], value["y1"], width, height, remote_width, remote_height)
            if all(key in value for key in ("left", "top", "right", "bottom")):
                return self._clamp_bbox(value["left"], value["top"], value["right"], value["bottom"], width, height, remote_width, remote_height)
            if all(key in value for key in ("x", "y", "width", "height")):
                return self._clamp_bbox(
                    value["x"],
                    value["y"],
                    value["x"] + value["width"],
                    value["y"] + value["height"],
                    width,
                    height,
                    remote_width,
                    remote_height,
                )
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            if all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in value):
                xs = [float(point[0]) for point in value]
                ys = [float(point[1]) for point in value]
                return self._clamp_bbox(min(xs), min(ys), max(xs), max(ys), width, height, remote_width, remote_height)
            return self._clamp_bbox(value[0], value[1], value[2], value[3], width, height, remote_width, remote_height)
        return BBox(x0=0, y0=0, x1=width, y1=height)

    def _clamp_bbox(
        self,
        x0: Any,
        y0: Any,
        x1: Any,
        y1: Any,
        width: float,
        height: float,
        remote_width: float,
        remote_height: float,
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
        elif remote_width > 0 and remote_height > 0 and (remote_width != width or remote_height != height):
            left *= width / remote_width
            right *= width / remote_width
            top *= height / remote_height
            bottom *= height / remote_height
        left = max(0.0, min(left, width))
        top = max(0.0, min(top, height))
        right = max(left, min(right, width))
        bottom = max(top, min(bottom, height))
        return BBox(x0=left, y0=top, x1=right, y1=bottom)

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

    def _pdf_page_sizes(self, path: Path) -> list[tuple[float, float]]:
        try:
            pdf = fitz.open(path)
        except Exception:
            return []
        try:
            return [(float(page.rect.width), float(page.rect.height)) for page in pdf]
        finally:
            pdf.close()

    def _save_raw_result(
        self,
        payload: Any,
        task_id: str | None,
        source_path: Path,
        *,
        suffix: str = "paddleocr_vl_raw",
    ) -> str:
        if not task_id:
            return ""
        directory = settings.ocr_dir / task_id
        directory.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", source_path.stem)[:80] or "document"
        path = directory / f"{stem}_{suffix}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
