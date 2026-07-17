from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings
from app.errors import TaskTransitionConflict
from app.infrastructure.artifact_store import ArtifactStore, LocalArtifactStore
from app.infrastructure.execution_state import CancellationToken, ExecutionStateCoordinator, TaskExecutionContext
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.infrastructure.task_runner import LocalJsonTaskJobRepository, TaskJob
from app.models import BBox, Clause, ClausePair, CompareTask, Document, Page, TextBlock
from app.services.compare_debug import CompareDebugWriter
from app.services.compare_service import CompareService
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.pymupdf import PyMuPDFExtractor
from app.services.pipeline_stages import ExtractionStage
from app.services.progress_bus import ProgressBus
from app.services.report_generator import build_report_filename


def make_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    y = height - 72
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def configure_storage(tmp_path: Path) -> None:
    settings.storage_dir = tmp_path / "storage"
    settings.uploads_dir = settings.storage_dir / "uploads"
    settings.tasks_dir = settings.storage_dir / "tasks"
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.debug_dir = settings.storage_dir / "debug"
    settings.document_extractor = "auto"
    settings.compare_document_extractor = "ppstructure_ocr_hybrid"
    settings.compare_require_structured_ocr = True
    settings.align_structured_extraction = True
    settings.ensure_storage()


def test_match_matrix_summary_counts_alignment_risks(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    artifact_store: ArtifactStore = LocalArtifactStore(settings)
    writer = CompareDebugWriter(artifact_store)
    pairs = [
        ClausePair(
            original=Clause(clause_id="O001", text="付款1000元", normalized_text="付款1000元"),
            compare=Clause(clause_id="N001", text="付款5000元", normalized_text="付款5000元"),
            match_method="body",
            match_confidence="LOW",
            score_details={"alignment": {"risk_flags": ["CRITICAL_TOKEN_MISMATCH", "POSSIBLE_CLAUSE_MISALIGNMENT"]}},
        ),
        ClausePair(
            original=Clause(clause_id="O002", text="交付", normalized_text="交付"),
            compare=Clause(clause_id="N002", text="交付", normalized_text="交付"),
            match_method="body",
            match_confidence="NORMAL",
            score_details={"alignment": {"risk_flags": []}},
        ),
    ]

    path = Path(writer.write_match_matrix_summary("task-1", pairs))
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["low_confidence_alignment_count"] == 1
    assert payload["alignment_risk_flag_counts"] == {
        "CRITICAL_TOKEN_MISMATCH": 1,
        "POSSIBLE_CLAUSE_MISALIGNMENT": 1,
    }


def test_compare_service_generates_artifacts(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(
        original,
        [
            "1. Payment",
            "Buyer shall pay within 30 days.",
            "2. Delivery",
            "Seller shall deliver goods on June 1.",
        ],
    )
    make_pdf(
        compare,
        [
            "1. Payment",
            "Buyer shall pay within 45 days.",
            "2. Delivery",
            "Seller shall deliver goods on June 1.",
            "3. Invoice",
            "Seller shall provide invoice.",
        ],
    )

    service = CompareService(extractor=LocalStructuredExtractor())
    task = service.compare(original, compare, task_id="TTEST000001")

    assert task.status == "PROCESSING"
    assert task.progress_percent < 100
    assert task.extractor_used == "ppstructure_ocr_hybrid"
    assert task.document_profiles["original"].recommended_strategy == "text"
    assert task.document_profiles["compare"].total_text_chars > 0
    assert Path(task.debug_artifact_paths["document_profiles"]).exists()
    assert Path(task.debug_artifact_paths["clause_matches"]).exists()
    assert Path(task.debug_artifact_paths["diff_decisions"]).exists()
    assert task.diff_count >= 1
    assert any(
        evidence.method == "char_exact"
        for diff in task.diffs
        for evidence in [*diff.original_evidence, *diff.compare_evidence]
    )
    assert task.original_highlight_pdf_path is None
    assert task.compare_highlight_pdf_path is None
    assert task.report_pdf_path is None
    assert (settings.tasks_dir / "TTEST000001" / "task.json").exists()

    task = service.ensure_report(task)

    assert Path(task.report_pdf_path).exists()
    assert build_report_filename(task).endswith("差异分析报告.pdf")
    with fitz.open(task.report_pdf_path) as report_pdf:
        report_text = "\n".join(page.get_text() for page in report_pdf)
    assert "差异分析报告" in report_text
    assert "审计统计" in report_text
    assert "修改" in report_text


def test_compare_service_progress_callback_persists_monotonic_progress(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    repository = LocalJsonTaskRepository(settings)
    repository.save_compare_task(
        CompareService(repository=repository)._load_or_create_task(
            task_id="TPROGRESS_CALLBACK",
            original_pdf=tmp_path / "original.pdf",
            compare_pdf=tmp_path / "compare.pdf",
            original_filename="original.pdf",
            compare_filename="compare.pdf",
            compare_options=None,
        )
    )
    service = CompareService(repository=repository)
    callback = service._make_progress_callback("TPROGRESS_CALLBACK")

    callback(42, "条款匹配中", {"sub_stage": "match_done"})
    task = repository.load_compare_task("TPROGRESS_CALLBACK")

    assert task.stage == "条款匹配中"
    assert task.progress_percent == 42

    callback(30, "文档解析中", {"sub_stage": "late_old_event"})
    task = repository.load_compare_task("TPROGRESS_CALLBACK")

    assert task.progress_percent == 42


def test_compare_service_progress_callback_clamps_processing_progress_below_complete(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    repository = LocalJsonTaskRepository(settings)
    task = CompareService(repository=repository)._load_or_create_task(
        task_id="TPROGRESS_CLAMP",
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
        compare_options=None,
    )
    repository.save_compare_task(task)

    CompareService(repository=repository)._make_progress_callback("TPROGRESS_CLAMP")(100, "汇总统计中", None)

    assert repository.load_compare_task("TPROGRESS_CLAMP").progress_percent == 99


def test_compare_service_does_not_reset_terminal_task_to_processing(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    repository = LocalJsonTaskRepository(settings)
    task = CompareTask(task_id="TTERMINAL_ENTRY", status="COMPLETED", stage="已完成", progress_percent=100)
    repository.save_compare_task(task)
    before = repository.load_compare_task(task.task_id)
    service = CompareService(repository=repository)

    with pytest.raises(TaskTransitionConflict):
        service._load_or_create_task(
            task_id=task.task_id,
            original_pdf=tmp_path / "original.pdf",
            compare_pdf=tmp_path / "compare.pdf",
            original_filename="original.pdf",
            compare_filename="compare.pdf",
            compare_options=None,
        )

    assert repository.load_compare_task(task.task_id) == before


def test_compare_service_execution_progress_delegates_to_coordinator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_storage(tmp_path)
    repository = LocalJsonTaskRepository(settings)
    job_repository = LocalJsonTaskJobRepository(settings)
    events: list[object] = []
    monkeypatch.setattr(ProgressBus.get_instance(), "publish", events.append)
    coordinator = ExecutionStateCoordinator(
        job_repository,
        task_repository=repository,
        progress_publisher=ProgressBus.get_instance(),
    )
    job = coordinator.enqueue(
        TaskJob(job_id="compare:TSERVICE_PROGRESS:1", task_id="TSERVICE_PROGRESS", task_type="compare")
    )
    repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    execution_context = TaskExecutionContext(
        job_id=job.job_id,
        task_id=job.task_id,
        worker_id="worker-1",
        cancellation_token=CancellationToken(job.job_id, "worker-1", coordinator),
    )
    commit_progress = coordinator.commit_progress
    calls = 0

    def record_progress(*args: object, **kwargs: object) -> CompareTask:
        nonlocal calls
        calls += 1
        return commit_progress(*args, **kwargs)

    monkeypatch.setattr(coordinator, "commit_progress", record_progress)

    CompareService(repository=repository)._make_progress_callback(job.task_id, execution_context)(
        42,
        "条款匹配中",
        {"matched": 3},
    )

    task = repository.load_compare_task(job.task_id)
    assert calls == 1
    assert (task.stage, task.progress_percent) == ("条款匹配中", 42)
    assert events[-1].revision == task.revision


def test_compare_service_aligns_pymupdf_side_to_structured_extraction(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    structured_extractor = FakeStructuredExtractor()
    stage = ExtractionStage(
        structured_extractor=structured_extractor,
    )

    original_result = ExtractionResult(
        document=make_document("original table", "table"),
        extractor_used="auto_ppstructure_ocr_hybrid",
        raw_result_path="/tmp/original_raw.json",
    )
    compare_result = ExtractionResult(
        document=make_document("compare text", "text"),
        extractor_used="pymupdf",
    )

    original_aligned, compare_aligned = stage._align_structured_extractions(
        tmp_path / "original.pdf",
        tmp_path / "compare.pdf",
        "TALIGN000001",
        original_result,
        compare_result,
    )

    assert original_aligned is original_result
    assert compare_aligned.extractor_used == "ppstructure_ocr_hybrid"
    assert compare_aligned.document.pages[0].blocks[0].block_type == "table"
    assert compare_aligned.raw_result_path == "/tmp/structured_raw.json"
    assert structured_extractor.calls == [(tmp_path / "compare.pdf", "TALIGN000001")]
    assert "compare 已从 PyMuPDF 切换为结构化 OCR 抽取" in compare_aligned.warnings[0]


def test_compare_service_keeps_pymupdf_when_structured_alignment_fails(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    stage = ExtractionStage(
        structured_extractor=FailingStructuredExtractor(),
    )

    original_result = ExtractionResult(
        document=make_document("original table", "table"),
        extractor_used="auto_ppstructure_ocr_hybrid",
    )
    compare_result = ExtractionResult(
        document=make_document("compare text", "text"),
        extractor_used="pymupdf",
    )

    _, compare_aligned = stage._align_structured_extractions(
        tmp_path / "original.pdf",
        tmp_path / "compare.pdf",
        "TALIGN000002",
        original_result,
        compare_result,
    )

    assert compare_aligned is compare_result
    assert compare_aligned.extractor_used == "pymupdf"
    assert "compare 尝试切换结构化 OCR 抽取失败，已保留 PyMuPDF 结果" in compare_aligned.warnings[0]


def test_compare_service_leaves_terminal_failure_to_execution_coordinator(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(original, ["Original"])
    make_pdf(compare, ["Compare"])
    repository = LocalJsonTaskRepository(settings)
    service = CompareService(extractor=FailingStructuredExtractor(), repository=repository)

    with pytest.raises(DocumentExtractionError, match="原版文件结构化 OCR 失败"):
        service.compare(original, compare, task_id="TSTRICT_FAIL")

    task = repository.load_compare_task("TSTRICT_FAIL")
    assert task.status == "PROCESSING"
    assert task.errors == []


def test_extraction_stage_rejects_ocr_only_result_in_strict_mode(tmp_path: Path) -> None:
    stage = ExtractionStage(extractor=OCRonlyExtractor(), require_structured_ocr=True)

    with pytest.raises(DocumentExtractionError, match="非结构化结果 ppstructure_ocr_hybrid_ocr_only"):
        stage._extract_side(tmp_path / "original.pdf", "TSTRICT_RESULT", "原版文件")


def make_document(text: str, block_type: str) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text=text,
                        bbox=BBox(x0=10, y0=10, x1=100, y1=30),
                        block_type=block_type,
                    )
                ],
            )
        ],
    )


class FakeStructuredExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str | None]] = []

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        self.calls.append((Path(path), task_id))
        return ExtractionResult(
            document=make_document("structured table", "table"),
            extractor_used="ppstructure_ocr_hybrid",
            raw_result_path="/tmp/structured_raw.json",
        )


class FailingStructuredExtractor:
    name = "ppstructure_ocr_hybrid"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        raise DocumentExtractionError("remote OCR unavailable")


class LocalStructuredExtractor:
    name = "ppstructure_ocr_hybrid"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        result = PyMuPDFExtractor().extract(path, task_id=task_id)
        result.extractor_used = self.name
        return result


class OCRonlyExtractor:
    name = "ppstructure_ocr_hybrid"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        return ExtractionResult(
            document=make_document("ocr only", "ocr_line"),
            extractor_used="ppstructure_ocr_hybrid_ocr_only",
        )
