import json
from pathlib import Path

import pytest

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
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    VisualDetection,
    VisualDetectionResult,
)


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


def test_signing_region_stage_uses_opencv_detector_by_default(monkeypatch, tmp_path: Path) -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    monkeypatch.setattr("app.config.settings.signing_visual_backend", "opencv")
    monkeypatch.setattr("app.config.settings.signing_visual_enabled", True)

    stage = SigningRegionStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"))

    assert isinstance(stage.visual_detector, OpenCvVisualSignatureDetector)
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    stage.block_detector = _NoCandidateDetector()
    stage.execute(ctx)

    configuration = ctx.signing_region_debug["configuration"]
    assert configuration["visual_backend"] == "opencv"
    assert configuration["visual_detector"] == "OpenCvVisualSignatureDetector"
    assert "opencv_available" in configuration


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


def test_signing_stage_expands_detected_block_to_nearby_signature_fragments(tmp_path: Path) -> None:
    class _Detector:
        def detect(self, _document: Document) -> SigningBlockDetectionResult:
            return SigningBlockDetectionResult(
                blocks=[
                    SigningBlock(
                        block_id="SB-29-1",
                        page_no=29,
                        bbox=BBox(x0=93.5, y0=89.5, x1=349.0, y1=428.5),
                        block_role=SigningBlockRole.PARTY_A,
                        confidence=0.6,
                        confidence_level=SigningBlockConfidenceLevel.MEDIUM,
                        confidence_reasons=["party_label", "seal_signature_date_cluster"],
                        source_block_ids=["party_a", "rep_a", "time_a", "time_b"],
                        text="甲方（盖章\n法定代表人（负责人）/授权代表（签字）\n时间：2026年5月8日\n时间：2026年5月8日",
                    )
                ]
            )

    page = Page(
        page_no=29,
        width=595,
        height=842,
        blocks=[
            TextBlock(
                block_id="party_a",
                page_no=29,
                text="甲方（盖章",
                bbox=BBox(x0=112, y0=137, x1=191, y1=160),
                block_type="seal",
            ),
            TextBlock(
                block_id="rep_a",
                page_no=29,
                text="法定代表人（负责人）/授权代表（签字）",
                bbox=BBox(x0=110, y0=173, x1=370, y1=199),
            ),
            TextBlock(
                block_id="seal_code",
                page_no=29,
                text="42130130002406",
                bbox=BBox(x0=159, y0=191, x1=224, y1=214),
                block_type="seal",
                flow_role="non_text",
            ),
            TextBlock(
                block_id="signature_value",
                page_no=29,
                text="17高",
                bbox=BBox(x0=352, y0=179, x1=410, y1=236),
            ),
            TextBlock(
                block_id="aside_noise",
                page_no=29,
                text="运合",
                bbox=BBox(x0=330, y0=95, x1=346, y1=150),
                block_type="aside_text",
                flow_role="aside",
            ),
            TextBlock(
                block_id="time_a",
                page_no=29,
                text="时间：2026年5月8日",
                bbox=BBox(x0=108, y0=209, x1=336, y1=239),
            ),
            TextBlock(
                block_id="lower_seal",
                page_no=29,
                text="图",
                bbox=BBox(x0=260, y0=333, x1=278, y1=350),
                block_type="seal",
                flow_role="non_text",
            ),
            TextBlock(
                block_id="time_b",
                page_no=29,
                text="时间：2026年5月8日",
                bbox=BBox(x0=108, y0=383, x1=337, y1=416),
            ),
            TextBlock(
                block_id="edge_fragment",
                page_no=29,
                text="司",
                bbox=BBox(x0=554, y0=576, x1=591, y1=651),
                block_type="seal",
                flow_role="non_text",
            ),
            TextBlock(block_id="footer", page_no=29, text="-28-", bbox=BBox(x0=282, y0=781, x1=320, y1=796)),
        ],
    )
    doc = Document(filename="compare.pdf", path="compare.pdf", page_count=29, pages=[page])
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=doc.model_copy(deep=True), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=doc.model_copy(deep=True), extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_enabled=False,
    )
    stage.block_detector = _Detector()

    stage.execute(ctx)

    block = ctx.signing_blocks_compare[0]
    assert {"signature_value", "seal_code", "lower_seal"}.issubset(block.source_block_ids)
    assert "aside_noise" not in block.source_block_ids
    assert "edge_fragment" not in block.source_block_ids
    assert "footer" not in block.source_block_ids
    assert block.bbox.x1 >= 410
    assert "17高" in block.text
    assert ctx.clause_document_compare is not None
    retained_ids = {item.block_id for item in ctx.clause_document_compare.pages[0].blocks}
    assert "edge_fragment" in retained_ids
    assert {"signature_value", "seal_code", "lower_seal"}.isdisjoint(retained_ids)


