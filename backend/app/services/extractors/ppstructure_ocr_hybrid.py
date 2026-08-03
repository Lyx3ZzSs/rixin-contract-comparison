from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz.fuzz import ratio

from app.clients import HttpClientProvider, default_http_client_provider
from app.config import Settings, settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.models import (
    BBox,
    CharBox,
    Document,
    LayoutQualityReport,
    Page,
    PageLayoutQualityReport,
    ParseWarningDetail,
    TextBlock,
)
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.extractors.ppstructure import PPStructureExtractor
from app.services.layout_analysis import bbox_area, flow_role_for_region
from app.services.pipeline_metrics import PerformanceRecorder
from app.services.reading_order import assign_page_reading_order, reading_order_conflict_count
from app.services.text_repair import (
    compact_text_for_repair,
    is_clause_marker_ocr_repair,
    is_delivery_date_placeholder_repair,
)


@dataclass(frozen=True)
class LayoutMatchDecision:
    block: TextBlock | None
    score: float
    status: str
    reason: str


class PPStructureOCRHybridExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(
        self,
        structure_extractor: PPStructureExtractor | None = None,
        ocr_extractor: PPOCRV5Extractor | None = None,
        overlap_threshold: float | None = None,
        require_structure: bool = False,
        component_parallel_enabled: bool | None = None,
        app_settings: Settings = settings,
        client_provider: HttpClientProvider = default_http_client_provider,
        artifact_store: ArtifactStore = default_artifact_store,
    ) -> None:
        self.settings = app_settings
        self.artifact_store = artifact_store
        self.require_structure = require_structure
        self.component_parallel_enabled = (
            app_settings.hybrid_component_parallel_enabled
            if component_parallel_enabled is None
            else component_parallel_enabled
        )
        self.structure_extractor = structure_extractor or PPStructureExtractor(
            app_settings=app_settings,
            client_provider=client_provider,
            artifact_store=artifact_store,
        )
        self.ocr_extractor = ocr_extractor or PPOCRV5Extractor(
            app_settings=app_settings,
            client_provider=client_provider,
            artifact_store=artifact_store,
        )
        self.overlap_threshold = (
            self.settings.hybrid_layout_overlap_threshold if overlap_threshold is None else overlap_threshold
        )

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        source_path = Path(path)
        recorder = PerformanceRecorder()
        if source_path.exists():
            recorder.set_counter("file_size_bytes", source_path.stat().st_size)
        recorder.set_counter(
            "component_parallel_requested",
            int(self.component_parallel_enabled),
        )
        shared_encoded_file = self._build_shared_encoded_file(source_path, recorder)
        with recorder.measure("component_extraction"):
            if self.component_parallel_enabled:
                ocr_result, structure_result, structure_error = self._extract_components_parallel(
                    path,
                    task_id,
                    recorder,
                    shared_encoded_file,
                )
            else:
                ocr_result, structure_result, structure_error = self._extract_components_serial(
                    path,
                    task_id,
                    recorder,
                    shared_encoded_file,
                )
        recorder.set_counter(
            "component_parallel_used",
            int(self.component_parallel_enabled),
        )
        if structure_error is not None:
            recorder.increment("ppstructure_failure_count")
            if self.require_structure:
                raise DocumentExtractionError(f"PP-Structure 结构识别失败: {structure_error}") from structure_error
            ocr_result.extractor_used = "ppstructure_ocr_hybrid_ocr_only"
            ocr_result.warnings.append(f"PP-Structure 结构识别失败，已使用 PP-OCRv5 文本继续处理: {structure_error}")
            ocr_result.performance = {
                **recorder.snapshot(),
                "components": {
                    "ppocrv5": ocr_result.performance,
                    "ppstructure": {},
                },
            }
            return ocr_result
        assert structure_result is not None

        with recorder.measure("document_merge"):
            document = self._merge_documents(ocr_result.document, structure_result.document)
            quality = self._build_layout_quality(document, structure_result.layout_quality)
        raw_result_path = self._merge_raw_paths(structure_result.raw_result_path, ocr_result.raw_result_path)
        if self.settings.save_ocr_raw_result and self.settings.hybrid_save_merged_raw and task_id:
            with recorder.measure("merged_raw_result_write"):
                merged_raw_path = self._save_merged_raw(
                    document,
                    task_id,
                    source_path,
                    structure_result.raw_result_path,
                    ocr_result.raw_result_path,
                )
            raw_result_path = self._merge_raw_paths(raw_result_path, merged_raw_path)

        recorder.set_counter("page_count", document.page_count)
        recorder.set_counter("text_block_count", sum(len(page.blocks) for page in document.pages))
        performance = recorder.snapshot()
        performance["components"] = {
            "ppocrv5": ocr_result.performance,
            "ppstructure": structure_result.performance,
        }
        return ExtractionResult(
            document=document,
            extractor_used=self.name,
            raw_result_path=raw_result_path,
            warnings=[*structure_result.warnings, *ocr_result.warnings],
            layout_quality=quality,
            performance=performance,
        )

    def _extract_components_serial(
        self,
        path: str | Path,
        task_id: str | None,
        recorder: PerformanceRecorder,
        shared_encoded_file: str | None,
    ) -> tuple[ExtractionResult, ExtractionResult | None, DocumentExtractionError | None]:
        with recorder.measure("ppocrv5_component"):
            ocr_result = self._extract_component(
                self.ocr_extractor,
                path,
                task_id,
                shared_encoded_file,
            )
        try:
            with recorder.measure("ppstructure_component"):
                structure_result = self._extract_component(
                    self.structure_extractor,
                    path,
                    task_id,
                    shared_encoded_file,
                )
        except DocumentExtractionError as exc:
            return ocr_result, None, exc
        return ocr_result, structure_result, None

    def _extract_components_parallel(
        self,
        path: str | Path,
        task_id: str | None,
        recorder: PerformanceRecorder,
        shared_encoded_file: str | None,
    ) -> tuple[ExtractionResult, ExtractionResult | None, DocumentExtractionError | None]:
        def extract_ocr() -> ExtractionResult:
            with recorder.measure("ppocrv5_component"):
                return self._extract_component(
                    self.ocr_extractor,
                    path,
                    task_id,
                    shared_encoded_file,
                )

        def extract_structure() -> ExtractionResult:
            with recorder.measure("ppstructure_component"):
                return self._extract_component(
                    self.structure_extractor,
                    path,
                    task_id,
                    shared_encoded_file,
                )

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid-ocr") as executor:
            ocr_future = executor.submit(extract_ocr)
            structure_future = executor.submit(extract_structure)
            ocr_error: Exception | None = None
            structure_error: Exception | None = None
            try:
                ocr_result = ocr_future.result()
            except Exception as exc:
                ocr_error = exc
                ocr_result = None
            try:
                structure_result = structure_future.result()
            except Exception as exc:
                structure_error = exc
                structure_result = None

        if ocr_error is not None:
            raise ocr_error
        assert ocr_result is not None
        if structure_error is None:
            return ocr_result, structure_result, None
        if isinstance(structure_error, DocumentExtractionError):
            return ocr_result, None, structure_error
        raise structure_error

    def _build_shared_encoded_file(
        self,
        source_path: Path,
        recorder: PerformanceRecorder,
    ) -> str | None:
        supports_shared_payload = all(
            callable(getattr(extractor, "extract_with_encoded_file", None))
            for extractor in (self.ocr_extractor, self.structure_extractor)
        )
        recorder.set_counter("shared_request_payload_supported", int(supports_shared_payload))
        if not supports_shared_payload or not source_path.is_file() or source_path.suffix.lower() != ".pdf":
            recorder.set_counter("shared_request_payload_used", 0)
            return None
        with recorder.measure("shared_request_file_encode"):
            encoded_file = base64.b64encode(source_path.read_bytes()).decode("ascii")
        recorder.set_counter("shared_request_payload_used", 1)
        recorder.set_counter("shared_encoded_file_chars", len(encoded_file))
        return encoded_file

    @staticmethod
    def _extract_component(
        extractor: Any,
        path: str | Path,
        task_id: str | None,
        shared_encoded_file: str | None,
    ) -> ExtractionResult:
        extract_with_encoded_file = getattr(extractor, "extract_with_encoded_file", None)
        if shared_encoded_file is not None and callable(extract_with_encoded_file):
            return extract_with_encoded_file(
                path,
                encoded_file=shared_encoded_file,
                task_id=task_id,
            )
        return extractor.extract(path, task_id=task_id)

    def _merge_documents(self, ocr_document: Document, structure_document: Document) -> Document:
        structure_pages = {page.page_no: page for page in structure_document.pages}
        pages: list[Page] = []
        for page in ocr_document.pages:
            merged_page = Page(
                page_no=page.page_no,
                width=page.width,
                height=page.height,
                blocks=self._merge_page_blocks(
                    page.blocks,
                    structure_pages.get(page.page_no),
                    page.width,
                    page.height,
                ),
            )
            assign_page_reading_order(merged_page)
            pages.append(merged_page)
        return ocr_document.model_copy(update={"pages": pages})

    def _merge_page_blocks(
        self,
        ocr_blocks: list[TextBlock],
        structure_page: Page | None,
        page_width: float,
        page_height: float,
    ) -> list[TextBlock]:
        if structure_page is None:
            return [self._unmatched_block(block, page_width, page_height) for block in ocr_blocks]
        structure_blocks = [
            block for block in structure_page.blocks if block.bbox.x1 > block.bbox.x0 and block.bbox.y1 > block.bbox.y0
        ]
        if not structure_blocks:
            return ocr_blocks
        html_tables = self._collect_html_tables(structure_blocks)
        structure_by_id = {block.block_id: block for block in structure_blocks}
        merged = [self._attach_structure(block, structure_blocks, page_width, page_height) for block in ocr_blocks]
        merged = self._consolidate_table_blocks(merged, html_tables)
        merged = self._repair_short_structure_annotations(merged, structure_by_id)
        merged = self._consolidate_structure_text_blocks(merged, structure_by_id, set(html_tables))
        return self._append_structure_only_regions(merged, structure_blocks)

    def _repair_short_structure_annotations(
        self,
        blocks: list[TextBlock],
        structure_blocks: dict[str, TextBlock],
    ) -> list[TextBlock]:
        """Recover short handwritten header text when OCR and structure OCR disagree.

        PP-Structure often groups an entire header into one region while PP-OCR
        provides the individual line boxes.  A low-confidence one-character OCR
        child can therefore be repaired only when the surrounding high-confidence
        children strongly anchor a short CJK replacement in the structure text.
        """
        grouped: dict[str, list[tuple[int, TextBlock]]] = {}
        for index, block in enumerate(blocks):
            if block.layout_block_id:
                grouped.setdefault(block.layout_block_id, []).append((index, block))

        repaired = list(blocks)
        for layout_id, indexed_children in grouped.items():
            structure_block = structure_blocks.get(layout_id)
            if structure_block is None or self._normalize_block_type(structure_block.block_type) != "header":
                continue
            ordered = sorted(indexed_children, key=lambda item: (item[1].bbox.y0, item[1].bbox.x0, item[1].block_id))
            child_texts = [self._compact_annotation_text(block.text) for _, block in ordered]
            structure_text = self._compact_annotation_text(structure_block.text)
            combined_text = "".join(child_texts)
            if not structure_text or not combined_text:
                continue

            matcher = SequenceMatcher(None, combined_text, structure_text)
            if matcher.ratio() < 0.72 or sum(size for _, _, size in matcher.get_matching_blocks()) < 8:
                continue
            opcodes = matcher.get_opcodes()
            offset = 0
            for (result_index, child), child_text in zip(ordered, child_texts, strict=True):
                start = offset
                end = start + len(child_text)
                offset = end
                if not self._is_low_confidence_short_annotation(child, child_text):
                    continue
                replacements = {
                    structure_text[j1:j2]
                    for tag, i1, i2, j1, j2 in opcodes
                    if tag == "replace" and max(start, i1) < min(end, i2) and structure_text[j1:j2]
                }
                if len(replacements) != 1:
                    continue
                replacement = replacements.pop()
                if not re.fullmatch(r"[\u4e00-\u9fff]{1,2}", replacement):
                    continue
                repaired[result_index] = child.model_copy(
                    update={
                        "text": replacement,
                        "source": f"{child.source or 'ppocrv5_layout_matched'}+ppstructure_short_annotation_repair",
                        "char_boxes": self._estimate_structure_char_boxes(replacement, child.bbox, child.page_no),
                    }
                )
        return repaired

    @staticmethod
    def _compact_annotation_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        normalized = re.sub(r"^\s*[#>*-]+\s*", "", normalized)
        return re.sub(r"\s+", "", normalized)

    @staticmethod
    def _is_low_confidence_short_annotation(block: TextBlock, text: str) -> bool:
        confidence = block.confidence if block.confidence is not None else 1.0
        return 1 <= len(text) <= 2 and confidence <= 0.6 and re.fullmatch(r"[A-Za-z0-9|]+", text) is not None

    def _attach_structure(
        self,
        ocr_block: TextBlock,
        structure_blocks: list[TextBlock],
        page_width: float,
        page_height: float,
    ) -> TextBlock:
        decision = self._structure_match_decision(ocr_block, structure_blocks)
        matched = decision.block
        if matched is None:
            return self._unmatched_block(ocr_block, page_width, page_height)
        block_type = self._normalize_block_type(matched.block_type) or ocr_block.block_type
        return ocr_block.model_copy(
            update={
                "block_type": block_type,
                "layout_block_id": matched.block_id,
                "layout_order": matched.layout_order or self._layout_order(matched.block_id),
                "layout_bbox": matched.bbox,
                "block_role": matched.block_role or matched.block_type,
                "flow_role": flow_role_for_region(matched.block_type)
                if self.settings.layout_analysis_mode == "v3"
                else "",
                "layout_match_score": decision.score,
                "layout_match_status": decision.status,
                "layout_match_reason": decision.reason,
                "source": ocr_block.source or "ppocrv5_layout_matched",
            }
        )

    def _best_structure_match(self, bbox: BBox, structure_blocks: list[TextBlock]) -> TextBlock | None:
        probe = TextBlock(block_id="probe", page_no=1, text="", bbox=bbox)
        return self._structure_match_decision(probe, structure_blocks).block

    def _structure_match_decision(
        self,
        ocr_block: TextBlock,
        structure_blocks: list[TextBlock],
    ) -> LayoutMatchDecision:
        scored: list[tuple[float, TextBlock, str]] = []
        for block in structure_blocks:
            coverage = self._overlap_coverage(ocr_block.bbox, block.bbox)
            intersection = self._intersection_area(ocr_block.bbox, block.bbox)
            iou = intersection / max(
                self._area(ocr_block.bbox) + self._area(block.bbox) - intersection,
                1.0,
            )
            contains_center = self._contains_center(ocr_block.bbox, block.bbox)
            area_ratio = self._area(block.bbox) / max(self._area(ocr_block.bbox), 1.0)
            oversized_penalty = min(0.3, max(0.0, area_ratio - 20.0) / 100.0)
            score = coverage * 0.65 + iou * 0.25 + (0.1 if contains_center else 0.0) - oversized_penalty
            reason = f"coverage={coverage:.3f}, iou={iou:.3f}, center={contains_center}"
            scored.append((score, block, reason))

        scored.sort(key=lambda item: (-item[0], self._area(item[1].bbox), item[1].block_id))
        threshold = self.overlap_threshold * 0.65
        if (
            self.settings.layout_analysis_mode == "v3"
            and len(scored) > 1
            and scored[1][0] >= threshold * 0.75
            and scored[0][0] - scored[1][0] <= 0.1
        ):
            top_spatial_score = scored[0][0]
            reranked: list[tuple[float, TextBlock, str]] = []
            for score, block, reason in scored:
                if top_spatial_score - score <= 0.1 and score >= threshold * 0.75:
                    text_similarity = self._layout_text_similarity(ocr_block.text, block.text)
                    score = min(1.0, score * 0.9 + text_similarity * 0.1)
                    reason += f", text_similarity={text_similarity:.3f}"
                reranked.append((score, block, reason))
            scored = sorted(reranked, key=lambda item: (-item[0], self._area(item[1].bbox), item[1].block_id))
        if scored and scored[0][0] >= threshold:
            best_score, best_block, reason = scored[0]
            ambiguous = len(scored) > 1 and scored[1][0] >= threshold and best_score - scored[1][0] <= 0.05
            return LayoutMatchDecision(
                block=best_block,
                score=round(best_score, 4),
                status="ambiguous" if ambiguous else "matched",
                reason=f"{reason}; second_gap={best_score - scored[1][0]:.3f}" if ambiguous else reason,
            )
        if not self.settings.hybrid_layout_center_fallback:
            return LayoutMatchDecision(None, 0.0, "meaningful_unmatched", "no candidate passed match threshold")
        fallback = self._smallest_center_containing_block(ocr_block.bbox, structure_blocks)
        if fallback is None:
            return LayoutMatchDecision(None, 0.0, "meaningful_unmatched", "no spatial or center-containing candidate")
        return LayoutMatchDecision(fallback, round(threshold, 4), "ambiguous", "matched by center-containing fallback")

    def _unmatched_block(self, block: TextBlock, page_width: float, page_height: float) -> TextBlock:
        compact = re.sub(r"\s+", "", block.text or "")
        near_edge = (
            block.bbox.x0 <= page_width * 0.03
            or block.bbox.x1 >= page_width * 0.97
            or block.bbox.y1 <= page_height * 0.03
            or block.bbox.y0 >= page_height * 0.97
        )
        low_confidence = block.confidence is not None and block.confidence < 0.4
        is_noise = len(compact) <= 4 and (near_edge or low_confidence or not compact.isalnum())
        status = "noise_unmatched" if is_noise else "meaningful_unmatched"
        flow_role = "noise" if is_noise else flow_role_for_region(block.block_type)
        return block.model_copy(
            update={
                "source": block.source or ("ppocrv5_noise_unmatched" if is_noise else "ppocrv5_unmatched"),
                "flow_role": flow_role if self.settings.layout_analysis_mode == "v3" else "",
                "layout_match_score": 0.0,
                "layout_match_status": status,
                "layout_match_reason": "short edge/low-confidence OCR fragment"
                if is_noise
                else "no layout region matched",
            }
        )

    @staticmethod
    def _layout_text_similarity(left: str, right: str) -> float:
        left_compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", left or ""))
        right_compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", right or ""))
        if not left_compact or not right_compact:
            return 0.0
        return ratio(left_compact[:1000], right_compact[:1000]) / 100.0

    def _smallest_center_containing_block(self, bbox: BBox, structure_blocks: list[TextBlock]) -> TextBlock | None:
        center_x = (bbox.x0 + bbox.x1) / 2
        center_y = (bbox.y0 + bbox.y1) / 2
        candidates = [
            block
            for block in structure_blocks
            if block.bbox.x0 <= center_x <= block.bbox.x1 and block.bbox.y0 <= center_y <= block.bbox.y1
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda block: self._area(block.bbox))

    def _overlap_coverage(self, inner: BBox, outer: BBox) -> float:
        inner_area = self._area(inner)
        if inner_area <= 0:
            return 0.0
        x0 = max(inner.x0, outer.x0)
        y0 = max(inner.y0, outer.y0)
        x1 = min(inner.x1, outer.x1)
        y1 = min(inner.y1, outer.y1)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return ((x1 - x0) * (y1 - y0)) / inner_area

    def _intersection_area(self, left: BBox, right: BBox) -> float:
        return max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0)) * max(
            0.0, min(left.y1, right.y1) - max(left.y0, right.y0)
        )

    def _contains_center(self, inner: BBox, outer: BBox) -> bool:
        center_x = (inner.x0 + inner.x1) / 2
        center_y = (inner.y0 + inner.y1) / 2
        return outer.x0 <= center_x <= outer.x1 and outer.y0 <= center_y <= outer.y1

    def _area(self, bbox: BBox) -> float:
        return bbox_area(bbox)

    def _layout_order(self, block_id: str) -> int | None:
        match = re.search(r"_b(\d+)$", block_id or "")
        return int(match.group(1)) if match else None

    @staticmethod
    def _append_structure_only_regions(
        blocks: list[TextBlock],
        structure_blocks: list[TextBlock],
    ) -> list[TextBlock]:
        attached = {block.layout_block_id for block in blocks if block.layout_block_id}
        keep_types = {"seal", "figure", "image", "chart", "formula", "abandon"}
        result = list(blocks)
        for structure_block in structure_blocks:
            if structure_block.block_id in attached or structure_block.block_type not in keep_types:
                continue
            result.append(
                structure_block.model_copy(
                    update={
                        "layout_block_id": structure_block.block_id,
                        "layout_bbox": structure_block.bbox,
                        "source": "ppstructure_layout_only",
                        "flow_role": (
                            flow_role_for_region(structure_block.block_type) if structure_block.flow_role else ""
                        ),
                        "layout_match_status": "structure_only",
                        "layout_match_reason": "PP-Structure region has no OCR child",
                    }
                )
            )
        return result

    @staticmethod
    def _collect_html_tables(structure_blocks: list[TextBlock]) -> dict[str, tuple[str, list[list[float]], BBox]]:
        return {
            b.block_id: (b.text, b.table_cell_bboxes, b.bbox)
            for b in structure_blocks
            if b.block_type in {"table", "table_title", "table_cell"} and "<table" in (b.text or "").lower()
        }

    @staticmethod
    def _consolidate_table_blocks(
        blocks: list[TextBlock], html_tables: dict[str, tuple[str, list[list[float]], BBox]]
    ) -> list[TextBlock]:
        if not html_tables:
            return blocks
        table_children: dict[str, list[TextBlock]] = {}
        for block in blocks:
            if block.layout_block_id in html_tables:
                table_children.setdefault(block.layout_block_id, []).append(block)

        seen: set[str] = set()
        result: list[TextBlock] = []
        for block in blocks:
            layout_id = block.layout_block_id
            if layout_id in html_tables:
                if layout_id in seen:
                    continue
                seen.add(layout_id)
                html_text, cell_bboxes, layout_bbox = html_tables[layout_id]
                merged_text, merged_char_boxes = PPStructureOCRHybridExtractor._merge_table_ocr_children(
                    table_children.get(layout_id, [block])
                )
                update = {
                    "bbox": layout_bbox,
                    "raw_html": html_text,
                    "table_cell_bboxes": cell_bboxes,
                }
                if merged_text:
                    update["text"] = merged_text
                if merged_char_boxes:
                    update["char_boxes"] = merged_char_boxes
                block = block.model_copy(update=update)
            result.append(block)
        return result

    @staticmethod
    def _merge_table_ocr_children(blocks: list[TextBlock]) -> tuple[str, list[CharBox]]:
        parts: list[str] = []
        char_boxes: list[CharBox] = []
        offset = 0

        for block in blocks:
            text = block.text or ""
            if not text:
                continue
            parts.append(text)
            for local_index, char_box in enumerate(block.char_boxes):
                source_index = char_box.text_index if char_box.text_index is not None else local_index
                char_boxes.append(char_box.model_copy(update={"text_index": offset + source_index}))
            offset += len(text) + 1

        return "\n".join(parts), char_boxes

    def _consolidate_structure_text_blocks(
        self,
        blocks: list[TextBlock],
        structure_blocks: dict[str, TextBlock],
        table_layout_ids: set[str],
    ) -> list[TextBlock]:
        text_children: dict[str, list[TextBlock]] = {}
        for block in blocks:
            layout_id = block.layout_block_id
            if not layout_id or layout_id in table_layout_ids:
                continue
            structure_block = structure_blocks.get(layout_id)
            if structure_block is None or not self._is_replaceable_structure_text(structure_block):
                continue
            text_children.setdefault(layout_id, []).append(block)

        replacements: dict[str, TextBlock] = {}
        for layout_id, children in text_children.items():
            structure_block = structure_blocks[layout_id]
            replacement = self._structure_text_replacement(structure_block, children)
            if replacement is not None:
                replacements[layout_id] = replacement

        if not replacements:
            return blocks

        seen: set[str] = set()
        result: list[TextBlock] = []
        for block in blocks:
            layout_id = block.layout_block_id
            replacement = replacements.get(layout_id)
            if replacement is None:
                result.append(block)
                continue
            if layout_id in seen:
                continue
            seen.add(layout_id)
            result.append(replacement)
        return result

    def _structure_text_replacement(self, structure_block: TextBlock, children: list[TextBlock]) -> TextBlock | None:
        structure_text = unicodedata.normalize("NFKC", structure_block.text or "").strip()
        if not structure_text:
            return None
        ordered_children = self._ordered_text_children_for_structure(structure_block, children)
        ocr_text = "\n".join(block.text.strip() for block in ordered_children if block.text.strip())
        if not self._should_trust_structure_text(structure_text, ocr_text):
            return None

        return ordered_children[0].model_copy(
            update={
                "block_id": ordered_children[0].block_id,
                "text": structure_text,
                "bbox": structure_block.bbox,
                "block_type": self._normalize_block_type(structure_block.block_type) or "text",
                "layout_block_id": structure_block.block_id,
                "layout_order": structure_block.layout_order or self._layout_order(structure_block.block_id),
                "layout_bbox": structure_block.bbox,
                "confidence": structure_block.confidence,
                "source": "ppstructure_text",
                "char_boxes": self._estimate_structure_char_boxes(
                    structure_text, structure_block.bbox, structure_block.page_no
                ),
            }
        )

    def _ordered_text_children_for_structure(
        self, structure_block: TextBlock, children: list[TextBlock]
    ) -> list[TextBlock]:
        if self._is_single_line_structure_block(structure_block, children):
            return sorted(children, key=lambda block: (block.bbox.x0, block.bbox.y0, block.block_id))
        return children

    def _is_single_line_structure_block(self, structure_block: TextBlock, children: list[TextBlock]) -> bool:
        if not children:
            return False
        structure_height = structure_block.bbox.y1 - structure_block.bbox.y0
        child_heights = sorted(max(0.0, block.bbox.y1 - block.bbox.y0) for block in children)
        median_child_height = child_heights[len(child_heights) // 2] if child_heights else 0.0
        return structure_height <= max(24.0, median_child_height * 2.0)

    def _should_trust_structure_text(self, structure_text: str, ocr_text: str) -> bool:
        if is_delivery_date_placeholder_repair(structure_text, ocr_text):
            return True
        if is_clause_marker_ocr_repair(structure_text, ocr_text):
            return True
        structure_norm = compact_text_for_repair(structure_text)
        ocr_norm = compact_text_for_repair(ocr_text)
        if not structure_norm or not ocr_norm or structure_norm == ocr_norm:
            return False
        if min(len(structure_norm), len(ocr_norm)) < 8:
            return False
        similarity = SequenceMatcher(None, structure_norm, ocr_norm).ratio()
        if similarity < 0.92:
            return False
        return self._only_confusable_text_differences(structure_norm, ocr_norm)

    @staticmethod
    def _only_confusable_text_differences(structure_norm: str, ocr_norm: str) -> bool:
        if len(structure_norm) != len(ocr_norm):
            return False
        confusables = {
            ("且", "日"),
            ("目", "日"),
            ("曰", "日"),
            ("口", "日"),
        }
        differences = [
            (ocr_char, structure_char)
            for structure_char, ocr_char in zip(structure_norm, ocr_norm, strict=True)
            if structure_char != ocr_char
        ]
        return 0 < len(differences) <= 2 and all(pair in confusables for pair in differences)

    def _is_replaceable_structure_text(self, block: TextBlock) -> bool:
        block_type = self._normalize_block_type(block.block_type)
        return block_type in {"text", "paragraph", "content", "list", "reference"}

    @staticmethod
    def _estimate_structure_char_boxes(text: str, bbox: BBox, page_no: int) -> list[CharBox]:
        lines = text.splitlines() or [text]
        line_count = max(1, len(lines))
        line_height = max(1.0, (bbox.y1 - bbox.y0) / line_count)
        char_boxes: list[CharBox] = []
        text_index = 0
        for line_index, line in enumerate(lines):
            if line_index > 0:
                text_index += 1
            visible_count = max(1, len(line))
            char_width = max(1.0, bbox.x1 - bbox.x0) / visible_count
            y0 = bbox.y0 + line_index * line_height
            y1 = min(bbox.y1, y0 + line_height)
            for char_index, char in enumerate(line):
                x0 = bbox.x0 + char_index * char_width
                x1 = bbox.x0 + (char_index + 1) * char_width
                if not char.isspace():
                    char_boxes.append(
                        CharBox(
                            char=char,
                            page_no=page_no,
                            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                            text_index=text_index,
                        )
                    )
                text_index += 1
        return char_boxes

    def _normalize_block_type(self, block_type: str) -> str:
        value = (block_type or "").strip().lower()
        if value == "table":
            return "table"
        if value in {"table_title", "table_caption", "table_footnote"}:
            return value
        if value in {"header", "page_header"}:
            return "header"
        if value in {"footer", "page_footer"}:
            return "footer"
        if value in {"footnote", "vision_footnote"}:
            return "footnote"
        if value in {"doc_title", "title"}:
            return value
        if value in {"paragraph", "text", "content", "list", "reference"}:
            return "text"
        return value

    def _build_layout_quality(
        self,
        document: Document,
        structure_quality: LayoutQualityReport | None,
    ) -> LayoutQualityReport:
        quality = structure_quality.model_copy(deep=True) if structure_quality is not None else LayoutQualityReport()
        blocks = [block for page in document.pages for block in page.blocks]
        ocr_blocks = [block for block in blocks if not block.source.startswith("ppstructure_layout_only")]
        matched = [block for block in ocr_blocks if block.layout_block_id]
        quality.ocr_block_count = len(ocr_blocks)
        quality.matched_ocr_block_count = len(matched)
        quality.unmatched_ocr_block_count = len(ocr_blocks) - len(matched)
        quality.ambiguous_match_count = sum(block.layout_match_status == "ambiguous" for block in ocr_blocks)
        quality.meaningful_unmatched_count = sum(
            block.layout_match_status == "meaningful_unmatched" for block in ocr_blocks
        )
        quality.noise_unmatched_count = sum(block.layout_match_status == "noise_unmatched" for block in ocr_blocks)
        quality.structure_only_count = sum(block.layout_match_status == "structure_only" for block in blocks)
        quality.reading_order_count = sum(block.reading_order is not None for block in blocks)
        quality.reading_order_conflict_count = sum(reading_order_conflict_count(page) for page in document.pages)
        quality.page_quality = [self._page_layout_quality(page) for page in document.pages]
        v3_diagnostics = quality.parser_version == "v3"
        if quality.ocr_block_count and quality.matched_ocr_block_count / quality.ocr_block_count < 0.98:
            quality.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_LOW_MATCH_RATE",
                    message=(
                        f"版面区域匹配率为 "
                        f"{quality.matched_ocr_block_count / quality.ocr_block_count:.1%}，"
                        f"有 {quality.unmatched_ocr_block_count} 个 OCR 块未匹配。"
                    ),
                    source="layout_analysis",
                )
            )
        if v3_diagnostics and quality.ambiguous_match_count:
            quality.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_AMBIGUOUS_MATCH",
                    message=f"有 {quality.ambiguous_match_count} 个 OCR 块存在多个接近的版面候选。",
                    source="layout_analysis",
                )
            )
        if v3_diagnostics and quality.meaningful_unmatched_count:
            quality.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_MEANINGFUL_UNMATCHED",
                    message=f"有 {quality.meaningful_unmatched_count} 个有效 OCR 块未匹配到版面区域。",
                    source="layout_analysis",
                )
            )
        if v3_diagnostics and quality.reading_order_conflict_count:
            quality.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_READING_ORDER_CONFLICT",
                    message=f"检测到 {quality.reading_order_conflict_count} 个模型顺序与几何阅读顺序冲突。",
                    source="layout_analysis",
                )
            )
        if blocks and quality.reading_order_count != len(blocks):
            quality.warnings.append(
                ParseWarningDetail(
                    code="LAYOUT_READING_ORDER_INCOMPLETE",
                    message=f"有 {len(blocks) - quality.reading_order_count} 个版面块缺少阅读顺序。",
                    source="layout_analysis",
                )
            )
        return quality

    @staticmethod
    def _page_layout_quality(page: Page) -> PageLayoutQualityReport:
        ocr_blocks = [block for block in page.blocks if not block.source.startswith("ppstructure_layout_only")]
        issues: list[str] = []
        ambiguous = sum(block.layout_match_status == "ambiguous" for block in ocr_blocks)
        meaningful = sum(block.layout_match_status == "meaningful_unmatched" for block in ocr_blocks)
        conflicts = reading_order_conflict_count(page)
        if ambiguous:
            issues.append("ambiguous_match")
        if meaningful:
            issues.append("meaningful_unmatched")
        if conflicts:
            issues.append("reading_order_conflict")
        return PageLayoutQualityReport(
            page_no=page.page_no,
            region_count=len({block.layout_block_id for block in page.blocks if block.layout_block_id}),
            ocr_block_count=len(ocr_blocks),
            matched_ocr_block_count=sum(bool(block.layout_block_id) for block in ocr_blocks),
            ambiguous_match_count=ambiguous,
            meaningful_unmatched_count=meaningful,
            noise_unmatched_count=sum(block.layout_match_status == "noise_unmatched" for block in ocr_blocks),
            structure_only_count=sum(block.layout_match_status == "structure_only" for block in page.blocks),
            reading_order_conflict_count=conflicts,
            issues=issues,
        )

    def _save_merged_raw(
        self,
        document: Document,
        task_id: str,
        source_path: Path,
        structure_raw_path: str,
        ocr_raw_path: str,
    ) -> str:
        path = self.artifact_store.raw_json_path(task_id, source_path, "ppstructure_ocr_hybrid_raw")
        self.artifact_store.write_json(
            path,
            {
                "structure_raw_path": structure_raw_path,
                "ocr_raw_path": ocr_raw_path,
                "document": document.model_dump(),
            },
        )
        return str(path)

    def _merge_raw_paths(self, *paths: str) -> str:
        return "\n".join(path for path in paths if path)
