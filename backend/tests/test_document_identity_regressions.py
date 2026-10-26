"""Regressions for semantic identity across OCR layout representations."""
import pytest

from app.models import BBox, CharBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.cover_metadata import CoverMetadataComparator
from app.services.diff_quality import DiffQualityProcessor
from app.services.document_preparation import DocumentPreparer
from app.services.header_footer_compare import HeaderFooterComparator
from app.services.pipeline_stages import _reconcile_visual_signing_date_diffs
from app.services.signing_table_equivalence import equivalent_printed_fields, printed_fields


def block(text, x=70, y=100, kind="text", page=1, key=None):
    return TextBlock(block_id=key or f"{page}-{x}-{y}-{text}", text=text, page_no=page, block_type=kind,
                     bbox=BBox(x0=x, y0=y, x1=x + len(text) * 8, y1=y + 12))


def document(blocks, width=600, height=800):
    return Document(filename="test.pdf", path="test.pdf", page_count=1,
                    pages=[Page(page_no=1, width=width, height=height, blocks=blocks)])


def test_contract_number_is_not_project_title():
    original = document([block("委托人合同编号："), block("承包人合同编号：", y=130),
                         block("光伏电站技术服务合同", y=230, kind="doc_title")])
    compare = original.model_copy(deep=True)
    compare.pages[0].blocks[0].text += "AB-123456"
    fields = CoverMetadataComparator().extract(compare)
    assert fields["project_title"].value == "光伏电站技术服务合同"
    assert fields["contract_no_buyer"].value == "AB-123456"
    diffs = CoverMetadataComparator().build_diffs(original, compare)
    assert len(diffs) == 1 and diffs[0].diff_type == "ADD"
    assert "合同编号" in diffs[0].title


def test_repeated_title_has_same_owner_despite_header_classification():
    title = "光伏电站技术服务合同"
    original = document([block(title, kind="doc_title")])
    original.pages.append(Page(page_no=2, width=600, height=800,
                               blocks=[block(title, kind="header", page=2, key="repeat")]))
    original.page_count = 2
    compare = original.model_copy(deep=True)
    compare.pages[1].blocks[0].block_type = "doc_title"
    DocumentPreparer().prepare_pair(original, compare)
    assert not HeaderFooterComparator().build_diffs(original, compare)
    assert not original.pages[1].blocks[0].enter_clause_compare
    assert not compare.pages[1].blocks[0].enter_clause_compare


def test_unrelated_headers_on_different_pages_are_not_a_modification():
    left = document([block("项目技术服务合同", kind="header")])
    right = document([block("GN-2026-12345", kind="header", page=2)])
    assert {d.diff_type for d in HeaderFooterComparator().build_diffs(left, right)} == {"ADD", "DELETE"}


def test_sequence_recovers_page_numbers_above_canvas_footer_band():
    def pages(y):
        d = document([])
        d.pages = [Page(page_no=n, width=600, height=800, blocks=[
            block(str(n), x=280, y=y, kind="number", page=n, key=f"p{n}")]) for n in (2, 3, 4)]
        d.page_count = 3
        return d
    assert not HeaderFooterComparator().build_diffs(pages(745), pages(690))
    stray = document([block("7", y=690, kind="number")])
    assert not HeaderFooterComparator()._entries_by_slot(stray)


def test_wrapped_revision_is_removed_but_nearby_body_is_kept():
    heading = block("设置格式[Jy]: 字体：黑色", x=540, y=100, key="comment")
    tail = block("设置", x=540, y=125, key="tail")
    body = block("控制服务质量并承担责任。", x=90, y=125, key="body")
    d = document([heading, tail, body], width=800)
    DocumentPreparer().prepare(d, "original")
    assert tail.block_role == "revision_artifact"
    assert body.enter_clause_compare is not False


@pytest.mark.parametrize("values,blank", [(["L", "L"], True), (["1234567", "a@b.com"], False), (["L", "a@b.com"], False)])
def test_contact_placeholder_requires_paired_blank_fields(values, blank):
    left = document([block("传真号码：", key="fax"), block("邮箱地址：", y=125, key="mail")])
    right = document([block("传真号码：" + values[0], key="fax"), block("邮箱地址：" + values[1], y=125, key="mail")])
    DocumentPreparer().prepare_pair(left, right)
    assert right.pages[0].blocks[0].text == "传真号码：" + ("" if blank else values[0])


def test_long_numbered_body_keeps_amount_separate_from_scope():
    d = document([block("第一章 项目内容和费用", kind="paragraph_title", key="heading"),
                  block("1.乙方为甲方提供场站数据转发和配套技术支持服务，并承担相关工作。", y=130, key="1"),
                  block("2.合作方式：乙方按照甲方要求完成场站数据对接、转发及系统调试等工作。", y=160, key="2"),
                  block("3.本合同约定含税总金额为35000元，大写人民币叁万伍仟元整。", y=190, key="3"),
                  block("4.付款方式：", y=220, key="4")])
    clauses = ClauseSplitter().split(d, "T")
    assert [c.clause_no for c in clauses] == ["第一章", "1", "2", "3", "4"]
    assert "35000" not in next(c.text for c in clauses if c.clause_no == "2")