def test_signing_stage_merges_native_pdf_title_above_detected_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Detector:
        def detect(self, _document: Document) -> SigningBlockDetectionResult:
            return SigningBlockDetectionResult(
                blocks=[
                    SigningBlock(
                        block_id="SB-13-1",
                        page_no=13,
                        bbox=BBox(x0=46.98, y0=139.49, x1=543.37, y1=513.97),
                        block_role=SigningBlockRole.BOTH_PARTIES,
                        confidence=0.9,
                        confidence_level=SigningBlockConfidenceLevel.HIGH,
                        confidence_reasons=["signing_page_context", "paired_parties"],
                        source_block_ids=["signing_table"],
                        text="甲方（盖章）：\n乙方（盖章）：\n日期：",
                    )
                ]
            )

    native_title = TextBlock(
        block_id="native_p13_signing_title_1",
        page_no=13,
        text="签署页",
        bbox=BBox(x0=261.6, y0=71.7, x1=333.6, y1=89.7),
        block_type="paragraph_title",
        source="native_pdf_text",
        flow_role="heading",
    )
    late_body_reference = TextBlock(
        block_id="native_p13_body_reference",
        page_no=13,
        text="合同签署页中的地址",
        bbox=BBox(x0=80, y0=560, x1=260, y1=580),
        source="native_pdf_text",
    )
    monkeypatch.setattr(
        SigningRegionStage,
        "_native_signing_title_blocks",
        staticmethod(lambda _pdf_path: {13: [native_title, late_body_reference]}),
        raising=False,
    )

    page = Page(
        page_no=13,
        width=595,
        height=842,
        blocks=[
            TextBlock(
                block_id="signing_table",
                page_no=13,
                text="甲方（盖章）：\n乙方（盖章）：\n日期：",
                bbox=BBox(x0=46.98, y0=139.49, x1=543.37, y1=513.97),
            ),
        ],
    )
    doc = Document(filename="original.pdf", path="original.pdf", page_count=13, pages=[page])
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=doc.model_copy(deep=True), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=doc.model_copy(deep=True), extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_enabled=False,
    )
    stage.block_detector = _Detector()

    stage.execute(ctx)

    block = ctx.signing_blocks_original[0]
    assert block.text.startswith("签署页\n")
    assert block.bbox.y0 == pytest.approx(71.7)
    assert "native_p13_signing_title_1" in block.source_block_ids
    assert "native_p13_body_reference" not in block.source_block_ids
    assert "native_signing_title" in block.confidence_reasons


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


def test_signing_region_stage_records_visual_only_candidates_without_final_diff(tmp_path: Path) -> None:
    class _VisualOnlyDetector:
        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            detections = [
                VisualDetection(
                    page_no=1,
                    bbox=BBox(x0=80, y0=650, x1=180, y1=760),
                    label="seal",
                    confidence=0.88,
                    model_name="opencv",
                    raw_data={"reasons": ["red_seal_pixels"]},
                )
            ]
            return VisualDetectionResult(available=True, model_name="opencv", detections=detections)

    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_VisualOnlyDetector(),
        visual_enabled=True,
    )
    stage.block_detector = _NoCandidateDetector()
    stage.extractor = type(
        "_NoFallbackExtractor",
        (),
        {
            "extract_from_blocks": lambda self, blocks: [],
            "extract": lambda self, document: [],
        },
    )()

    stage.execute(ctx)

    assert ctx.signing_region_diffs == []
    assert ctx.signing_region_debug["visual_adapter_status"]["original"]["available"] is True
    assert ctx.signing_region_debug["visual_candidates"]["original"][0]["label"] == "seal"
    assert ctx.signing_region_debug["visual_candidates"]["original"][0]["used_for_promotion"] is False


