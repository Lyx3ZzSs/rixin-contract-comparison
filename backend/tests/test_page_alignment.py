from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.page_alignment import PageAlignmentStatus, PageSequenceAligner
from app.services.page_diff import PageDiffConsolidator
from app.services.diff_quality import DiffQualityProcessor
from app.services.table_compare import TableComparator


@pytest.mark.parametrize("cache_size", [0, 1, 50_000])
def test_alignment_cache_preserves_scores_anchors_and_split_merge(cache_size):
    left = _document([_body("首页"), _body("附件前段"), _body("附件后段"), _body("付款")])
    right = _document([_body("首页"), _body("附件前段") + _body("附件后段"), _body("付款")])
    expected = PageSequenceAligner(cache_size=0).align(left, right).to_debug_payload()
    actual = PageSequenceAligner(cache_size=cache_size).align(left, right)
    assert actual.to_debug_payload() == expected
    assert actual.performance["counters"]["similarity_cache_currsize"] <= cache_size


def test_alignment_cache_does_not_survive_document_mutation_or_concurrent_calls():
    aligner = PageSequenceAligner()
    original, compare = _document([_body("验收")]), _document([_body("验收")])
    aligner.align(original, compare)
    compare.pages[0].blocks[0].text = _body("付款")
    cases = [(original, compare), (compare, original), (_document(["tide"]), _document(["diet"]))]
    expected = [PageSequenceAligner(cache_size=0).align(*pair).to_debug_payload() for pair in cases]
    with ThreadPoolExecutor(max_workers=3) as executor:
        actual = list(executor.map(lambda pair: aligner.align(*pair).to_debug_payload(), cases))
    assert actual == expected
    assert "_similarity" not in vars(aligner)


def test_alignment_similarity_keeps_direction():
    from functools import lru_cache

    ratio = lru_cache(maxsize=10)(PageSequenceAligner._text_ratio)
    assert ratio("tide", "diet") != ratio("diet", "tide")


def test_rejected_page_groups_do_not_compute_combined_similarity(monkeypatch):
    aligner = PageSequenceAligner()
    parts = tuple(aligner._fingerprint((_page(i + 1, text),)) for i, text in enumerate(
        ("甲" * 40, "乙" * 40),
    ))
    target = aligner._fingerprint((_page(1, "丙" * 40),))

    def forbidden(*args):
        raise AssertionError("A rejected group must not construct combined text")

    monkeypatch.setattr(aligner, "_combine", forbidden)
    assert not aligner._group_supported(parts, target)


def test_dynamic_alignment_checks_group_support_before_combining(monkeypatch):
    aligner = PageSequenceAligner()
    monkeypatch.setattr(aligner, "_group_supported", lambda *args: False)

    def forbidden(*args):
        raise AssertionError("Unsupported merge/split must not be evaluated")

    monkeypatch.setattr(aligner, "_combine", forbidden)
    original = _document([_body("首页"), _body("付款")])
    result = aligner.align(original, original)
    assert _mapping(result) == [("MATCHED", (1,), (1,)), ("MATCHED", (2,), (2,))]


def _page(page_no: int, *texts: str, block_type: str = "text") -> Page:
    blocks = [
        TextBlock(
            block_id=f"p{page_no}b{index}",
            page_no=page_no,
            text=text,
            bbox=BBox(x0=60, y0=80 + index * 260, x1=540, y1=160 + index * 260),
            block_type=block_type,
        )
        for index, text in enumerate(texts)
    ]
    return Page(page_no=page_no, width=600, height=800, blocks=blocks)


def _document(texts: list[str]) -> Document:
    pages = [_page(index, text) for index, text in enumerate(texts, start=1)]
    return Document(filename="test.pdf", path="test.pdf", page_count=len(pages), pages=pages)


def _body(label: str) -> str:
    return f"{label}：双方确认本页约定的服务范围、验收标准、付款条件和违约责任。{label}专属内容不得与其他页面混同。"


