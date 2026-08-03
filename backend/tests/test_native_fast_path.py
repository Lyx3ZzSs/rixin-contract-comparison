from __future__ import annotations

from pathlib import Path

import fitz

from app.config import Settings
from app.infrastructure.artifact_store import LocalArtifactStore
from app.models import BBox, CompareTask, Document, Page, TextBlock
from app.services.extractors.base import ExtractionResult
from app.services.native_fast_path import NativeFastPathDecision, NativeFastPathEvaluator
from app.services.pipeline import PipelineContext
from app.services.pipeline_stages import ExtractionStage
from scripts.benchmark_native_fast_path import evaluate_pairs


def _write_text_pdf(
    path: Path,
    text: str,
    *,
    columns: bool = False,
    table: bool = False,
    red_vector: bool = False,
) -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    if columns:
        page.insert_textbox(
            fitz.Rect(50, 60, 270, 700),
            text,
            fontsize=9,
        )
        page.insert_textbox(
            fitz.Rect(330, 60, 550, 700),
            text.replace("original", "replacement"),
            fontsize=9,
        )
    else:
        page.insert_textbox(
            fitz.Rect(50, 60, 545, 700),
            text,
            fontsize=10,
        )
    if table:
        x_positions = (60, 220, 380, 535)
        y_positions = (400, 450, 500, 550)
        for x in x_positions:
            page.draw_line((x, y_positions[0]), (x, y_positions[-1]))
        for y in y_positions:
            page.draw_line((x_positions[0], y), (x_positions[-1], y))
        page.insert_text((70, 430), "No. Product Quantity Amount", fontsize=9)
        page.insert_text((70, 480), "1 Service 2 1000", fontsize=9)
    if red_vector:
        page.draw_circle((470, 620), 45, color=(1, 0, 0), width=3)
    pdf.save(path)
    pdf.close()


def _plain_text() -> str:
    return "\n".join(
        f"Article {index}. The supplier provides ordinary contract services and the customer pays on time."
        for index in range(1, 9)
    )


def _document(filename: str) -> Document:
    return Document(
        filename=filename,
        path=filename,
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id=f"{filename}-b1",
                        page_no=1,
                        text=_plain_text(),
                        bbox=BBox(x0=50, y0=60, x1=545, y1=500),
                    )
                ],
            )
        ],
    )


def test_native_fast_path_accepts_plain_native_pdf(tmp_path: Path) -> None:
    path = tmp_path / "plain.pdf"
    _write_text_pdf(path, _plain_text())

    decision = NativeFastPathEvaluator(min_chars_per_page=80).evaluate(path)

    assert decision.accepted is True
    assert decision.reasons == ()
    assert decision.extraction is not None
    assert decision.extraction.extractor_used == "pymupdf_fast_path"
    assert decision.metrics["counters"]["char_box_coverage"] >= 0.98
    assert decision.metrics["counters"]["image_page_count"] == 0
    assert decision.metrics["counters"]["detected_table_page_count"] == 0


def test_native_fast_path_rejects_low_text_page(tmp_path: Path) -> None:
    path = tmp_path / "short.pdf"
    _write_text_pdf(path, "Short contract.")

    decision = NativeFastPathEvaluator(min_chars_per_page=80).evaluate(path)

    assert decision.accepted is False
    assert "low_text_page" in decision.reasons


def test_native_fast_path_rejects_signing_region_text(tmp_path: Path) -> None:
    path = tmp_path / "signing.pdf"
    _write_text_pdf(path, f"{_plain_text()}\nAuthorized representative signature")

    decision = NativeFastPathEvaluator(min_chars_per_page=80).evaluate(path)

    assert decision.accepted is False
    assert "signing_region_text" in decision.reasons


def test_native_fast_path_rejects_multi_column_layout(tmp_path: Path) -> None:
    path = tmp_path / "columns.pdf"
    _write_text_pdf(path, _plain_text(), columns=True)

    decision = NativeFastPathEvaluator(min_chars_per_page=80).evaluate(path)

    assert decision.accepted is False
    assert "multi_column_layout" in decision.reasons