def test_signing_region_stage_promotes_rule_backed_candidate_with_visual_support(tmp_path: Path) -> None:
    class _Detector:
        def detect(self, _document: Document) -> SigningBlockDetectionResult:
            return SigningBlockDetectionResult(
                low_confidence_candidates=[
                    {
                        "page_no": "2",
                        "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
                        "score": 0.42,
                        "reasons": ["signing_page_context", "business_signing_form_fields"],
                        "block_ids": ["candidate"],
                        "text": "甲方：A公司\n乙方：B公司\n盖章：\n签字：",
                    }
                ]
            )

    class _VisualDetector:
        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            assert regions
            region = regions[0]
            return VisualDetectionResult(
                available=True,
                model_name="opencv",
                detections=[
                    VisualDetection(
                        page_no=region.page_no,
                        bbox=BBox(x0=100, y0=640, x1=500, y1=740),
                        label="signature",
                        confidence=0.82,
                        model_name="opencv",
                        raw_data={"reasons": ["table_or_stroke_density"]},
                    )
                ],
            )

    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            Page(page_no=1, width=595, height=842, blocks=[]),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="candidate",
                        page_no=2,
                        text="甲方：A公司\n乙方：B公司\n盖章：\n签字：",
                        bbox=BBox(x0=80, y0=620, x1=520, y1=760),
                    )
                ],
            ),
        ],
    )
    compare = original.model_copy(deep=True)
    compare.filename = "compare.pdf"
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_VisualDetector(),
        visual_enabled=True,
    )
    stage.block_detector = _Detector()

    stage.execute(ctx)

    assert {block.page_no for block in ctx.signing_blocks_original} == {2}
    assert ctx.signing_blocks_original[0].confidence_level == "medium"
    assert "visual_candidate_promoted" in ctx.signing_blocks_original[0].confidence_reasons
    assert ctx.signing_region_debug["visual_candidates"]["original"][0]["used_for_promotion"] is True


def test_signing_region_stage_does_not_scan_or_promote_candidates_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.config.settings.signing_opencv_scan_candidate_pages", False)
    high_block = SigningBlock(
        block_id="SB-HIGH-1",
        page_no=1,
        bbox=BBox(x0=60, y0=620, x1=520, y1=760),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.72,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["seal_signature_date_cluster", "paired_parties"],
        source_block_ids=["signing"],
        text="甲方：A公司\n乙方：B公司\n盖章：\n签字：\n日期：",
        exclude_from_clause_diff=False,
    )
    low_candidate = {
        "page_no": 2,
        "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
        "score": 0.42,
        "reasons": ["signing_page_context", "business_signing_form_fields"],
        "block_ids": ["candidate"],
        "text": "甲方：A公司\n乙方：B公司\n盖章：\n签字：",
    }

    class _Detector:
        def detect(self, _document: Document) -> SigningBlockDetectionResult:
            return SigningBlockDetectionResult(
                blocks=[high_block.model_copy(deep=True)],
                low_confidence_candidates=[dict(low_candidate)],
            )

    class _VisualDetector:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            self.calls.append([region.region_id for region in regions])
            detections = [
                VisualDetection(
                    page_no=regions[0].page_no,
                    bbox=regions[0].bbox,
                    label="signature",
                    confidence=0.88,
                    model_name="opencv",
                    raw_data={"reasons": ["table_or_stroke_density"]},
                ),
                VisualDetection(
                    page_no=2,
                    bbox=BBox.model_validate(low_candidate["bbox"]),
                    label="signature",
                    confidence=0.88,
                    model_name="opencv",
                    raw_data={"reasons": ["table_or_stroke_density"]},
                ),
            ]
            return VisualDetectionResult(available=True, model_name="opencv", detections=detections)

    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="signing",
                        page_no=1,
                        text="甲方：A公司\n乙方：B公司\n盖章：\n签字：\n日期：",
                        bbox=BBox(x0=60, y0=620, x1=520, y1=760),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="candidate",
                        page_no=2,
                        text="甲方：A公司\n乙方：B公司\n盖章：\n签字：",
                        bbox=BBox(x0=80, y0=620, x1=520, y1=760),
                    )
                ],
            ),
        ],
    )
    compare = original.model_copy(deep=True)
    compare.filename = "compare.pdf"
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")
    visual_detector = _VisualDetector()
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=visual_detector,
        visual_enabled=True,
    )
    stage.visual_fingerprinter = None
    stage.block_detector = _Detector()

    stage.execute(ctx)

    assert visual_detector.calls == [["SR-1-1"], ["SR-1-1"]]
    assert [block.page_no for block in ctx.signing_blocks_original] == [1]
    assert "visual_candidate_promoted" not in ctx.signing_blocks_original[0].confidence_reasons
    assert any(
        element.source == "visual_model"
        for region in ctx.signing_regions_original
        for element in region.elements
    )


