from app.models import BBox, Document, DocumentProfile, Page, PageProfile, TextBlock
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningPage,
    SigningPageType,
    SigningVisualFeatures,
)


def _bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _block(block_id: str, text: str, bbox: BBox, *, page_no: int = 1, block_type: str = "text") -> TextBlock:
    return TextBlock(block_id=block_id, page_no=page_no, text=text, bbox=bbox, block_type=block_type)


def _document(page: Page, *, page_role: str = "body") -> Document:
    profile = DocumentProfile(
        filename="test.pdf",
        page_count=1,
        page_profiles=[
            PageProfile(
                page_no=page.page_no,
                width=page.width,
                height=page.height,
                page_role=page_role,
                text_block_count=len(page.blocks),
            )
        ],
    )
    return Document(filename="test.pdf", path="test.pdf", page_count=1, pages=[page], profile=profile)


def test_signing_structure_models_hold_block_page_and_visual_features() -> None:
    visual = SigningVisualFeatures(
        status="ok",
        has_red_seal=True,
        has_handwriting=False,
        visual_hash="abc123",
        confidence=0.8,
        reasons=["red_connected_component"],
    )
    block = SigningBlock(
        block_id="SB-10-1",
        page_no=10,
        bbox=BBox(x0=60, y0=610, x1=520, y1=740),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["paired_parties", "seal_signature_date_cluster"],
        source_block_ids=["p10_b24", "p10_b25"],
        text="甲方：A 乙方：B\n(盖章)\n(签字)\n日期：",
        visual_features=visual,
        exclude_from_clause_diff=True,
    )
    page = SigningPage(
        page_no=10,
        bbox=BBox(x0=0, y0=0, x1=595, y1=842),
        signing_page_type=SigningPageType.MIXED_PAGE,
        confidence=0.72,
        confidence_reasons=["contains_high_confidence_signing_block"],
        block_ids=[block.block_id],
        exclude_full_page_from_clause_diff=False,
    )

    assert page.signing_page_type == SigningPageType.MIXED_PAGE
    assert block.block_role == SigningBlockRole.BOTH_PARTIES
    assert block.visual_features.visual_hash == "abc123"
    assert block.exclude_from_clause_diff is True


def test_detector_excludes_cover_signing_info_table() -> None:
    page = Page(
        page_no=1,
        width=595,
        height=842,
        blocks=[
            _block("title", "采购合同", _bbox(180, 220, 420, 260), block_type="doc_title"),
            _block(
                "cover_table",
                "甲方\n江苏东大金智信息系统有限公司\n乙方\n国能日新科技股份有限公司\n北京\n签订地点\n签订日期\n2026年4月21日",
                _bbox(90, 560, 505, 690),
                block_type="table",
            ),
        ],
    )

    result = SigningBlockDetector().detect(_document(page, page_role="cover"))

    assert result.blocks == []
    assert result.excluded_candidates[0]["reason"] == "cover_signing_info_table"


def test_detector_finds_bottom_mixed_page_signing_block() -> None:
    page = Page(
        page_no=10,
        width=595,
        height=842,
        blocks=[
            _block("body", "14.2甲方在合同履行过程中，要求乙方提供合同约定范围之外的硬件设备。", _bbox(80, 550, 530, 590), page_no=10),
            _block("party", "甲方：江苏东大金智信息系统有限公司  乙方：国能日新科技股份有限公司", _bbox(65, 620, 485, 635), page_no=10),
            _block("seal_a", "(盖章)", _bbox(65, 642, 110, 660), page_no=10),
            _block("seal_b", "(盖章)", _bbox(335, 642, 380, 660), page_no=10),
            _block("rep_a", "法人代表或授权委托人：", _bbox(82, 668, 215, 682), page_no=10),
            _block("rep_b", "法人代表或授权委托人：", _bbox(326, 668, 455, 682), page_no=10),
            _block("sign_a", "(签字)", _bbox(175, 690, 215, 708), page_no=10),
            _block("sign_b", "(签字)", _bbox(390, 690, 430, 708), page_no=10),
            _block("date_a", "日期：", _bbox(84, 713, 122, 730), page_no=10),
            _block("date_b", "日期：", _bbox(325, 713, 362, 730), page_no=10),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.page_no == 10
    assert block.exclude_from_clause_diff is True
    assert block.confidence_level == "high"
    assert "seal_signature_date_cluster" in block.confidence_reasons
    assert set(block.source_block_ids) >= {"party", "seal_a", "rep_a", "sign_a", "date_a"}

    assert len(result.pages) == 1
    signing_page = result.pages[0]
    assert signing_page.page_role == "body"
    assert signing_page.signing_page_type == SigningPageType.MIXED_PAGE
    assert signing_page.exclude_full_page_from_clause_diff is False


def test_detector_allows_full_page_exclusion_for_pure_signing_page() -> None:
    page = Page(
        page_no=11,
        width=595,
        height=842,
        blocks=[
            _block("context", "以下无正文，为签字页", _bbox(80, 120, 240, 140), page_no=11),
            _block("party", "甲方：A公司  乙方：B公司", _bbox(70, 575, 410, 595), page_no=11),
            _block("seal_a", "(盖章)", _bbox(70, 620, 120, 640), page_no=11),
            _block("seal_b", "(盖章)", _bbox(330, 620, 380, 640), page_no=11),
            _block("sign_a", "(签字)", _bbox(170, 665, 215, 685), page_no=11),
            _block("sign_b", "(签字)", _bbox(390, 665, 435, 685), page_no=11),
            _block("date_a", "日期：", _bbox(80, 710, 122, 730), page_no=11),
            _block("date_b", "日期：", _bbox(325, 710, 365, 730), page_no=11),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.pages) == 1
    signing_page = result.pages[0]
    assert signing_page.signing_page_type == SigningPageType.FULL_PAGE
    assert signing_page.exclude_full_page_from_clause_diff is True


def test_detector_allows_full_page_exclusion_for_merged_signing_table() -> None:
    page = Page(
        page_no=12,
        width=595,
        height=842,
        blocks=[
            _block(
                "signing_table",
                "以下无正文，为签署页\n"
                "甲方：A公司\n"
                "乙方：B公司\n"
                "法定代表人：\n"
                "(盖章)\n"
                "(签字)\n"
                "日期：",
                _bbox(70, 120, 525, 735),
                page_no=12,
                block_type="table",
            ),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert [block.source_block_ids for block in result.blocks] == [["signing_table"]]
    assert len(result.pages) == 1
    signing_page = result.pages[0]
    assert signing_page.signing_page_type == SigningPageType.FULL_PAGE
    assert signing_page.exclude_full_page_from_clause_diff is True
