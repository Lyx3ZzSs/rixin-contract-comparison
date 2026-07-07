import json
from pathlib import Path

from app.models import (
    BBox,
    CompareOptions,
    CompareTask,
    DiffItem,
    Document,
    DocumentProfile,
    EvidenceBox,
    OcrRemediationAction,
    Page,
    PageOcrQualityProfile,
    PageProfile,
    TaskOcrQualitySummary,
    TaskOcrRemediationSummary,
    TextBlock,
)
from app.services.extractors.base import ExtractionResult
from app.services.pipeline import PipelineContext
from app.services.pipeline_stages import ClauseDiffStage, PreClauseDiffStage, SigningRegionStage, SplitStage, SummaryStage
from app.services.signing_region.block_detector import SigningBlockDetectionResult
from app.services.signing_region.models import VisualDetection, VisualDetectionResult


class _TestArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def debug_json_path(self, task_id: str, filename: str) -> Path:
        return self.root / task_id / "debug" / filename

    def write_json(self, path: Path, payload) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _doc(seal_text: str) -> Document:
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(
            page_no=1,
            width=595,
            height=842,
            blocks=[
                TextBlock(block_id="label", page_no=1, text="甲方（盖章）：", bbox=BBox(x0=60, y0=650, x1=170, y1=675)),
                TextBlock(block_id="seal", page_no=1, text=seal_text, bbox=BBox(x0=80, y0=680, x1=190, y1=780), block_type="seal"),
            ],
        )],
    )


def _ctx(tmp_path: Path, options: CompareOptions | None = None) -> PipelineContext:
    return PipelineContext(
        task=CompareTask(task_id="task-signing", compare_options=options or CompareOptions()),
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )


class _NoCandidateDetector:
    def detect(self, _document: Document) -> SigningBlockDetectionResult:
        return SigningBlockDetectionResult()


def test_signing_region_stage_builds_diff_and_covers_seal(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    monkeypatch.setattr(PreClauseDiffStage, "_recognize_seals", staticmethod(lambda *_args: None))
    artifact_store = _TestArtifactStore(tmp_path / "artifacts")

    PreClauseDiffStage(artifact_store=artifact_store).execute(ctx)
    stage = SigningRegionStage(artifact_store=artifact_store, visual_enabled=False)
    stage.block_detector = _NoCandidateDetector()
    stage.execute(ctx)

    assert len(ctx.signing_region_diffs) == 1
    assert ctx.signing_region_diffs[0].source_type == "signing_region"
    expected_index = (
        len(ctx.header_footer_diffs)
        + len(ctx.metadata_diffs)
        + len(ctx.table_diffs)
        + len(ctx.seal_diffs)
        + 1
    )
    assert ctx.signing_region_diffs[0].diff_id == f"D{expected_index:03d}"
    assert ctx.seal_diffs[0].diff_id in ctx.signing_region_covered_diff_ids
    assert ctx.seal_diffs[0].diff_id in ctx.signing_region_debug["coverage"]["covered_diff_ids"]
    assert ctx.signing_region_debug["legacy_region_fallback"] == {"original": True, "compare": True}
    assert ctx.task.debug_artifact_paths["signing_region"].endswith("signing_region.json")


def test_signing_region_stage_skips_when_stamps_are_ignored(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, CompareOptions(ignore_stamps=True))
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    ctx.signing_pages_original = ["stale-page"]
    ctx.signing_pages_compare = ["stale-page"]
    ctx.signing_blocks_original = ["stale-block"]
    ctx.signing_blocks_compare = ["stale-block"]
    ctx.clause_document_original = _doc("stale")
    ctx.clause_document_compare = _doc("stale")

    SigningRegionStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"), visual_enabled=False).execute(ctx)

    assert ctx.signing_pages_original == []
    assert ctx.signing_pages_compare == []
    assert ctx.signing_blocks_original == []
    assert ctx.signing_blocks_compare == []
    assert ctx.clause_document_original is None
    assert ctx.clause_document_compare is None
    assert ctx.signing_regions_original == []
    assert ctx.signing_regions_compare == []
    assert ctx.signing_region_diffs == []
    assert ctx.signing_region_covered_diff_ids == set()
    assert ctx.signing_region_debug == {"skipped": True}


def test_signing_region_stage_skips_when_mode_is_off(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, CompareOptions(signing_region_mode="off"))
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")

    SigningRegionStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"), visual_enabled=False).execute(ctx)

    assert ctx.signing_regions_original == []
    assert ctx.signing_regions_compare == []
    assert ctx.signing_region_diffs == []
    assert ctx.signing_region_covered_diff_ids == set()
    assert ctx.signing_region_debug == {"skipped": True}


def test_signing_stage_sets_clause_documents_without_signing_blocks(tmp_path: Path) -> None:
    original = Document(
        filename="o.pdf",
        path="o.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=10,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="body",
                        page_no=10,
                        text="14.2 正文条款",
                        bbox=BBox(x0=80, y0=550, x1=520, y1=590),
                    ),
                    TextBlock(
                        block_id="sign",
                        page_no=10,
                        text="甲方：A 乙方：B\n(盖章)\n(签字)\n日期：",
                        bbox=BBox(x0=65, y0=620, x1=485, y1=730),
                    ),
                ],
            )
        ],
    )
    compare = original.model_copy(deep=True)
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

    SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_enabled=False,
    ).execute(ctx)

    assert ctx.clause_document_original is not None
    assert [block.block_id for block in ctx.clause_document_original.pages[0].blocks] == ["body"]
    assert ctx.signing_region_debug["clause_exclusion"]["original"][0]["block_id"] == "sign"