def test_signing_region_stage_caps_candidate_scan_by_unique_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.config.settings.signing_opencv_scan_candidate_pages", True)
    monkeypatch.setattr("app.config.settings.signing_opencv_max_candidate_pages", 2)
    candidates = [
        {
            "page_no": 2,
            "bbox": {"x0": 80, "y0": 620, "x1": 250, "y1": 700},
            "score": 0.42,
            "reasons": ["signing_page_context", "business_signing_form_fields"],
            "block_ids": ["candidate-2a"],
            "text": "甲方：A公司\n盖章：",
        },
        {
            "page_no": 2,
            "bbox": {"x0": 260, "y0": 620, "x1": 520, "y1": 700},
            "score": 0.43,
            "reasons": ["signing_page_context", "business_signing_form_fields"],
            "block_ids": ["candidate-2b"],
            "text": "乙方：B公司\n盖章：",
        },
        {
            "page_no": 3,
            "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
            "score": 0.44,
            "reasons": ["signing_page_context", "business_signing_form_fields"],
            "block_ids": ["candidate-3"],
            "text": "甲方：A公司\n乙方：B公司\n盖章：",
        },
        {
            "page_no": 4,
            "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
            "score": 0.45,
            "reasons": ["signing_page_context", "business_signing_form_fields"],
            "block_ids": ["candidate-4"],
            "text": "甲方：A公司\n乙方：B公司\n盖章：",
        },
    ]

    class _Detector:
        def detect(self, _document: Document) -> SigningBlockDetectionResult:
            return SigningBlockDetectionResult(
                low_confidence_candidates=[dict(candidate) for candidate in candidates]
            )

    class _VisualDetector:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            self.calls.append([region.region_id for region in regions])
            return VisualDetectionResult(
                available=True,
                model_name="opencv",
                detections=[
                    VisualDetection(
                        page_no=region.page_no,
                        bbox=region.bbox,
                        label="signature",
                        confidence=0.88,
                        model_name="opencv",
                        raw_data={"reasons": ["table_or_stroke_density"]},
                    )
                    for region in regions
                    if region.region_id.startswith("LC-")
                ],
            )

    pages = [Page(page_no=page_no, width=595, height=842, blocks=[]) for page_no in range(1, 5)]
    original = Document(filename="original.pdf", path="original.pdf", page_count=4, pages=pages)
    compare = original.model_copy(deep=True)
    compare.filename = "compare.pdf"
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")
    visual_detector = _VisualDetector()
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=visual_detector,
        visual_enabled=True,
    )
    stage.visual_fingerprinter = None
    stage.block_detector = _Detector()

    stage.execute(ctx)

    expected_region_ids = ["LC-2-1", "LC-2-2", "LC-3-3"]
    assert visual_detector.calls == [expected_region_ids, expected_region_ids]
    assert [block.page_no for block in ctx.signing_blocks_original] == [2, 2, 3]
    assert {candidate["page_no"] for candidate in ctx.signing_region_debug["visual_candidates"]["original"]} == {
        2,
        3,
    }
    assert all(
        candidate["used_for_promotion"]
        for candidate in ctx.signing_region_debug["visual_candidates"]["original"]
    )