def test_native_fast_path_rejects_detected_table(tmp_path: Path) -> None:
    path = tmp_path / "table.pdf"
    _write_text_pdf(path, _plain_text(), table=True)

    decision = NativeFastPathEvaluator(min_chars_per_page=80).evaluate(path)

    assert decision.accepted is False
    assert "detected_table" in decision.reasons


def test_native_fast_path_rejects_red_vector_graphics(tmp_path: Path) -> None:
    path = tmp_path / "red-vector.pdf"
    _write_text_pdf(path, _plain_text(), red_vector=True)

    decision = NativeFastPathEvaluator(min_chars_per_page=80).evaluate(path)

    assert decision.accepted is False
    assert "red_vector_graphics" in decision.reasons


def test_native_fast_path_benchmark_reports_pair_eligibility(tmp_path: Path) -> None:
    clean_original = tmp_path / "clean-original.pdf"
    clean_compare = tmp_path / "clean-compare.pdf"
    unsafe_original = tmp_path / "unsafe-original.pdf"
    unsafe_compare = tmp_path / "unsafe-compare.pdf"
    _write_text_pdf(clean_original, _plain_text())
    _write_text_pdf(clean_compare, _plain_text())
    _write_text_pdf(unsafe_original, _plain_text())
    _write_text_pdf(unsafe_compare, f"{_plain_text()}\nSignature page")

    report = evaluate_pairs(
        [
            (clean_original, clean_compare),
            (unsafe_original, unsafe_compare),
        ],
        NativeFastPathEvaluator(min_chars_per_page=80),
    )

    assert report["pair_count"] == 2
    assert report["eligible_pair_count"] == 1
    assert report["rejected_pair_count"] == 1
    assert report["eligible_pair_rate"] == 0.5
    assert report["rejection_reason_counts"]["signing_region_text"] == 1


class _FakeNativeEvaluator:
    def __init__(self, decisions: list[NativeFastPathDecision]) -> None:
        self.decisions = iter(decisions)

    def evaluate(self, _path: Path) -> NativeFastPathDecision:
        return next(self.decisions)


class _PathStructuredExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        filename = Path(path).name
        self.calls.append(filename)
        return ExtractionResult(
            document=_document(filename),
            extractor_used=self.name,
        )


def _decision(filename: str, *, accepted: bool, reason: str = "") -> NativeFastPathDecision:
    extraction = ExtractionResult(
        document=_document(filename),
        extractor_used="pymupdf_fast_path",
        performance={
            "duration_seconds": 0.01,
            "accepted": accepted,
            "reasons": [reason] if reason else [],
        },
    )
    return NativeFastPathDecision(
        accepted=accepted,
        extraction=extraction,
        reasons=(reason,) if reason else (),
        metrics=extraction.performance,
    )


def _context(tmp_path: Path, task_id: str) -> tuple[PipelineContext, LocalArtifactStore]:
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    _write_text_pdf(original_path, _plain_text())
    _write_text_pdf(compare_path, _plain_text().replace("supplier", "provider"))
    app_settings = Settings(storage_dir=tmp_path / "storage")
    task = CompareTask(
        task_id=task_id,
        original_pdf_path=str(original_path),
        compare_pdf_path=str(compare_path),
    )
    return (
        PipelineContext(
            task=task,
            original_pdf=original_path,
            compare_pdf=compare_path,
        ),
        LocalArtifactStore(app_settings),
    )