def test_signing_pipeline_excludes_cover_table_and_strips_real_signing_blocks(tmp_path: Path) -> None:
    cover_table = TextBlock(
        block_id="cover_table",
        page_no=1,
        text="甲方\n江苏东大金智信息系统有限公司\n乙方\n国能日新科技股份有限公司\n北京\n签订地点\n签订日期\n2026年4月21日",
        bbox=BBox(x0=90, y0=560, x1=505, y1=690),
        block_type="table",
    )
    original_signing = TextBlock(
        block_id="o_signing",
        page_no=10,
        text="甲方：江苏东大金智信息系统有限公司 乙方：国能日新科技股份有限公司\n(盖章)\n法人代表或授权委托人：\n(签字)\n日期：",
        bbox=BBox(x0=65, y0=620, x1=485, y1=730),
    )
    compare_signing = TextBlock(
        block_id="c_signing",
        page_no=11,
        text="甲方：江苏东达金智信息系统有限公司 乙方：国能日新科技股份有限公司\n(盖章)\n法人代表或\n日期：2026.",
        bbox=BBox(x0=60, y0=60, x1=500, y1=180),
    )
    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=10,
        pages=[
            Page(page_no=1, width=595, height=842, blocks=[cover_table]),
            Page(
                page_no=10,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="o_body",
                        page_no=10,
                        text="14.2 正文条款",
                        bbox=BBox(x0=80, y0=550, x1=520, y1=590),
                    ),
                    original_signing,
                ],
            ),
        ],
        profile=DocumentProfile(
            filename="original.pdf",
            page_count=10,
            page_profiles=[
                PageProfile(page_no=1, width=595, height=842, page_role="cover"),
                PageProfile(page_no=10, width=595, height=842, page_role="body"),
            ],
        ),
    )
    compare = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=11,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[cover_table.model_copy(update={"block_id": "compare_cover_table"})],
            ),
            Page(page_no=11, width=595, height=842, blocks=[compare_signing]),
        ],
        profile=DocumentProfile(
            filename="compare.pdf",
            page_count=11,
            page_profiles=[
                PageProfile(page_no=1, width=595, height=842, page_role="cover"),
                PageProfile(page_no=11, width=595, height=842, page_role="body"),
            ],
        ),
    )
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

    SigningRegionStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"), visual_enabled=False).execute(ctx)

    def _page(document: Document, page_no: int) -> Page:
        return next(page for page in document.pages if page.page_no == page_no)

    assert ctx.clause_document_original is not None
    assert ctx.clause_document_compare is not None
    assert all(region.page_no != 1 for region in ctx.signing_regions_original)
    assert all(region.page_no != 1 for region in ctx.signing_regions_compare)
    assert {block.page_no for block in ctx.signing_blocks_original} == {10}
    assert {block.page_no for block in ctx.signing_blocks_compare} == {11}
    assert "cover_signing_info_table" in {
        item["reason"] for item in ctx.signing_region_debug["excluded_candidates"]["original"]
    }
    assert [block.block_id for block in _page(ctx.clause_document_original, 10).blocks] == ["o_body"]
    assert [block.block_id for block in _page(ctx.clause_document_compare, 11).blocks] == []