def test_signing_region_stage_prioritizes_stronger_candidates_for_visual_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.config.settings.signing_opencv_scan_candidate_pages", True)
    monkeypatch.setattr("app.config.settings.signing_opencv_max_candidate_pages", 2)
    weak_body_candidate = SigningBlock(
        block_id="unused",
        page_no=11,
        bbox=BBox(x0=70, y0=240, x1=515, y1=508),
        block_role=SigningBlockRole.UNKNOWN,
        confidence=0.35,
        confidence_level=SigningBlockConfidenceLevel.LOW,
        confidence_reasons=["signing_page_context", "bottom_signing_position"],
        source_block_ids=["body"],
        text="通过专人递送、特快专递或传真方式送达本合同签署页中的有关地址。",
    )
    candidate_regions = SigningRegionStage._candidate_regions_from_low_confidence(
        [
            {
                "page_no": weak_body_candidate.page_no,
                "bbox": weak_body_candidate.bbox.model_dump(mode="json"),
                "score": weak_body_candidate.confidence,
                "reasons": weak_body_candidate.confidence_reasons,
                "block_ids": weak_body_candidate.source_block_ids,
                "text": weak_body_candidate.text,
            },
            {
                "page_no": 29,
                "bbox": {"x0": 96, "y0": 89, "x1": 266, "y1": 233},
                "score": 0.1,
                "reasons": ["party_label"],
                "block_ids": ["party_a"],
                "text": "甲方（盖章）",
            },
            {
                "page_no": 53,
                "bbox": {"x0": 102, "y0": 65, "x1": 510, "y1": 211},
                "score": 0.35,
                "reasons": ["paired_parties", "two_column_layout"],
                "block_ids": ["party_a", "party_b", "seal_label"],
                "text": "甲方：国能长源随州发电有限公司\n乙方：国能日新科技股份有限公司\n(盖章):",
            },
        ]
    )

    result = SigningRegionStage._candidate_regions_for_visual_scan(candidate_regions)

    assert [region.page_no for region in result] == [53, 29]


@pytest.mark.parametrize(
    ("candidate_update", "visual_candidate_update", "visual_bbox"),
    [
        ({"score": 0.34}, {}, {"x0": 100, "y0": 640, "x1": 500, "y1": 740}),
        ({"reasons": ["party_label"]}, {}, {"x0": 100, "y0": 640, "x1": 500, "y1": 740}),
        ({}, {}, {"x0": 20, "y0": 100, "x1": 70, "y1": 150}),
        ({}, {"confidence": 0.2}, {"x0": 100, "y0": 640, "x1": 500, "y1": 740}),
        ({}, {"confidence": "bad"}, {"x0": 100, "y0": 640, "x1": 500, "y1": 740}),
    ],
)
def test_signing_region_stage_does_not_promote_candidate_without_required_gates(
    candidate_update,
    visual_candidate_update,
    visual_bbox,
) -> None:
    candidate = {
        "page_no": 2,
        "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
        "score": 0.42,
        "reasons": ["signing_page_context", "business_signing_form_fields"],
        "block_ids": ["candidate"],
        "text": "甲方：A公司\n乙方：B公司\n盖章：\n签字：",
    }
    candidate.update(candidate_update)
    visual_candidate = {
        "page_no": 2,
        "bbox": visual_bbox,
        "label": "signature",
        "confidence": 0.82,
        "model_name": "opencv",
        "reasons": ["table_or_stroke_density"],
        "used_for_promotion": False,
    }
    visual_candidate.update(visual_candidate_update)
    structure = SigningBlockDetectionResult(low_confidence_candidates=[candidate])

    SigningRegionStage._promote_visual_supported_candidates(
        structure,
        {"_visual_candidates": [visual_candidate]},
    )

    assert structure.blocks == []
    assert visual_candidate["used_for_promotion"] is False


def test_signing_region_stage_does_not_promote_body_text_with_dark_stroke_visual() -> None:
    candidate = {
        "page_no": 7,
        "bbox": {"x0": 45, "y0": 600, "x1": 550, "y1": 755},
        "score": 0.4,
        "reasons": ["paired_parties", "bottom_signing_position"],
        "block_ids": ["payment_body", "acceptance_body"],
        "text": "乙方为甲方提供的标的物总额人民币594000.00元。\n"
        "乙方设备货到现场，甲方签收确认无误后20日内付款。",
    }
    visual_candidate = {
        "page_no": 7,
        "bbox": {"x0": 45, "y0": 600, "x1": 550, "y1": 755},
        "label": "signature",
        "confidence": 0.72,
        "model_name": "opencv",
        "reasons": ["dark_stroke_density"],
        "used_for_promotion": False,
    }
    structure = SigningBlockDetectionResult(low_confidence_candidates=[candidate])

    SigningRegionStage._promote_visual_supported_candidates(
        structure,
        {"_visual_candidates": [visual_candidate]},
    )

    assert structure.blocks == []
    assert visual_candidate["used_for_promotion"] is False


