from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from app.clients import HttpClientProvider, default_http_client_provider
from app.config import Settings, settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.models import Document, ParseWarningDetail
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.layout_analysis import LayoutResult, PPStructureLayoutAdapter
from app.services.pipeline_metrics import PerformanceRecorder


class PPStructureExtractor:
    name = "ppstructure"

    def __init__(
        self,
        app_settings: Settings = settings,
        client_provider: HttpClientProvider = default_http_client_provider,
        artifact_store: ArtifactStore = default_artifact_store,
    ) -> None:
        self.settings = app_settings
        self.client_provider = client_provider
        self.artifact_store = artifact_store
        self.adapter = PPStructureLayoutAdapter(mode=app_settings.layout_analysis_mode)
        self.last_layout: LayoutResult | None = None

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        return self._extract(path, task_id=task_id, encoded_file=None)

    def extract_with_encoded_file(
        self,
        path: str | Path,
        *,
        encoded_file: str,
        task_id: str | None = None,
    ) -> ExtractionResult:
        return self._extract(path, task_id=task_id, encoded_file=encoded_file)

    def _extract(
        self,
        path: str | Path,
        *,
        task_id: str | None,
        encoded_file: str | None,
    ) -> ExtractionResult:
        path = Path(path)
        if not path.exists():
            raise DocumentExtractionError(f"文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise DocumentExtractionError("仅支持 PDF 文件。")

        recorder = PerformanceRecorder()
        recorder.set_counter("file_size_bytes", path.stat().st_size)
        recorder.set_counter("shared_encoded_file_used", int(encoded_file is not None))
        payload = self._request_layout(
            path,
            performance_recorder=recorder,
            encoded_file=encoded_file,
        )
        raw_path = ""
        if self.settings.save_ocr_raw_result and task_id:
            with recorder.measure("raw_result_write"):
                raw_path = self._save_raw_result(payload, task_id, path)
        with recorder.measure("payload_to_document"):
            document = self.payload_to_document(payload, path)
        quality = self.last_layout.quality if self.last_layout is not None else None
        recorder.set_counter("page_count", document.page_count)
        recorder.set_counter("text_block_count", sum(len(page.blocks) for page in document.pages))
        return ExtractionResult(
            document=document,
            extractor_used=self.name,
            raw_result_path=raw_path,
            layout_quality=quality,
            performance=recorder.snapshot(),
        )

    def _request_layout(
        self,
        path: Path,
        *,
        performance_recorder: PerformanceRecorder | None = None,
        encoded_file: str | None = None,
    ) -> dict[str, Any]:
        recorder = performance_recorder or PerformanceRecorder()
        url = self._layout_url()
        headers = {"Content-Type": "application/json"}
        if self.settings.ppstructure_access_token:
            headers["Authorization"] = f"Bearer {self.settings.ppstructure_access_token}"
        try:
            with recorder.measure("request_body_build"):
                body = self._request_body(path, encoded_file=encoded_file)
            recorder.increment("http_request_count")
            with recorder.measure("http_request"):
                response = self.client_provider.get_structure_client().post(
                    url,
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
            recorder.set_counter("http_response_bytes", len(getattr(response, "content", b"")))
            with recorder.measure("response_json_decode"):
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            recorder.increment("http_failure_count")
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            raise DocumentExtractionError(
                f"远程 PP-Structure 请求失败 ({url}, HTTP {exc.response.status_code}): {detail}"
            ) from exc
        except httpx.ConnectError as exc:
            recorder.increment("http_failure_count")
            raise DocumentExtractionError(f"无法连接远程 PP-Structure 服务 ({url}): {exc}") from exc
        except httpx.TimeoutException as exc:
            recorder.increment("http_failure_count")
            raise DocumentExtractionError(f"远程 PP-Structure 请求超时 ({url}): {exc}") from exc
        except ValueError as exc:
            recorder.increment("http_failure_count")
            raise DocumentExtractionError(f"远程 PP-Structure 返回内容不是 JSON: {exc}") from exc
        if payload.get("errorCode") not in (0, None):
            recorder.increment("remote_error_count")
            raise DocumentExtractionError(f"远程 PP-Structure 解析失败: {payload.get('errorMsg') or payload}")
        result = payload.get("result")
        if isinstance(result, dict):
            pages = result.get("layoutParsingResults") or result.get("layout_parsing_results")
            if isinstance(pages, list):
                recorder.set_counter("remote_page_count", len(pages))
        return payload

    def _layout_url(self) -> str:
        base = self.settings.ppstructure_url.strip().rstrip("/")
        if not base:
            raise DocumentExtractionError("未配置 PPSTRUCTURE_URL，无法调用远程 PP-Structure。")
        return base if base.endswith("/layout-parsing") else f"{base}/layout-parsing"

    def _request_body(self, path: Path, *, encoded_file: str | None = None) -> dict[str, Any]:
        if encoded_file is None:
            encoded_file = base64.b64encode(path.read_bytes()).decode("ascii")
        return {
            "file": encoded_file,
            "fileType": 0,
            "useDocOrientationClassify": self.settings.ppstructure_use_doc_orientation_classify,
            "useDocUnwarping": self.settings.ppstructure_use_doc_unwarping,
            "useTextlineOrientation": self.settings.ppstructure_use_textline_orientation,
            "useTableRecognition": self.settings.ppstructure_use_table_recognition,
            "useSealRecognition": self.settings.ppstructure_use_seal_recognition,
            "useRegionDetection": self.settings.ppstructure_use_region_detection,
            "formatBlockContent": self.settings.ppstructure_format_block_content,
            "visualize": False,
        }

    def payload_to_document(self, payload: dict[str, Any], source_path: str | Path) -> Document:
        source_path = Path(source_path)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise DocumentExtractionError(f"PP-Structure 返回缺少 result: {payload}")
        page_results = result.get("layoutParsingResults") or result.get("layout_parsing_results")
        if not isinstance(page_results, list):
            raise DocumentExtractionError(f"PP-Structure 返回缺少 layoutParsingResults: {payload}")

        page_sizes = self.adapter.pdf_page_sizes(source_path)
        if self.settings.layout_analysis_mode == "shadow":
            layout = PPStructureLayoutAdapter(mode="legacy").parse(payload, page_sizes)
            shadow = PPStructureLayoutAdapter(mode="v2").parse(payload, page_sizes)
            shadow.quality.mode = "shadow"
            if (
                layout.quality.region_count != shadow.quality.region_count
                or layout.quality.invalid_bbox_count != shadow.quality.invalid_bbox_count
                or layout.quality.label_counts != shadow.quality.label_counts
                or [(region.page_number, region.region_type, region.layout_order) for region in layout.regions]
                != [(region.page_number, region.region_type, region.layout_order) for region in shadow.regions]
            ):
                shadow.quality.warnings.append(
                    ParseWarningDetail(
                        code="LAYOUT_SHADOW_DIFFERENCE",
                        message=(
                            "新版版面解析影子结果与旧版存在差异："
                            f"旧版 {layout.quality.region_count} 个区域，"
                            f"新版 {shadow.quality.region_count} 个区域。"
                        ),
                        severity="INFO",
                        source="layout_analysis",
                    )
                )
            layout.quality = shadow.quality
        elif self.settings.layout_analysis_mode == "v3_shadow":
            layout = PPStructureLayoutAdapter(mode="v2").parse(payload, page_sizes)
            shadow = PPStructureLayoutAdapter(mode="v3_shadow").parse(payload, page_sizes)
            shadow.quality.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_V3_SHADOW",
                    message="V3 版面分析正在影子模式运行，生产结果仍使用 V2。",
                    severity="INFO",
                    source="layout_analysis",
                )
            )
            layout.quality = shadow.quality
        else:
            layout = self.adapter.parse(payload, page_sizes)
        document = self.adapter.to_document(layout, source_path)
        self.last_layout = layout
        if not any(block.text.strip() for page in document.pages for block in page.blocks):
            raise DocumentExtractionError("PP-Structure 未返回可用于对比的文本。")
        return document

    def _save_raw_result(self, payload: Any, task_id: str | None, source_path: Path) -> str:
        if not task_id:
            return ""
        path = self.artifact_store.raw_json_path(task_id, source_path, "ppstructure_raw")
        self.artifact_store.write_json(path, payload)
        return str(path)