def test_split_stage_uses_clause_documents_when_available(tmp_path: Path) -> None:
    class _Splitter:
        def __init__(self) -> None:
            self.seen_filenames: list[str] = []

        def split(self, document: Document, prefix: str):
            self.seen_filenames.append(document.filename)
            return []

    ctx = _ctx(tmp_path)
    original = Document(filename="original-full.pdf", path="o.pdf", page_count=1, pages=[])
    compare = Document(filename="compare-full.pdf", path="c.pdf", page_count=1, pages=[])
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")
    ctx.clause_document_original = Document(filename="original-clause.pdf", path="o.pdf", page_count=1, pages=[])
    ctx.clause_document_compare = Document(filename="compare-clause.pdf", path="c.pdf", page_count=1, pages=[])
    splitter = _Splitter()
    stage = SplitStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"))
    stage.splitter = splitter

    stage.execute(ctx)

    assert splitter.seen_filenames == ["original-clause.pdf", "compare-clause.pdf"]


def test_signing_stage_does_not_fallback_when_detector_has_rejected_candidates(tmp_path: Path) -> None:
    class _Detector:
        def detect(self, document: Document) -> SigningBlockDetectionResult:
            if document.filename == "original.pdf":
                return SigningBlockDetectionResult(
                    low_confidence_candidates=[
                        {
                            "page_no": 1,
                            "block_ids": ["label"],
                            "score": 0.42,
                            "reasons": ["below_threshold"],
                            "text": "甲方（盖章）：",
                        }
                    ]
                )
            return SigningBlockDetectionResult(
                excluded_candidates=[
                    {
                        "page_no": 1,
                        "block_ids": ["label"],
                        "reason": "cover_signing_info_table",
                        "text": "甲方（盖章）：",
                    }
                ]
            )

    class _Extractor:
        def extract_from_blocks(self, blocks) -> list:
            assert blocks == []
            return []

        def extract(self, _document: Document) -> list:
            raise AssertionError("legacy fallback should not run for detector candidates")

    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    ctx.original_extraction.document.filename = "original.pdf"
    ctx.compare_extraction.document.filename = "compare.pdf"
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_enabled=False,
    )
    stage.block_detector = _Detector()
    stage.extractor = _Extractor()

    stage.execute(ctx)

    assert ctx.signing_regions_original == []
    assert ctx.signing_regions_compare == []
    assert ctx.signing_region_debug["legacy_region_fallback"] == {"original": False, "compare": False}
    assert ctx.signing_region_debug["low_confidence_candidates"]["original"][0]["block_ids"] == ["label"]
    assert ctx.signing_region_debug["excluded_candidates"]["compare"][0]["reason"] == "cover_signing_info_table"


