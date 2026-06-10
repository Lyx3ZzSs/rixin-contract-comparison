from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.layout_analysis import LayoutRegion, LayoutResult, PPStructureLayoutAdapter
from app.services.models.remote_model import RemoteModel


REGION_TYPES = {
    "abandon",
    "aside_text",
    "doc_title",
    "figure",
    "figure_caption",
    "figure_title",
    "footer",
    "header",
    "list",
    "number",
    "page_footer",
    "page_header",
    "paragraph_title",
    "reference",
    "seal",
    "table",
    "table_caption",
    "table_footnote",
    "table_title",
    "text",
    "vision_footnote",
}


class LayoutDetector(RemoteModel):
    """PP-Structure remote model backed by the shared layout response adapter."""

    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        timeout: int = 600,
        use_table_recognition: bool = True,
        use_seal_recognition: bool = True,
        use_region_detection: bool = True,
        format_block_content: bool = True,
        use_doc_orientation_classify: bool = False,
        use_doc_unwarping: bool = False,
        use_textline_orientation: bool = False,
        layout_analysis_mode: str = "v2",
    ) -> None:
        super().__init__(
            name="layout_detector",
            base_url=base_url,
            access_token=access_token,
            timeout=timeout,
        )
        self._use_table_recognition = use_table_recognition
        self._use_seal_recognition = use_seal_recognition
        self._use_region_detection = use_region_detection
        self._format_block_content = format_block_content
        self._use_doc_orientation_classify = use_doc_orientation_classify
        self._use_doc_unwarping = use_doc_unwarping
        self._use_textline_orientation = use_textline_orientation
        self._adapter = PPStructureLayoutAdapter(mode=layout_analysis_mode)

    def predict(self, input: Any) -> LayoutResult:
        path = self._resolve_path(input)
        payload = self._post("/layout-parsing", self._request_body(path))
        return self._parse_response(payload, self._adapter.pdf_page_sizes(path))

    def _resolve_path(self, input: Any) -> Path:
        if isinstance(input, Path):
            return input
        if isinstance(input, str):
            return Path(input)
        raise TypeError(f"LayoutDetector.predict expects Path or str, got {type(input)}")

    def _request_body(self, path: Path) -> dict[str, Any]:
        return {
            "file": self._encode_file(path),
            "fileType": 0,
            "useDocOrientationClassify": self._use_doc_orientation_classify,
            "useDocUnwarping": self._use_doc_unwarping,
            "useTextlineOrientation": self._use_textline_orientation,
            "useTableRecognition": self._use_table_recognition,
            "useSealRecognition": self._use_seal_recognition,
            "useRegionDetection": self._use_region_detection,
            "formatBlockContent": self._format_block_content,
            "visualize": False,
        }

    def _parse_response(
        self,
        payload: dict[str, Any],
        page_sizes: list[tuple[float, float]],
    ) -> LayoutResult:
        return self._adapter.parse(payload, page_sizes)


__all__ = ["LayoutDetector", "LayoutRegion", "LayoutResult", "REGION_TYPES"]