def test_signing_region_stage_does_not_promote_body_reference_to_signing_page() -> None:
    candidate = {
        "page_no": 11,
        "bbox": {"x0": 70, "y0": 242, "x1": 515, "y1": 508},
        "score": 0.35,
        "reasons": ["signing_page_context", "bottom_signing_position"],
        "block_ids": ["notice_body"],
        "text": "形式作出，并通过专人递送、特快专递或传真方式送达本合同签署页中的有关地\n"
        "址。当事人对其送达地址作出变更的，应自变更之日起五日内将变更后的送达地\n"
        "（1）若为专人递交，于递送时；",
    }
    visual_candidate = {
        "page_no": 11,
        "bbox": {"x0": 70, "y0": 242, "x1": 515, "y1": 508},
        "label": "signature",
        "confidence": 0.77,
        "model_name": "opencv",
        "reasons": ["dark_stroke_density"],
        "used_for_promotion": False,
    }
    structure = SigningBlockDetectionResult(low_confidence_candidates=[candidate])

    SigningRegionStage._promote_visual_supported_candidates(
        structure,
        {"_visual_candidates": [visual_candidate]},
    )

    assert structure.blocks == []
    assert visual_candidate["used_for_promotion"] is False


def test_signing_region_stage_promotes_party_label_candidate_with_red_seal_visual() -> None:
    candidate = {
        "page_no": 8,
        "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
        "score": 0.4,
        "reasons": ["paired_parties", "bottom_signing_position"],
        "block_ids": ["party_labels"],
        "text": "甲方：A公司\n乙方：B公司",
    }
    visual_candidate = {
        "page_no": 8,
        "bbox": {"x0": 100, "y0": 640, "x1": 500, "y1": 740},
        "label": "seal",
        "confidence": 0.95,
        "model_name": "opencv",
        "reasons": ["red_seal_pixels"],
        "used_for_promotion": False,
    }
    structure = SigningBlockDetectionResult(low_confidence_candidates=[candidate])

    SigningRegionStage._promote_visual_supported_candidates(
        structure,
        {"_visual_candidates": [visual_candidate]},
    )

    assert len(structure.blocks) == 1
    assert structure.blocks[0].page_no == 8
    assert structure.blocks[0].confidence_level == "medium"
    assert visual_candidate["used_for_promotion"] is True


def test_signing_region_stage_ignores_malformed_low_confidence_candidates() -> None:
    visual_candidate = {
        "page_no": 2,
        "bbox": {"x0": 100, "y0": 640, "x1": 500, "y1": 740},
        "label": "signature",
        "confidence": 0.82,
        "model_name": "opencv",
        "reasons": ["table_or_stroke_density"],
        "used_for_promotion": False,
    }
    malformed_candidates = [
        None,
        "candidate",
        123,
        {
            "page_no": "bad",
            "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
            "score": 0.42,
            "reasons": ["signing_page_context"],
        },
        {
            "page_no": 2,
            "bbox": "bad",
            "score": 0.42,
            "reasons": ["signing_page_context"],
        },
        {
            "page_no": 2,
            "bbox": {"x0": "bad", "y0": 620, "x1": 520, "y1": 760},
            "score": 0.42,
            "reasons": ["signing_page_context"],
        },
        {
            "page_no": 2,
            "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
            "score": "bad",
            "reasons": ["signing_page_context"],
        },
        {
            "page_no": 2,
            "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
            "score": 0.42,
            "reasons": 123,
            "block_ids": 123,
        },
    ]
    structure = SigningBlockDetectionResult(low_confidence_candidates=malformed_candidates)

    regions = SigningRegionStage._candidate_regions_from_low_confidence(structure.low_confidence_candidates)
    SigningRegionStage._promote_visual_supported_candidates(
        structure,
        {"_visual_candidates": [visual_candidate]},
    )

    assert all(region.region_id.startswith("LC-") for region in regions)
    assert structure.blocks == []
    assert visual_candidate["used_for_promotion"] is False