def test_signing_region_stage_builds_visual_diff_from_detector_and_fingerprint(tmp_path: Path) -> None:
    class _Detector:
        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            if not regions:
                return VisualDetectionResult(available=True, model_name="fake")
            region = regions[0]
            return VisualDetectionResult(
                available=True,
                model_name="fake",
                detections=[
                    VisualDetection(
                        page_no=region.page_no,
                        bbox=region.bbox,
                        label="signature",
                        confidence=0.91,
                        model_name="fake",
                    )
                ],
            )

    class _Fingerprinter:
        def fingerprint_region(self, pdf_path: Path, region) -> dict[str, str | float]:
            if pdf_path.name == "original.pdf":
                return {"status": "ok", "hash": "visual-original", "score": 1.0}
            return {"status": "ok", "hash": "visual-compare", "score": 1.0}

    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("合同专用章"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("合同专用章"), extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_Detector(),
        visual_fingerprinter=_Fingerprinter(),
        visual_enabled=True,
    )
    stage.block_detector = _NoCandidateDetector()

    stage.execute(ctx)

    assert len(ctx.signing_region_diffs) == 1
    diff = ctx.signing_region_diffs[0]
    assert diff.source_type == "signing_region"
    assert "SIGNING_VISUAL_CHANGE" in diff.review_flags
    assert "visual-original" in ctx.signing_region_debug["original_regions"][0]["elements"][-1]["visual_hash"]
    assert ctx.signing_region_debug["visual_adapter_status"]["original"]["available"] is True
    assert ctx.signing_region_debug["configuration"]["visual_enabled"] is True


def test_signing_region_stage_records_unavailable_visual_adapter_without_failing(tmp_path: Path) -> None:
    class _Detector:
        def detect(self, _pdf_path: Path, _regions, _task_id: str) -> VisualDetectionResult:
            return VisualDetectionResult(available=False, error="remote_call_failed")

    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_Detector(),
        visual_enabled=True,
    )
    stage.block_detector = _NoCandidateDetector()

    stage.execute(ctx)

    assert len(ctx.signing_region_diffs) == 1
    assert ctx.signing_region_debug["visual_adapter_status"]["original"]["available"] is False
    assert ctx.signing_region_debug["visual_adapter_status"]["original"]["error"] == "remote_call_failed"
    assert ctx.signing_region_debug["configuration"]["visual_enabled"] is True


def test_signing_region_stage_ignores_visual_only_change_when_one_side_adapter_fails(tmp_path: Path) -> None:
    class _Detector:
        def detect(self, pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            if pdf_path.name == "compare.pdf":
                return VisualDetectionResult(available=False, error="remote_call_failed")
            if not regions:
                return VisualDetectionResult(available=True, model_name="fake")
            region = regions[0]
            return VisualDetectionResult(
                available=True,
                model_name="fake",
                detections=[
                    VisualDetection(
                        page_no=region.page_no,
                        bbox=region.bbox,
                        label="signature",
                        confidence=0.91,
                        model_name="fake",
                    )
                ],
            )

    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("合同专用章"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("合同专用章"), extractor_used="test")

    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_Detector(),
        visual_enabled=True,
    )
    stage.block_detector = _NoCandidateDetector()
    stage.execute(ctx)

    assert ctx.signing_region_diffs == []
    assert ctx.signing_region_debug["visual_adapter_status"]["compare"]["available"] is False


def test_signing_region_stage_suppresses_low_confidence_visual_detections(tmp_path: Path) -> None:
    class _Detector:
        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            if not regions:
                return VisualDetectionResult(available=True, model_name="fake")
            region = regions[0]
            return VisualDetectionResult(
                available=True,
                model_name="fake",
                detections=[
                    VisualDetection(
                        page_no=region.page_no,
                        bbox=region.bbox,
                        label="signature",
                        confidence=0.2,
                        model_name="fake",
                    )
                ],
            )

    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("合同专用章"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("合同专用章"), extractor_used="test")

    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_Detector(),
        visual_enabled=True,
    )
    stage.block_detector = _NoCandidateDetector()
    stage.execute(ctx)

    assert ctx.signing_region_diffs == []
    assert len(ctx.signing_region_debug["suppressed_low_confidence_candidates"]) == 2
    assert ctx.signing_region_debug["suppressed_low_confidence_candidates"][0]["reason"] == "low_visual_confidence"