def signing_page(accounts=("111101", "222202"), address="地址一", table=False):
    blocks = []
    for col, party in enumerate(("委托人", "承包人")):
        texts = [f"{party}：公司{col}", "法定代表人或其委托代理人：", f"地址：{address}",
                 f"开户银行：银行{col}", f"账号：{accounts[col]}", f"纳税人登记号：12345{col}", "签字日期：年月日"]
        for n, text in enumerate(texts):
            b = block(text, x=70 + col * 260, y=100 + n * 55, key=f"{col}-{n}")
            b.char_boxes = [CharBox(char=char, page_no=1, text_index=i,
                                   bbox=BBox(x0=b.bbox.x0 + i * 8, y0=b.bbox.y0,
                                             x1=b.bbox.x0 + i * 8 + 8, y1=b.bbox.y1)) for i, char in enumerate(text)]
            blocks.append(b)
    if table:
        merged = block("", kind="table")
        for b in blocks:
            offset = len(merged.text)
            merged.char_boxes.extend(c.model_copy(update={"text_index": c.text_index + offset}) for c in b.char_boxes)
            merged.text += b.text + "\n"
        blocks = [merged]
    return document(blocks).pages[0]


def test_signing_field_equivalence_preserves_party_binding():
    original = signing_page(table=True)
    assert equivalent_printed_fields(original, signing_page())
    assert not equivalent_printed_fields(original, signing_page(accounts=("222202", "111101")))
    assert not equivalent_printed_fields(original, signing_page(accounts=("111102", "222202")))
    assert not equivalent_printed_fields(original, signing_page(address="地址二"))
    assert not equivalent_printed_fields(original, document([]).pages[0])


def test_word_level_geometry_never_collapses_repeated_account_digits():
    page = signing_page(table=True)
    b = page.blocks[0]
    start = b.text.index("111101")
    digits = [c for c in b.char_boxes if start <= c.text_index < start + 6]
    for c in digits:
        c.bbox = digits[0].bbox
    assert printed_fields(page)[0]["account"] == "111101"


def test_financial_ocr_repair_requires_independent_support_and_unchanged_value():
    from app.models import TextSourceEvidence
    left = "金额35000元，大写参万伍任元整。"
    right = left.replace("任", "仟")
    d = document([block(left)])
    diff = DiffItem(diff_id="money", diff_type="MODIFY", original_text=left, compare_text=right,
                    original_evidence=[EvidenceBox(page_no=1, bbox=d.pages[0].blocks[0].bbox)])
    processor = DiffQualityProcessor()
    assert not processor._corroborated_financial_ocr_equivalence(diff, d, None)
    d.pages[0].blocks[0].source_evidence = [TextSourceEvidence(source="ppstructure", page_no=1, raw_text=right, bbox=d.pages[0].blocks[0].bbox)]
    assert processor._corroborated_financial_ocr_equivalence(diff, d, None)
    diff.compare_text = right.replace("35000", "45000")
    assert not processor._corroborated_financial_ocr_equivalence(diff, d, None)


def test_visual_date_merge_retains_location_and_clears_body_ancestry():
    raw = DiffItem(diff_id="date", diff_type="ADD", title="签署日期", clause_no="8.5",
                   compare_text="8.5合同正文\n签字日期：2026年7月10日", compare_snippet="20260710")
    visual = DiffItem(diff_id="WHOLE-PAGE-6-D", diff_type="ADD", compare_text="2026年7月10日",
                      match_score_details={"object_type": "HANDWRITTEN_DATE"},
                      compare_evidence=[EvidenceBox(page_no=6, bbox=BBox(x0=100,y0=600,x1=200,y1=640))])
    diffs, remap = _reconcile_visual_signing_date_diffs([raw, visual])
    assert len(diffs) == 1 and remap[visual.diff_id] == raw.diff_id
    assert diffs[0].compare_evidence[0].page_no == 6
    assert diffs[0].clause_no == "" and diffs[0].compare_text == "2026年7月10日"


def test_visual_date_merge_does_not_replace_existing_signature_evidence():
    existing = [EvidenceBox(page_no=28, bbox=BBox(x0=100, y0=y, x1=200, y1=y + 20), text=text)
                for y, text in [(500, "签名"), (600, "2026年5月22日")]]
    raw = DiffItem(diff_id="date", diff_type="ADD", title="签署日期",
                   compare_text="签名及2026年5月22日", compare_evidence=existing)
    visual = DiffItem(diff_id="WHOLE-PAGE-28-D", diff_type="ADD", compare_text="2026年5月22日",
                      match_score_details={"object_type": "HANDWRITTEN_DATE"},
                      compare_evidence=[existing[1]])
    diffs, _ = _reconcile_visual_signing_date_diffs([raw, visual])
    assert len(diffs) == 1 and diffs[0].compare_evidence == existing
    assert diffs[0].compare_text == "签名及2026年5月22日"