def test_signing_region_stage_normalizes_string_candidate_lists_for_promotion() -> None:
    candidate = {
        "page_no": 2,
        "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
        "score": 0.42,
        "reasons": "signing_page_context",
        "block_ids": "candidate",
        "text": "甲方：A公司\n乙方：B公司\n盖章：\n签字：",
    }
    visual_candidate = {
        "page_no": 2,
        "bbox": {"x0": 100, "y0": 640, "x1": 500, "y1": 740},
        "label": "signature",
        "confidence": 0.82,
        "model_name": "opencv",
        "reasons": ["table_or_stroke_density"],
        "used_for_promotion": False,
    }
    structure = SigningBlockDetectionResult(low_confidence_candidates=[candidate])

    regions = SigningRegionStage._candidate_regions_from_low_confidence(structure.low_confidence_candidates)
    SigningRegionStage._promote_visual_supported_candidates(
        structure,
        {"_visual_candidates": [visual_candidate]},
    )

    assert regions[0].confidence_reasons == ["signing_page_context"]
    assert structure.blocks[0].confidence_reasons == ["signing_page_context", "visual_candidate_promoted"]
    assert structure.blocks[0].source_block_ids == ["candidate"]
    assert visual_candidate["used_for_promotion"] is True


def test_signing_region_stage_keeps_visual_enrichment_after_candidate_reextraction(tmp_path: Path) -> None:
    high_block = SigningBlock(
        block_id="SB-HIGH-1",
        page_no=1,
        bbox=BBox(x0=60, y0=620, x1=520, y1=760),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.72,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["seal_signature_date_cluster", "paired_parties"],
        source_block_ids=["signing"],
        text="甲方：A公司\n乙方：B公司\n盖章：\n签字：\n日期：",
        exclude_from_clause_diff=False,
    )
    low_candidate = {
        "page_no": 2,
        "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
        "score": 0.42,
        "reasons": ["signing_page_context", "business_signing_form_fields"],
        "block_ids": ["candidate"],
        "text": "甲方：A公司\n乙方：B公司\n盖章：\n签字：",
    }

    class _Detector:
        def detect(self, _document: Document) -> SigningBlockDetectionResult:
            return SigningBlockDetectionResult(
                blocks=[high_block.model_copy(deep=True)],
                low_confidence_candidates=[dict(low_candidate)],
            )

    class _VisualDetector:
        call_count = 0

        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            self.call_count += 1
            assert len(regions) == 2
            region = regions[0]
            return VisualDetectionResult(
                available=True,
                model_name="opencv",
                detections=[
                    VisualDetection(
                        page_no=region.page_no,
                        bbox=region.bbox,
                        label="signature",
                        confidence=0.88,
                        model_name="opencv",
                        raw_data={"reasons": ["table_or_stroke_density"]},
                    )
                ],
            )

    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="signing",
                        page_no=1,
                        text="甲方：A公司\n乙方：B公司\n盖章：\n签字：\n日期：",
                        bbox=BBox(x0=60, y0=620, x1=520, y1=760),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="candidate",
                        page_no=2,
                        text="甲方：A公司\n乙方：B公司\n盖章：\n签字：",
                        bbox=BBox(x0=80, y0=620, x1=520, y1=760),
                    )
                ],
            ),
        ],
    )
    compare = original.model_copy(deep=True)
    compare.filename = "compare.pdf"
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")
    visual_detector = _VisualDetector()
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=visual_detector,
        visual_enabled=True,
    )
    stage.visual_fingerprinter = None
    stage.block_detector = _Detector()

    stage.execute(ctx)

    assert visual_detector.call_count == 2
    assert any(
        element.source == "visual_model"
        for region in ctx.signing_regions_original
        for element in region.elements
    )
    assert any(
        element["source"] == "visual_model"
        for region in ctx.signing_region_debug["original_regions"]
        for element in region["elements"]
    )


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
