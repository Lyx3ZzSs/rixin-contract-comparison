from app.models import BBox, Document, DocumentProfile, Page, PageProfile, TextBlock
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.extractor import SigningRegionExtractor
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


def _block(
    block_id: str,
    text: str,
    bbox: BBox,
    *,
    page_no: int = 1,
    block_type: str = "text",
    layout_bbox: BBox | None = None,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=bbox,
        block_type=block_type,
        layout_bbox=layout_bbox,
    )


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


def _multi_page_document(pages: list[Page], *, page_role: str = "body") -> Document:
    profile = DocumentProfile(
        filename="test.pdf",
        page_count=len(pages),
        page_profiles=[
            PageProfile(
                page_no=page.page_no,
                width=page.width,
                height=page.height,
                page_role=page_role,
                text_block_count=len(page.blocks),
            )
            for page in pages
        ],
    )
    return Document(filename="test.pdf", path="test.pdf", page_count=len(pages), pages=pages, profile=profile)


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


def test_detector_excludes_cover_signing_info_table_with_signature_labels() -> None:
    page = Page(
        page_no=1,
        width=595,
        height=842,
        blocks=[
            _block("title", "采购合同", _bbox(180, 220, 420, 260), block_type="doc_title"),
            _block(
                "cover_table",
                "甲方\n江苏东大金智信息系统有限公司\n乙方\n国能日新科技股份有限公司\n"
                "签订地点\n北京\n签订日期\n2026年4月21日\n法定代表人\n签字\n盖章",
                _bbox(90, 560, 505, 720),
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


def test_detector_keeps_clause_tail_with_delivery_time_out_of_bottom_signing_block() -> None:
    page = Page(
        page_no=10,
        width=595,
        height=842,
        blocks=[
            _block(
                "clause_13_1",
                "13.1 本合同经双方盖章后生效，合同中未尽事宜由双方协商解决。",
                _bbox(86, 384, 532, 395),
                page_no=10,
            ),
            _block(
                "clause_14_1",
                "14.1本合同正本一式肆份，甲乙双方各执贰份。合同附页、报价单、订货单、收货验",
                _bbox(86, 505, 532, 516),
                page_no=10,
            ),
            _block(
                "clause_14_1_tail",
                "收单与合同正本具同等法律效力。",
                _bbox(62, 529, 241, 539),
                page_no=10,
            ),
            _block(
                "clause_start",
                "14.2甲方在合同履行过程中，要求乙方提供合同约定范围之外的硬件设备、软件产品、",
                _bbox(85, 552, 536, 563),
                page_no=10,
            ),
            _block(
                "clause_middle",
                "运输安装等行为，双方应就补充采购签订书面补充协议，明确：采购内容及技术规格、价格",
                _bbox(62, 575, 531, 586),
                page_no=10,
            ),
            _block(
                "clause_tail",
                "及付款方式、交付时间、验收标准。",
                _bbox(61, 598, 254, 610),
                page_no=10,
            ),
            _block("party", "甲方：江苏东大金智信息系统有限公司  乙方：国能日新科技股份有限公司", _bbox(87, 622, 483, 633), page_no=10),
            _block("seal_a", "(盖章)", _bbox(67, 643, 104, 659), page_no=10),
            _block("seal_b", "(盖章)", _bbox(338, 643, 375, 659), page_no=10),
            _block("rep_a", "法人代表或授权委托人：", _bbox(85, 669, 210, 680), page_no=10),
            _block("rep_b", "法人代表或授权委托人：", _bbox(326, 669, 451, 680), page_no=10),
            _block("sign_a", "(签字)", _bbox(175, 690, 213, 707), page_no=10),
            _block("sign_b", "(签字)", _bbox(392, 690, 430, 707), page_no=10),
            _block("date_a", "日期：", _bbox(84, 712, 121, 730), page_no=10),
            _block("date_b", "日期：", _bbox(325, 712, 361, 730), page_no=10),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert "clause_tail" not in block.source_block_ids
    assert "交付时间" not in block.text
    assert set(block.source_block_ids) >= {"party", "seal_a", "seal_b", "rep_a", "rep_b", "sign_a", "sign_b", "date_a", "date_b"}


def test_extractor_builds_region_from_detected_signing_block() -> None:
    page = Page(
        page_no=10,
        width=595,
        height=842,
        blocks=[
            _block("party", "甲方：A公司  乙方：B公司", _bbox(65, 620, 485, 635), page_no=10),
            _block("seal_a", "(盖章)", _bbox(65, 642, 110, 660), page_no=10),
            _block("sign_a", "(签字)", _bbox(175, 690, 215, 708), page_no=10),
            _block("date_a", "日期：", _bbox(84, 713, 122, 730), page_no=10),
        ],
    )
    detection = SigningBlockDetector().detect(_document(page))

    regions = SigningRegionExtractor().extract_from_blocks(detection.blocks)

    assert len(regions) == 1
    assert regions[0].signing_block_id == "SB-10-1"
    assert regions[0].page_no == 10
    assert "盖章" in regions[0].elements[0].text or "签字" in regions[0].elements[0].text


def test_detector_keeps_body_effective_clause_out_of_signing_block() -> None:
    page = Page(
        page_no=13,
        width=595,
        height=842,
        blocks=[
            _block("effective", "本合同经双方签字盖章后生效。", _bbox(80, 580, 340, 600), page_no=13),
            _block("party", "甲方：A公司  乙方：B公司", _bbox(70, 625, 410, 645), page_no=13),
            _block("seal_a", "(盖章)", _bbox(70, 660, 120, 680), page_no=13),
            _block("seal_b", "(盖章)", _bbox(330, 660, 380, 680), page_no=13),
            _block("sign_a", "(签字)", _bbox(170, 700, 215, 720), page_no=13),
            _block("sign_b", "(签字)", _bbox(390, 700, 435, 720), page_no=13),
            _block("date_a", "日期：", _bbox(80, 735, 122, 755), page_no=13),
            _block("date_b", "日期：", _bbox(325, 735, 365, 755), page_no=13),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    assert "effective" not in result.blocks[0].source_block_ids
    assert len(result.pages) == 1
    signing_page = result.pages[0]
    assert signing_page.signing_page_type == SigningPageType.MIXED_PAGE
    assert signing_page.exclude_full_page_from_clause_diff is False


def test_detector_does_not_exclude_body_like_party_label_obligations() -> None:
    page = Page(
        page_no=14,
        width=595,
        height=842,
        blocks=[
            _block(
                "party_a_obligation",
                "甲方：应当在签署日期后签字盖章并履行付款义务。",
                _bbox(70, 650, 520, 675),
                page_no=14,
            ),
            _block(
                "party_b_obligation",
                "乙方：应负责按合同约定交付服务。",
                _bbox(70, 685, 430, 710),
                page_no=14,
            ),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert all(not block.exclude_from_clause_diff for block in result.blocks)


def test_detector_ignores_body_text_that_only_mentions_parties() -> None:
    page = Page(
        page_no=7,
        width=595,
        height=842,
        blocks=[
            _block(
                "payment_body",
                "乙方为甲方提供的标的物总额人民币594000.00元，金额大写人民币：伍拾玖万肆仟元整。",
                _bbox(45, 600, 550, 635),
                page_no=7,
            ),
            _block(
                "acceptance_body",
                "乙方设备货到现场，甲方签收确认无误且收到相应发票后20日内，甲方应将到货款支付给乙方。",
                _bbox(45, 720, 550, 755),
                page_no=7,
            ),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert result.blocks == []
    assert result.low_confidence_candidates == []


def test_detector_extends_top_signing_page_to_continuation_fields() -> None:
    page = Page(
        page_no=9,
        width=595,
        height=842,
        blocks=[
            _block("context", "签字页，此页无正文", _bbox(62, 67, 171, 81), page_no=9),
            _block("party_a", "甲方：南京瑞尚电力科技有限公司", _bbox(64, 118, 273, 131), page_no=9),
            _block("party_b", "乙方：国能日新科技股份有限公司", _bbox(302, 118, 512, 131), page_no=9),
            _block("seal_a", "(盖章)", _bbox(110, 145, 154, 167), page_no=9),
            _block("seal_b", "(盖章)", _bbox(343, 144, 388, 167), page_no=9),
            _block("address", "地址：江苏省南京市栖霞区八卦洲街 地址：北京市海淀区西三旗建材城中", _bbox(61, 179, 532, 194), page_no=9),
            _block("address_left", "道鹂岛路254号悦福大厦12-0044", _bbox(62, 210, 271, 226), page_no=9),
            _block("address_right", "路27号1幢2层227号", _bbox(309, 210, 458, 226), page_no=9),
            _block("rep_a", "法人代表或授权委托人：", _bbox(62, 241, 207, 257), page_no=9),
            _block("rep_b", "法人代表或授权委托人：", _bbox(303, 241, 448, 257), page_no=9),
            _block("sign_a", "(签字)", _bbox(166, 269, 211, 292), page_no=9),
            _block("sign_b", "(签字)", _bbox(407, 269, 452, 292), page_no=9),
            _block("zip_a", "邮编：", _bbox(59, 301, 99, 322), page_no=9),
            _block("zip_b", "邮编：100096", _bbox(302, 302, 388, 320), page_no=9),
            _block("date_a", "日期：", _bbox(60, 332, 99, 353), page_no=9),
            _block("date_b", "日期：", _bbox(300, 331, 339, 353), page_no=9),
            _block("footer", "共9页第9页", _bbox(272, 780, 322, 793), page_no=9),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"address", "rep_a", "sign_a", "zip_a", "date_a"}.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert block.bbox.y1 >= 370
    assert result.pages[0].signing_page_type == SigningPageType.FULL_PAGE


def test_detector_extends_scanned_party_seal_header_to_business_fields() -> None:
    page = Page(
        page_no=53,
        width=595,
        height=842,
        blocks=[
            _block("party_a", "甲方：国能长源随州发电有限公司", _bbox(89, 100, 286, 128), page_no=53),
            _block("party_b", "乙方：国能日新科技股份有限公司", _bbox(304, 98, 494, 119), page_no=53),
            _block("seal_a", "州发电有限公", _bbox(137, 73, 214, 113), page_no=53, block_type="seal"),
            _block("seal_b", "合同专用章", _bbox(378, 191, 439, 220), page_no=53, block_type="seal"),
            _block("seal_label", "(盖章):", _bbox(302, 165, 358, 186), page_no=53),
            _block("address_a", "地址：随州市浙河镇樊家冲村", _bbox(92, 237, 253, 257), page_no=53),
            _block("address_b", "地址：北京市海淀区西三旗建材城", _bbox(305, 232, 497, 252), page_no=53),
            _block("address_b2", "内1幢二层227号", _bbox(348, 264, 451, 285), page_no=53),
            _block("phone_a", "电话：岳洪波19007223578", _bbox(92, 338, 239, 356), page_no=53),
            _block("phone_b", "电话：黄科15648181206", _bbox(309, 333, 446, 353), page_no=53),
            _block("email_a", "邮箱：12048144@ceic.com", _bbox(92, 407, 237, 423), page_no=53),
            _block("email_b", "邮箱：110888031@qq.com", _bbox(305, 403, 447, 422), page_no=53),
            _block("sign_a", "代表签字：", _bbox(90, 439, 148, 459), page_no=53),
            _block("sign_b", "代表签字：", _bbox(303, 436, 363, 458), page_no=53),
            _block("date_a", "2026年5月6日", _bbox(88, 502, 195, 527), page_no=53),
            _block("date_b", "2026年5月6日", _bbox(313, 501, 418, 524), page_no=53),
            _block("footer", "52", _bbox(283, 779, 298, 793), page_no=53),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"party_a", "party_b", "address_a", "phone_b", "email_b", "sign_a", "date_b"}.issubset(
        block.source_block_ids
    )
    assert "footer" not in block.source_block_ids
    assert block.bbox.y1 >= 540
    assert block.confidence >= 0.7


def test_detector_extends_scanned_party_seal_header_to_blank_day_date_values() -> None:
    page = Page(
        page_no=53,
        width=595,
        height=842,
        blocks=[
            _block("party_a", "甲方：国能长源随州发电有限公司", _bbox(70, 86, 287, 101), page_no=53),
            _block("party_b", "乙方：国能日新科技股份有限公司", _bbox(313, 86, 523, 101), page_no=53),
            _block("party_a_tail", "随县分公司", _bbox(125, 120, 196, 137), page_no=53),
            _block("seal_a", "（盖章）：", _bbox(75, 154, 134, 172), page_no=53),
            _block("seal_b", "（盖章）：", _bbox(313, 154, 372, 172), page_no=53),
            _block("address_a", "地址：随州市淅河镇樊家冲村", _bbox(70, 226, 251, 241), page_no=53),
            _block("address_b", "地址：北京市海淀区西三旗建材城", _bbox(315, 226, 524, 241), page_no=53),
            _block("address_b_tail", "内 1 幢二层 227 号", _bbox(364, 260, 476, 276), page_no=53),
            _block("phone_a", "电话：岳洪波19007223578", _bbox(72, 331, 235, 345), page_no=53),
            _block("phone_b", "电话：黄科15648181206", _bbox(320, 331, 470, 345), page_no=53),
            _block("email_a", "邮箱：12048144@ceic.com", _bbox(70, 400, 233, 417), page_no=53),
            _block("email_b", "邮箱：110888031@qq.com", _bbox(315, 400, 471, 417), page_no=53),
            _block("sign_a", "代表签字：", _bbox(69, 434, 133, 452), page_no=53),
            _block("sign_b", "代表签字：", _bbox(314, 434, 378, 452), page_no=53),
            _block("date_a", "2026 年 5 月 日", _bbox(68, 504, 186, 522), page_no=53),
            _block("date_b", "2026 年 5 月 日", _bbox(324, 504, 442, 522), page_no=53),
            _block("footer", "52", _bbox(288, 778, 305, 793), page_no=53),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"date_a", "date_b"}.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert block.bbox.y1 >= 540


def test_detector_recovers_signing_page_from_context_and_business_fields_when_labels_are_ocr_degraded() -> None:
    page = Page(
        page_no=2,
        width=597,
        height=819,
        blocks=[
            _block("body", "十二、本合同自双方签字盖章之日起生效。本合同1式4份", _bbox(68, 202, 386, 214), page_no=2),
            _block("context", "以下无正文", _bbox(68, 245, 130, 261), page_no=2),
            _block("title", "签字页", _bbox(280, 335, 327, 355), page_no=2),
            _block(
                "degraded_table",
                "需\n方\n供\n司宁\n电\n话：010-83458100\n电话：18097182156\n传\n传真：\n"
                "真：010-83458107\n开户银行：招商银行北京大屯路支行\n"
                "开户银行：招商银行股份有限公司西宁生物园区\n支行\n帐\n账\n"
                "号：110904199110901\n号：972900591810801\n税\n税\n"
                "号：91630000MA7588E76L\n号：911101086723891430\n邮政编码：100096\n邮政编码：813000",
                _bbox(63, 389, 545, 654),
                page_no=2,
                block_type="table",
            ),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.confidence >= 0.5
    assert "signing_context_business_fields" in block.confidence_reasons
    assert "degraded_table" in block.source_block_ids
    assert block.exclude_from_clause_diff is True


def test_detector_keeps_degraded_signing_table_with_responsible_person_label() -> None:
    page = Page(
        page_no=13,
        width=595,
        height=842,
        blocks=[
            _block("title", "签署页", _bbox(265, 90, 336, 114), page_no=13, block_type="paragraph_title"),
            _block(
                "degraded_table",
                "州发电有限公司\n甲方\n（盖章)：\n国能日新科技股份有限公司\n"
                "国能长源随州发电有限公司随县分公司\n法定代表专出\n法定代表人（负责人）\n"
                "1周\n授权代表002406\n授权代表（签房0813622\n"
                "地址：北京市海淀区西三旗建材城内1\n地址：随州市淅河镇樊家冲村\n"
                "幢二层227号\n邮编：441300\n邮编：100096\n联系人：岳洪波\n联系人：黄科\n"
                "电话：19007223578\n电话：15648181206\n开户银行：中国农业银行随州浙河支行\n"
                "开户银行：招商银行北京大屯路支行\n账号：17780801040004555\n"
                "账号：110904199110901\n统一社会信用代码：\n统一社会信用代码：\n"
                "91421300MA49GRNC88\n911101086723891430\n"
                "签订时间：2026年5月15日\n签订时间：2026年51月15日",
                _bbox(89, 146.5, 511, 507.5),
                page_no=13,
                block_type="table",
            ),
            _block("attachment", "附件一：安全生产管理协议", _bbox(93, 619, 224, 632.5), page_no=13),
            _block("footer", "12", _bbox(290.5, 782.5, 305, 796.5), page_no=13),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"title", "degraded_table"}.issubset(block.source_block_ids)
    assert "attachment" not in block.source_block_ids
    assert "footer" not in block.source_block_ids
    assert block.confidence >= 0.7
    assert block.exclude_from_clause_diff is True


def test_detector_extends_vertically_stacked_party_signature_sections() -> None:
    page = Page(
        page_no=29,
        width=595,
        height=842,
        blocks=[
            _block("party_a", "甲方（盖章）", _bbox(90, 127, 175, 144), page_no=29),
            _block("rep_a", "法定代表人（负责人）/授权代表（签字）", _bbox(90, 168, 368, 184), page_no=29),
            _block("date_a", "年月日", _bbox(197, 204, 333, 227), page_no=29),
            _block("time_a", "时间：", _bbox(87, 206, 143, 226), page_no=29),
            _block("party_b", "乙方（盖章）", _bbox(89, 307, 175, 324), page_no=29),
            _block("rep_b", "法定代表人（负责人）/授权代表（签字）", _bbox(89, 348, 367, 363), page_no=29),
            _block("date_b", "年月日", _bbox(196, 384, 333, 407), page_no=29),
            _block("time_b", "时间：", _bbox(87, 385, 143, 405), page_no=29),
            _block("footer", "-28-", _bbox(274, 778, 319, 797), page_no=29),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"party_a", "rep_a", "time_a", "party_b", "rep_b", "time_b"}.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert block.block_role == SigningBlockRole.BOTH_PARTIES
    assert block.bbox.y1 >= 425
    assert block.confidence >= 0.7


def test_detector_extends_scanned_stacked_signature_sections_with_lower_seal_and_time() -> None:
    page = Page(
        page_no=29,
        width=595,
        height=842,
        blocks=[
            _block("party_a", "甲方（盖章", _bbox(112, 137, 191, 160), page_no=29, block_type="seal"),
            _block("rep_a", "法定代表人（负责人）/授权代表（签字）", _bbox(110, 173, 370, 199), page_no=29),
            _block("seal_code_a", "42130130002406", _bbox(159, 191, 224, 214), page_no=29, block_type="seal"),
            _block("time_a", "时间：2026年 5月8日", _bbox(108, 209, 336, 239), page_no=29),
            _block("sign_a", "17高", _bbox(352, 179, 410, 236), page_no=29),
            _block("seal_b", "图", _bbox(260, 333, 278, 350), page_no=29, block_type="seal"),
            _block("time_b", "时间：2026年5月8日", _bbox(108, 383, 337, 416), page_no=29),
            _block("footer", "-28-", _bbox(282, 781, 320, 796), page_no=29),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"party_a", "rep_a", "time_a", "seal_b", "time_b"}.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert block.bbox.y1 >= 430
    assert block.confidence >= 0.6


def test_detector_uses_tight_top_padding_for_bottom_signing_block() -> None:
    page = Page(
        page_no=10,
        width=595,
        height=842,
        blocks=[
            _block("body_tail", "及付款方式、交付时间、验收标准。", _bbox(62, 598, 254, 611), page_no=10),
            _block("party", "甲方：A公司  乙方：B公司", _bbox(87, 623, 483, 633), page_no=10),
            _block("seal_a", "(盖章)", _bbox(68, 644, 105, 659), page_no=10),
            _block("seal_b", "(盖章)", _bbox(338, 644, 375, 659), page_no=10),
            _block("rep_a", "法人代表或授权委托人：", _bbox(86, 669, 211, 680), page_no=10),
            _block("rep_b", "法人代表或授权委托人：", _bbox(327, 669, 451, 680), page_no=10),
            _block("sign_a", "(签字)", _bbox(176, 690, 213, 707), page_no=10),
            _block("sign_b", "(签字)", _bbox(392, 690, 430, 707), page_no=10),
            _block("date_a", "日期：", _bbox(85, 713, 121, 730), page_no=10),
            _block("date_b", "日期：", _bbox(326, 713, 361, 730), page_no=10),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert "body_tail" not in block.source_block_ids
    assert block.bbox.y0 > 611


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


def test_detector_recovers_lower_middle_two_column_signing_block() -> None:
    page = Page(
        page_no=7,
        width=595,
        height=842,
        blocks=[
            _block("body_end", "22.5 本合同一式两份，双方各持一份，传真件有效。", _bbox(63, 286, 316, 298), page_no=7),
            _block("party_a", "甲方：【南京国电南自电网自动化有限公司】（盖章）", _bbox(62, 334, 284, 354), page_no=7),
            _block("party_b", "乙方：【国能日新科技股份有限公司】（盖章）", _bbox(277, 338, 520, 348), page_no=7),
            _block("rep_a", "授权代表签字：", _bbox(62, 371, 142, 384), page_no=7),
            _block("rep_b", "授权代表签字：", _bbox(286, 371, 360, 384), page_no=7),
            _block("tax_a", "纳税人识别号：91320115716208030H", _bbox(62, 388, 260, 401), page_no=7),
            _block("tax_b", "纳税人识别号：", _bbox(286, 389, 360, 401), page_no=7),
            _block("address_a", "地址：南京市江宁经济技术开发区水阁路", _bbox(62, 405, 271, 419), page_no=7),
            _block("address_b", "地址：", _bbox(285, 405, 329, 418), page_no=7),
            _block("phone_a", "电话：025-69833061", _bbox(64, 440, 174, 452), page_no=7),
            _block("phone_b", "电话：", _bbox(286, 439, 324, 453), page_no=7),
            _block("bank_a", "开户行：中行江宁开发区支行", _bbox(64, 458, 204, 470), page_no=7),
            _block("bank_b", "开户行：", _bbox(285, 456, 328, 471), page_no=7),
            _block("account_a", "账号:532658192135", _bbox(64, 475, 172, 486), page_no=7),
            _block("account_b", "账号：", _bbox(286, 473, 324, 488), page_no=7),
            _block("date_a", "日期：2026.4.17", _bbox(64, 508, 170, 528), page_no=7),
            _block("date_b", "日期：", _bbox(285, 507, 319, 523), page_no=7),
            _block("edge_seal_fragment", "司", _bbox(554, 576, 591, 651), page_no=7, block_type="seal"),
            _block("footer", "7", _bbox(293, 760, 302, 771), page_no=7),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"party_a", "party_b", "rep_a", "rep_b", "date_a", "date_b"}.issubset(block.source_block_ids)
    assert "body_end" not in block.source_block_ids
    assert "edge_seal_fragment" not in block.source_block_ids
    assert "footer" not in block.source_block_ids
    assert block.bbox.x1 < 540
    assert block.confidence >= 0.7
    assert block.exclude_from_clause_diff is True


def test_detector_extends_signing_page_title_across_blank_gap_to_business_fields() -> None:
    page = Page(
        page_no=12,
        width=606,
        height=826,
        blocks=[
            _block("title", "签署页", _bbox(258, 82, 341, 103), page_no=12, block_type="paragraph_title"),
            _block("address", "地址：北京市西城区广安门内大街 地址：北京市海淀区建材城中路27号", _bbox(92, 270, 512, 293), page_no=12),
            _block("contact_a", "联系人：环加飞", _bbox(93, 324, 192, 341), page_no=12),
            _block("phone_a", "电话：010-83582793", _bbox(94, 347, 219, 365), page_no=12),
            _block("fax_a", "传真：010-83582600", _bbox(94, 371, 219, 389), page_no=12),
            _block("contact_b", "联系人：刘玉良", _bbox(312, 320, 414, 337), page_no=12),
            _block("phone_b", "电话：18811089109", _bbox(313, 343, 434, 362), page_no=12),
            _block("bank", "开户银行：中国工商银行股份有限公司北京白广路支行 开户银行：招商银行北京大屯路支行", _bbox(95, 412, 512, 436), page_no=12),
            _block("account", "账号：901027101 账号：110904199110901", _bbox(95, 466, 463, 484), page_no=12),
            _block("credit", "统一社会信用代码：91110000053621038D 统一社会信用代码：911101086723891430", _bbox(96, 483, 513, 527), page_no=12),
            _block("footer", "12", _bbox(306, 765, 316, 774), page_no=12),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"title", "address", "bank", "account", "credit"}.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert block.confidence >= 0.7
    assert "signing_context_business_fields" in block.confidence_reasons
    assert block.exclude_from_clause_diff is True


def test_detector_excludes_medium_bottom_seal_signature_date_cluster_from_clause_diff() -> None:
    page = Page(
        page_no=7,
        width=597,
        height=818,
        blocks=[
            _block("body_end", "22.5本合同一式两份，双方各持一份，传真件有效。", _bbox(68, 281, 319, 293), page_no=7),
            _block("party_a", "甲方：【南", _bbox(68, 329, 128, 344), page_no=7),
            _block("seal_a", "司】（盖章", _bbox(68, 347, 126, 361), page_no=7),
            _block("rep_a", "授权代表签", _bbox(68, 364, 126, 378), page_no=7),
            _block("address", "地址：南京", _bbox(68, 397, 140, 410), page_no=7),
            _block("phone", "电话：025-69833061", _bbox(68, 430, 180, 444), page_no=7),
            _block("bank", "开户行：中行江宁开发区支行", _bbox(69, 448, 209, 460), page_no=7),
            _block("account", "账号:532658192135", _bbox(69, 465, 177, 477), page_no=7),
            _block("date_a", "日期：2026.4.17", _bbox(68, 497, 174, 517), page_no=7),
            _block("date_b", "日期：", _bbox(286, 487, 406, 522), page_no=7),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.confidence == 0.6
    assert "seal_signature_date_cluster" in block.confidence_reasons
    assert block.exclude_from_clause_diff is True


def test_detector_recovers_terminal_business_only_signing_page_after_no_text_marker() -> None:
    merged_layout_bbox = _bbox(90.5, 287, 507, 472)
    notice_page = Page(
        page_no=22,
        width=597,
        height=823,
        blocks=[
            _block("notice_title", "12. 通知及同意", _bbox(116, 137, 209, 154), page_no=22, block_type="paragraph_title"),
            _block("notice_intro", "双方接收所有该等通知及同意的地址、传真号码和电子邮箱地址如下：", _bbox(120, 480, 509, 526), page_no=22),
            _block("notice_a", "甲方：", _bbox(120, 529, 159, 550), page_no=22),
            _block("notice_addr_a", "地址：北京市西城区广安门内大街482号，传真号码：010-83582600", _bbox(121, 549, 512, 595), page_no=22),
            _block("notice_b", "乙方：", _bbox(121, 600, 160, 619), page_no=22),
            _block("notice_addr_b", "地址：北京市海淀区建材城中路27号，电子邮箱：yuliang.liu@sprixin.com", _bbox(123, 619, 512, 665), page_no=22),
        ],
    )
    no_text_page = Page(
        page_no=23,
        width=597,
        height=823,
        blocks=[
            _block("body_end", "盖公章或合同专用章的日期为准。", _bbox(93, 97, 296, 112), page_no=23),
            _block("copies_title", "14. 份数", _bbox(118, 136, 172, 155), page_no=23, block_type="paragraph_title"),
            _block("copies", "本合同一式捌份，甲方执肆份，乙方执肆份，具有同等效力。", _bbox(119, 175, 494, 194), page_no=23),
            _block("special_title", "15. 特别约定", _bbox(120, 214, 200, 234), page_no=23, block_type="paragraph_title"),
            _block("special", "本特别约定是合同各方经协商后对合同其他条款的修改或补充，如有不一致，以特别约定为准。", _bbox(93, 256, 508, 296), page_no=23),
            _block("marker", "(以下无正文)", _bbox(126, 326, 215, 344), page_no=23),
        ],
    )
    signing_tail_page = Page(
        page_no=24,
        width=597,
        height=823,
        blocks=[
            _block(
                "address",
                "地址：北京市西城区广安门内大街 地址：北京市海淀区建材城中路",
                _bbox(91, 289, 509, 310),
                page_no=24,
                layout_bbox=merged_layout_bbox,
            ),
            _block("address_left", "482号", _bbox(91, 317, 133, 334), page_no=24, layout_bbox=merged_layout_bbox),
            _block(
                "address_right",
                "27号金隅智造工场N6",
                _bbox(313, 316, 452, 333),
                page_no=24,
                layout_bbox=merged_layout_bbox,
            ),
            _block("contact_a", "联系人：环加飞", _bbox(93, 341, 193, 357), page_no=24, layout_bbox=merged_layout_bbox),
            _block("contact_b", "联系人：刘玉良", _bbox(313, 340, 413, 357), page_no=24, layout_bbox=merged_layout_bbox),
            _block("phone_a", "电话：010-83582793", _bbox(93, 365, 218, 381), page_no=24, layout_bbox=merged_layout_bbox),
            _block("phone_b", "电话：18811089109", _bbox(314, 364, 435, 380), page_no=24, layout_bbox=merged_layout_bbox),
            _block("fax_a", "传真：010-83582600", _bbox(93, 388, 218, 405), page_no=24, layout_bbox=merged_layout_bbox),
            _block("fax_b", "传真：010-83458100", _bbox(315, 389, 439, 402), page_no=24, layout_bbox=merged_layout_bbox),
            _block(
                "email_a",
                "Email: huan.jiafei@nc.sgcc.com.cn",
                _bbox(92, 413, 303, 430),
                page_no=24,
                layout_bbox=merged_layout_bbox,
            ),
            _block(
                "email_b",
                "Email: yuliang.liu@sprixin.com",
                _bbox(312, 412, 503, 430),
                page_no=24,
                layout_bbox=merged_layout_bbox,
            ),
            _block(
                "credit_a",
                "统一社会信用代码：91110000053621038D",
                _bbox(93, 435, 302, 477),
                page_no=24,
                layout_bbox=merged_layout_bbox,
            ),
            _block(
                "credit_b",
                "统一社会信用代码：911101086723891430",
                _bbox(313, 435, 507, 475),
                page_no=24,
                layout_bbox=merged_layout_bbox,
            ),
            _block("footer", "24", _bbox(294, 759, 307, 771), page_no=24),
        ],
    )

    result = SigningBlockDetector().detect(_multi_page_document([notice_page, no_text_page, signing_tail_page]))

    assert [block.page_no for block in result.blocks] == [24]
    block = result.blocks[0]
    assert "terminal_business_signing_fields" in block.confidence_reasons
    assert {"address", "contact_a", "phone_a", "email_b", "credit_b"}.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert block.exclude_from_clause_diff is True


def test_detector_suppresses_nested_label_clusters_inside_full_signing_page() -> None:
    page = Page(
        page_no=13,
        width=595,
        height=842,
        blocks=[
            _block("title", "签字页", _bbox(78, 81, 118, 98), page_no=13),
            _block("party_a", "甲方", _bbox(80, 110, 125, 127), page_no=13),
            _block("party_b", "乙方", _bbox(319, 110, 364, 127), page_no=13),
            _block("name_a", "单位名称：定边县瑞能新能源科技有限", _bbox(84, 141, 285, 153), page_no=13),
            _block("name_b", "单位名称：国能日新科技股份有限公司", _bbox(321, 141, 521, 153), page_no=13),
            _block("seal_a", "公司（章）", _bbox(83, 168, 139, 182), page_no=13),
            _block("seal_b", "(章)", _bbox(323, 166, 354, 183), page_no=13),
            _block("rep_a", "法定代表人或授权代表签字：", _bbox(81, 197, 230, 209), page_no=13),
            _block("rep_b", "法定代表人或授权代表签字：", _bbox(320, 197, 470, 209), page_no=13),
            _block("address_a", "单位地址：陕西省榆林市定边县盐场堡", _bbox(84, 337, 285, 349), page_no=13),
            _block("address_b", "单位地址：北京市海淀区西三旗建材城", _bbox(321, 337, 522, 349), page_no=13),
            _block("addr_line_a", "镇西梁湾村冯湾15号", _bbox(84, 364, 196, 377), page_no=13),
            _block("addr_line_b", "内1幢二层227号", _bbox(320, 364, 417, 378), page_no=13),
            _block("phone_a", "电话：029-61825538", _bbox(81, 392, 214, 406), page_no=13),
            _block("phone_b", "电话：010-83458100", _bbox(320, 392, 453, 406), page_no=13),
            _block("bank_a", "开户银行：工商银行定边新区支行", _bbox(82, 422, 254, 432), page_no=13),
            _block("bank_b", "开户银行：招商银行北京大屯路支行", _bbox(321, 422, 511, 432), page_no=13),
            _block("tax_a", "税号：91610825MA703C1J05", _bbox(79, 447, 251, 462), page_no=13),
            _block("tax_b", "税号：911101086723891430", _bbox(318, 447, 490, 462), page_no=13),
            _block("account_a", "帐号：2610064209200113047", _bbox(79, 475, 256, 491), page_no=13),
            _block("account_b", "帐号：110904199110901", _bbox(319, 476, 471, 490), page_no=13),
            _block("contact_a", "联系人及电话：党学涛15229399022", _bbox(82, 505, 270, 517), page_no=13),
            _block("contact_b", "联系人及电话：李伟18991278230", _bbox(320, 505, 497, 517), page_no=13),
            _block("date_a", "签字日期：年月日", _bbox(81, 532, 232, 546), page_no=13),
            _block("date_b", "签字日期：年月日", _bbox(320, 532, 465, 546), page_no=13),
            _block("footer", "13", _bbox(300, 779, 312, 791), page_no=13),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.page_no == 13
    assert {"title", "rep_a", "rep_b", "date_a", "date_b"}.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert block.exclude_from_clause_diff is True