def _mapping(result) -> list[tuple[str, tuple[int, ...], tuple[int, ...]]]:
    return [(entry.status.value, entry.original_pages, entry.compare_pages) for entry in result.entries]


def test_identical_five_page_documents_align_one_to_one() -> None:
    pages = [_body(f"第{index}页") for index in range(1, 6)]

    result = PageSequenceAligner().align(_document(pages), _document(pages))

    assert _mapping(result) == [("MATCHED", (index,), (index,)) for index in range(1, 6)]


def test_middle_two_pages_merge_into_one() -> None:
    first_half = "项目结算书前半页：工程名称、结算总价、承包单位和项目负责人等基本信息。"
    second_half = "项目结算书后半页：编制时间、建设单位审核项、承包单位签章以及确认日期。"
    original = _document([_body("首页"), first_half, second_half, _body("后续条款")])
    compare = _document([_body("首页"), first_half + second_half, _body("后续条款")])

    result = PageSequenceAligner().align(original, compare)

    assert ("MERGED", (2, 3), (2,)) in _mapping(result)


def test_one_page_splits_into_two() -> None:
    first_half = "附件验收单前半页：项目名称、合同编号、服务期间与验收内容。"
    second_half = "附件验收单后半页：验收意见、经办人员、负责人签字与填写日期。"
    original = _document([_body("首页"), first_half + second_half, _body("后续条款")])
    compare = _document([_body("首页"), first_half, second_half, _body("后续条款")])

    result = PageSequenceAligner().align(original, compare)

    assert ("SPLIT", (2,), (2, 3)) in _mapping(result)


def test_real_middle_page_deletion_is_classified_deleted() -> None:
    deleted = "独有网络安全专项约定：乙方必须提交只属于本项目的隔离方案、密钥清单和应急演练记录。"
    original = _document([_body("首页"), deleted, _body("付款条款"), _body("签署页")])
    compare = _document([_body("首页"), _body("付款条款"), _body("签署页")])

    result = PageSequenceAligner().align(original, compare)

    entry = result.entry_for_original(2)
    assert entry is not None
    assert entry.status == PageAlignmentStatus.DELETED
    assert not entry.compare_pages


def test_real_middle_page_insertion_is_classified_inserted() -> None:
    inserted = "新增数据安全附件：甲方要求乙方提交新增的访问控制矩阵、审计日志和恢复演练结果。"
    original = _document([_body("首页"), _body("付款条款"), _body("签署页")])
    compare = _document([_body("首页"), inserted, _body("付款条款"), _body("签署页")])

    result = PageSequenceAligner().align(original, compare)

    entry = result.entry_for_compare(2)
    assert entry is not None
    assert entry.status == PageAlignmentStatus.INSERTED
    assert not entry.original_pages


def test_all_pages_after_merge_keep_one_page_offset() -> None:
    first_half = "结算附件前段：工程名称结算总价承包单位项目负责人以及基本资料。"
    second_half = "结算附件后段：编制时间审核项目建设单位签字盖章以及归档要求。"
    tail = [_body(f"附件后续{index}") for index in range(1, 5)]
    original = _document([_body("首页"), first_half, second_half, *tail])
    compare = _document([_body("首页"), first_half + second_half, *tail])

    result = PageSequenceAligner().align(original, compare)

    assert result.entry_for_original(4).compare_pages == (3,)
    assert result.entry_for_original(7).compare_pages == (6,)


def test_similar_attachment_pages_do_not_cross_match() -> None:
    template = "附件通用表格：项目名称合同编号服务期间验收结论经办人员负责人签字日期。"
    original = _document([template + "表单身份甲场站", template + "表单身份乙场站"])
    compare = _document([template + "表单身份甲场站", template + "表单身份乙场站"])

    result = PageSequenceAligner().align(original, compare)

    assert result.entry_for_original(1).compare_pages == (1,)
    assert result.entry_for_original(2).compare_pages == (2,)