def test_clause_diff_stage_counts_signing_region_diffs_before_clause_diffs(tmp_path: Path) -> None:
    class _FakeDiffEngine:
        def __init__(self) -> None:
            self.start_index = 0

        def build_diffs(self, _pairs, start_index: int) -> list[DiffItem]:
            self.start_index = start_index
            return [
                DiffItem(
                    diff_id=f"D{start_index:03d}",
                    diff_type="MODIFY",
                    source_type="clause",
                    title="正文条款",
                    original_text="原条款",
                    compare_text="新条款",
                )
            ]

    ctx = _ctx(tmp_path)
    ctx.seal_diffs = [
        DiffItem(diff_id="D001", diff_type="MODIFY", source_type="seal", title="印章", original_text="A", compare_text="B")
    ]
    ctx.signing_region_diffs = [
        DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="signing_region",
            title="签章区",
            original_text="甲方：A",
            compare_text="甲方：B",
        )
    ]
    fake_engine = _FakeDiffEngine()
    stage = ClauseDiffStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"))
    stage.diff_engine = fake_engine

    stage.execute(ctx)

    assert fake_engine.start_index == 3
    assert [diff.source_type for diff in ctx.diffs] == ["seal", "signing_region", "clause"]
    assert ctx.diffs[-1].diff_id == "D003"


def test_summary_stage_hides_legacy_diff_covered_by_signing_region(tmp_path: Path) -> None:
    signing_diff = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        source_type="signing_region",
        title="签章区（第1页）",
        original_text="甲方（盖章）：A公司",
        compare_text="甲方（盖章）：B公司",
    )
    covered_seal_diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="seal",
        title="印章区域（第1页）",
        original_text="A公司",
        compare_text="B公司",
        original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=680, x1=190, y1=780), method="seal_region")],
    )
    ctx = _ctx(tmp_path)
    ctx.diffs = [covered_seal_diff, signing_diff]
    ctx.signing_region_covered_diff_ids = {"D001"}

    SummaryStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts")).execute(ctx)

    assert [diff.diff_id for diff in ctx.task.diffs] == ["D002"]
    assert ctx.task.diff_count == 1


def test_summary_stage_removes_covered_legacy_diff_from_ocr_summaries(tmp_path: Path) -> None:
    signing_diff = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        source_type="signing_region",
        title="签章区（第1页）",
        original_text="甲方（盖章）：A公司",
        compare_text="甲方（盖章）：B公司",
    )
    covered_seal_diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="seal",
        title="印章区域（第1页）",
        original_text="A公司",
        compare_text="B公司",
    )
    ctx = _ctx(tmp_path)
    ctx.diffs = [covered_seal_diff, signing_diff]
    ctx.signing_region_covered_diff_ids = {"D001"}
    ctx.task.ocr_quality_summary = TaskOcrQualitySummary(
        status="LOW_TEXT_CONFIDENCE",
        requires_review=True,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="original",
                page_no=1,
                status="LOW_TEXT_CONFIDENCE",
                affected_diff_ids=["D001"],
            )
        ],
    )
    ctx.task.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="ACTIONS_PLANNED",
        attempted_action_count=1,
        unresolved_action_count=1,
        actions=[
            OcrRemediationAction(
                action_id="original:1:D001:RELOCATE_EVIDENCE",
                action_type="RELOCATE_EVIDENCE",
                reason="OCR 风险",
                diff_id="D001",
                side="original",
                page_no=1,
            )
        ],
    )

    SummaryStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts")).execute(ctx)

    assert [diff.diff_id for diff in ctx.task.diffs] == ["D002"]
    assert ctx.task.ocr_quality_summary is not None
    assert ctx.task.ocr_quality_summary.affected_diff_count == 0
    assert ctx.task.ocr_quality_summary.profiles[0].affected_diff_ids == []
    assert ctx.task.ocr_remediation_summary is not None
    assert ctx.task.ocr_remediation_summary.actions == []
    assert ctx.task.ocr_remediation_summary.attempted_action_count == 0
    assert ctx.task.ocr_remediation_summary.unresolved_action_count == 0
