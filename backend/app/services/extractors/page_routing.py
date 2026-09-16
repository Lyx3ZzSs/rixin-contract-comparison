"""Conservative page-level native pilot with whole-document fallback."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import fitz

from app.models import Document, LayoutQualityReport, Page
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.native_fast_path import NativeFastPathEvaluator
from app.services.pipeline_metrics import PerformanceRecorder


def remap_page(page: Page, page_no: int) -> Page:
    """Remap only typed page references and identifiers; preserve coordinates/text."""
    result = page.model_copy(deep=True)
    result.page_no = page_no
    for block in result.blocks:
        block.page_no = page_no
        for attr in ("block_id", "layout_block_id", "table_id"):
            value = getattr(block, attr)
            if value:
                setattr(block, attr, f"p{page_no}_routed_" + re.sub(r"^p\d+_", "", value))
        for evidence in [*block.char_boxes, *block.source_evidence]:
            evidence.page_no = page_no
    return result


class PageRoutingExtractor:
    name = "ppstructure_ocr_hybrid_page_routing"

    def __init__(self, fallback, *, mode: str, max_ocr_segments: int = 4, evaluator=None):
        if mode not in {"off", "shadow", "enabled"}:
            raise ValueError("page routing mode must be off, shadow or enabled")
        self.fallback = fallback
        self.mode = mode
        self.max_ocr_segments = max(1, max_ocr_segments)
        self.evaluator = evaluator or NativeFastPathEvaluator()

    def extract(self, path, task_id=None):
        path = Path(path)
        if self.mode == "off":
            return self.fallback.extract(path, task_id)
        recorder = PerformanceRecorder()
        route = {"mode": self.mode, "version": "native-page-pilot-v1", "pages": [], "decision": "evaluating"}

        def fallback(reason):
            route["decision"] = reason
            result = self.fallback.extract(path, task_id)
            result.performance["page_routing"] = {**route, **recorder.snapshot()}
            return result

        try:
            with tempfile.TemporaryDirectory(prefix="compare-page-routing-") as directory, fitz.open(path) as pdf:
                native = {}
                with recorder.measure("page_eligibility"):
                    for index in range(len(pdf)):
                        single = Path(directory) / f"native-{index + 1}.pdf"
                        with fitz.open() as part:
                            part.insert_pdf(pdf, from_page=index, to_page=index)
                            part.save(single)
                        decision = self.evaluator.evaluate(single)
                        route["pages"].append({"page_no": index + 1, "native_eligible": decision.accepted,
                                               "reasons": list(decision.reasons)})
                        if decision.accepted and decision.extraction is not None:
                            native[index + 1] = remap_page(decision.extraction.document.pages[0], index + 1)
                        single.unlink()
                recorder.set_counter("native_eligible_pages", len(native))
                recorder.set_counter("total_pages", len(pdf))
                if self.mode == "shadow":
                    return fallback("shadow_only")
                if not native:
                    return fallback("no_eligible_pages")
                segments = []
                for page_no in range(1, len(pdf) + 1):
                    if page_no in native:
                        continue
                    if not segments or segments[-1][-1] != page_no - 1:
                        segments.append([])
                    segments[-1].append(page_no)
                if len(segments) > self.max_ocr_segments:
                    return fallback("fragmentation_budget_exceeded")
                pages = dict(native)
                raw_paths, warnings, qualities = [], [], []
                with recorder.measure("structured_segments"):
                    for segment in segments:
                        chunk = Path(directory) / f"{path.stem}-pages-{segment[0]}-{segment[-1]}.pdf"
                        with fitz.open() as part:
                            part.insert_pdf(pdf, from_page=segment[0] - 1, to_page=segment[-1] - 1)
                            part.save(chunk)
                        result = self.fallback.extract(chunk, task_id)
                        if result.document.page_count != len(segment) or len(result.document.pages) != len(segment):
                            raise DocumentExtractionError("Routed OCR returned incomplete pages")
                        if [p.page_no for p in result.document.pages] != list(range(1, len(segment) + 1)):
                            raise DocumentExtractionError("Routed OCR page order mismatch")
                        for page, number in zip(result.document.pages, segment, strict=True):
                            pages[number] = remap_page(page, number)
                        raw_paths.append(result.raw_result_path)
                        warnings.extend(result.warnings)
                        if result.layout_quality is not None:
                            quality = result.layout_quality.model_copy(deep=True)
                            for item in [*quality.page_quality, *quality.warnings]:
                                if item.page_no is not None:
                                    item.page_no = segment[item.page_no - 1]
                            qualities.append(quality)
                if set(pages) != set(range(1, len(pdf) + 1)):
                    raise DocumentExtractionError("Routed document has missing pages")
                quality = None
                if qualities:
                    quality = LayoutQualityReport(page_count=len(pdf))
                    for item in qualities:
                        for key in type(quality).model_fields:
                            value = getattr(item, key)
                            if key != "page_count" and isinstance(value, int):
                                setattr(quality, key, getattr(quality, key) + value)
                        quality.page_quality.extend(item.page_quality)
                        quality.warnings.extend(item.warnings)
                        for label, count in item.label_counts.items():
                            quality.label_counts[label] = quality.label_counts.get(label, 0) + count
                route["decision"] = "routed"
                route["ocr_segments"] = segments
                return ExtractionResult(
                    document=Document(filename=path.name, path=str(path), page_count=len(pdf),
                                      pages=[pages[n] for n in sorted(pages)]),
                    extractor_used=self.name, raw_result_path="\n".join(filter(None, raw_paths)),
                    warnings=warnings, layout_quality=quality,
                    performance={"page_routing": {**route, **recorder.snapshot()}},
                )
        except Exception as exc:
            route["fallback_error_type"] = type(exc).__name__
            return fallback("whole_document_fallback")