def test_poor_ocr_with_stable_heading_and_layout_still_matches() -> None:
    original = Document(
        filename="o.pdf",
        path="o.pdf",
        page_count=1,
        pages=[_page(1, "项目结算书", "工 程 名 称 结 算 总 价 承 包 单 位", block_type="heading")],
    )
    compare = Document(
        filename="c.pdf",
        path="c.pdf",
        page_count=1,
        pages=[_page(1, "项目结算书", "工程名称 结算总价 承包单位", block_type="heading")],
    )

    result = PageSequenceAligner().align(original, compare)

    assert result.entries[0].status == PageAlignmentStatus.MATCHED
    assert result.entries[0].anchors


def test_uncertain_alignment_cannot_confirm_whole_page_delete() -> None:
    text = "清晰正文但字符数量足以形成页面候选。"
    original_page = _page(1, text, text + "第二段用于证明完整页面覆盖。")
    original = Document(filename="o.pdf", path="o.pdf", page_count=1, pages=[original_page])
    compare = _document([""])
    alignment = PageSequenceAligner().align(original, compare)
    evidence = EvidenceBox(
        page_no=1,
        bbox=BBox(x0=60, y0=80, x1=540, y1=420),
        method="clause",
        text="清晰正文",
        highlight_type="DELETE",
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="DELETE",
        source_type="clause",
        original_text=original.pages[0].blocks[0].text,
        original_evidence=[evidence],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [diff], page_alignment=alignment)

    assert all(item.quality_status == "NEEDS_REVIEW" for item in result if item.source_type == "page")
    assert all(item.match_score_details.get("change_decision") != "CONFIRMED" for item in result if item.source_type == "page")


def test_confirmed_real_page_delete_can_still_be_reported() -> None:
    original = Document(
        filename="o.pdf",
        path="o.pdf",
        page_count=2,
        pages=[
            _page(1, _body("保留首页")),
            _page(
                2,
                "量子测风设备专项清单包含传感器序列编码、北斗授时参数、隔离网闸端口和离线校验摘要。",
                "该专项材料只属于原合同，另含应急密钥封存编号、灾备演练批次、独立交接记录和专属设备验收结论。",
            ),
        ],
    )
    compare = _document([_body("保留首页")])
    alignment = PageSequenceAligner().align(original, compare)
    deleted_page = original.pages[1]
    evidence = EvidenceBox(
        page_no=2,
        bbox=BBox(x0=60, y0=80, x1=540, y1=420),
        method="table_cell",
        text=deleted_page.blocks[0].text,
        highlight_type="DELETE",
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="DELETE",
        source_type="table",
        original_text=deleted_page.blocks[0].text,
        original_evidence=[evidence],
    )

    result = PageDiffConsolidator().consolidate(original, compare, [diff], page_alignment=alignment)

    page_diffs = [item for item in result if item.source_type == "page"]
    assert len(page_diffs) == 1
    assert page_diffs[0].diff_type == "DELETE"
    assert page_diffs[0].quality_status == "NORMAL"
    assert page_diffs[0].match_score_details["change_decision"] == "CONFIRMED"


def test_unmatched_table_on_aligned_page_is_retained_as_review_candidate() -> None:
    document = _document([_body("签署页")])
    alignment = PageSequenceAligner().align(document, document)
    comparator = TableComparator()
    comparator._matcher.page_alignment = alignment
    diff = DiffItem(
        diff_id="D001",
        diff_type="DELETE",
        source_type="table",
        original_text="签署页中的结构化表格",
    )

    comparator._mark_aligned_unmatched_table_candidate(diff, 1, deleted=True)

    assert diff.quality_status == "NEEDS_REVIEW"
    assert diff.match_score_details["change_decision"] == "CANDIDATE"
    assert diff.match_score_details["decision_reason"] == "ALIGNED_PAGE_STRUCTURE_NOT_EXTRACTED"

    result = DiffQualityProcessor().process([diff])

    assert result.diffs[0].quality_status == "NEEDS_REVIEW"
    assert result.diffs[0].match_score_details["change_decision"] == "CANDIDATE"