def test_extraction_stage_uses_native_results_only_when_both_sides_pass(tmp_path: Path) -> None:
    ctx, artifact_store = _context(tmp_path, "TNATIVEHIT")
    structured = _PathStructuredExtractor()
    evaluator = _FakeNativeEvaluator(
        [
            _decision("original.pdf", accepted=True),
            _decision("compare.pdf", accepted=True),
        ]
    )

    ExtractionStage(
        extractor=structured,
        parallel_extraction_enabled=False,
        native_fast_path_mode="enabled",
        native_fast_path_evaluator=evaluator,  # type: ignore[arg-type]
        artifact_store=artifact_store,
    ).execute(ctx)

    metrics = ctx.task.metrics["performance"]["extraction"]
    assert structured.calls == []
    assert ctx.task.extractor_used == "pymupdf_fast_path"
    assert metrics["mode"] == "native_fast_path"
    assert metrics["native_fast_path"]["decision"] == "accepted"
    assert metrics["counters"]["native_fast_path_hit"] == 1


def test_extraction_stage_hits_fast_path_with_real_plain_pdfs(tmp_path: Path) -> None:
    ctx, artifact_store = _context(tmp_path, "TNATIVEREAL")
    structured = _PathStructuredExtractor()

    ExtractionStage(
        extractor=structured,
        parallel_extraction_enabled=False,
        native_fast_path_mode="enabled",
        native_fast_path_evaluator=NativeFastPathEvaluator(min_chars_per_page=80),
        artifact_store=artifact_store,
    ).execute(ctx)

    metrics = ctx.task.metrics["performance"]["extraction"]
    assert structured.calls == []
    assert metrics["mode"] == "native_fast_path"
    assert metrics["native_fast_path"]["decision"] == "accepted"
    assert ctx.original_extraction is not None
    assert ctx.original_extraction.document.pages[0].blocks
    assert ctx.compare_extraction is not None
    assert ctx.compare_extraction.document.pages[0].blocks


def test_extraction_stage_rejects_pair_and_runs_structured_ocr_for_both_sides(
    tmp_path: Path,
) -> None:
    ctx, artifact_store = _context(tmp_path, "TNATIVEMISS")
    structured = _PathStructuredExtractor()
    evaluator = _FakeNativeEvaluator(
        [
            _decision("original.pdf", accepted=True),
            _decision("compare.pdf", accepted=False, reason="detected_table"),
        ]
    )

    ExtractionStage(
        extractor=structured,
        parallel_extraction_enabled=False,
        native_fast_path_mode="enabled",
        native_fast_path_evaluator=evaluator,  # type: ignore[arg-type]
        artifact_store=artifact_store,
    ).execute(ctx)

    metrics = ctx.task.metrics["performance"]["extraction"]
    assert structured.calls == ["original.pdf", "compare.pdf"]
    assert ctx.task.extractor_used == "ppstructure_ocr_hybrid"
    assert metrics["mode"] == "serial"
    assert metrics["native_fast_path"]["decision"] == "rejected"
    assert metrics["native_fast_path"]["sides"]["compare"]["reasons"] == ["detected_table"]
    assert metrics["counters"]["native_fast_path_hit"] == 0


def test_extraction_stage_shadow_mode_never_changes_extractor(tmp_path: Path) -> None:
    ctx, artifact_store = _context(tmp_path, "TNATIVESHADOW")
    structured = _PathStructuredExtractor()
    evaluator = _FakeNativeEvaluator(
        [
            _decision("original.pdf", accepted=True),
            _decision("compare.pdf", accepted=True),
        ]
    )

    ExtractionStage(
        extractor=structured,
        parallel_extraction_enabled=False,
        native_fast_path_mode="shadow",
        native_fast_path_evaluator=evaluator,  # type: ignore[arg-type]
        artifact_store=artifact_store,
    ).execute(ctx)

    metrics = ctx.task.metrics["performance"]["extraction"]
    assert structured.calls == ["original.pdf", "compare.pdf"]
    assert ctx.task.extractor_used == "ppstructure_ocr_hybrid"
    assert metrics["native_fast_path"]["decision"] == "shadow_eligible"
    assert metrics["counters"]["native_fast_path_hit"] == 0
