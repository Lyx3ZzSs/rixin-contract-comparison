"""Contract opening titles have one owner despite native/OCR line wrapping."""

import pytest

from app.models import CharBox
from app.services.clause_splitter import ClauseSplitter
from app.services.cover_metadata import CoverMetadataComparator
from app.services.document_role_repair import reconcile_interior_document_titles
from tests.test_cover_metadata import _block, _multi_page_document


@pytest.mark.parametrize("changed", [True, False])
def test_paired_multiline_title_has_single_metadata_owner(changed):
    left = ["国能长源随州发电有限公司随县分公司", "2026 年新能源场站功率预测系统授权服务合同"]
    right = (
        ["长源电力随州公司2026年新能源场站", "功率预测系统授权服务单一来源项目合同"]
        if changed
        else ["国能长源随州发电有限公司随县分公司2026年新能源场站", "功率预测系统授权服务合同"]
    )
    docs = []
    for side, titles in [("o", left), ("c", right)]:
        blocks = [
            _block(f"{side}{i}", t, 120, 80 + i * 32, 470, 102 + i * 32, "text" if side == "o" else "doc_title")
            for i, t in enumerate(titles)
        ]
        for block in blocks:
            step = (block.bbox.x1 - block.bbox.x0) / len(block.text)
            block.char_boxes = [
                CharBox(
                    char=c,
                    page_no=2,
                    text_index=i,
                    bbox=block.bbox.model_copy(
                        update={"x0": block.bbox.x0 + i * step, "x1": block.bbox.x0 + (i + 1) * step}
                    ),
                )
                for i, c in enumerate(block.text)
            ]
        buyer = "国能长源随州发电有限公司" + ("随县分公司" if side == "o" else "")
        blocks += [
            _block(f"{side}-buyer", f"甲方：{buyer}", 72, 150, 500, 168),
            _block(f"{side}-seller", "乙方：国能日新科技股份有限公司", 72, 175, 500, 193),
            _block(f"{side}-intro", "鉴于甲方委托乙方提供服务，双方订立本合同。", 72, 210, 500, 230),
            _block(f"{side}-body", "1. 定义", 72, 265, 150, 286, "paragraph_title"),
        ]
        docs.append(
            _multi_page_document([[_block(side + "cover", "新能源服务合同", 120, 220, 470, 250, "doc_title")], blocks])
        )
    reconcile_interior_document_titles(*docs)
    diffs = CoverMetadataComparator().build_diffs(*docs)
    title_diffs = [d for d in diffs if d.title == "前置标题（第2页）"]
    assert len(title_diffs) == int(changed)
    assert any(d.title == "前置字段（第2页）：甲方" for d in diffs)
    for doc, titles in zip(docs, [left, right]):
        clauses = ClauseSplitter().split(doc, "C")
        assert all(t not in c.text for c in clauses for t in titles)
        assert any("鉴于甲方" in c.text for c in clauses)
    if changed:
        diff = title_diffs[0]
        assert diff.diff_type == "MODIFY"
        for text, ranges, evidences in [
            (diff.original_text, diff.original_change_ranges, diff.original_evidence),
            (diff.compare_text, diff.compare_change_ranges, diff.compare_evidence),
        ]:
            assert ranges and evidences
            assert [(r.start, r.end, r.highlight_type) for r in ranges] == [(0, len(text), "MODIFY")]
            assert "\n".join(e.text for e in evidences) == text
            assert all(e.highlight_type == "MODIFY" and e.page_no == 2 for e in evidences)


def test_later_title_without_metadata_owner_stays_in_clause_comparison():
    docs = []
    for side in ("o", "c"):
        docs.append(
            _multi_page_document(
                [
                    [_block(side + "body", "1. 服务内容", 72, 100, 450, 122, "paragraph_title")],
                    [
                        _block(side + "title", "技术服务合同", 180, 80, 410, 105, "doc_title"),
                        _block(side + "buyer", "甲方：某公司", 72, 150, 450, 170),
                        _block(side + "seller", "乙方：另一公司", 72, 180, 450, 200),
                        _block(side + "intro", "鉴于双方约定提供技术服务。", 72, 220, 450, 240),
                    ],
                ]
            )
        )
    reconcile_interior_document_titles(*docs)
    for d in docs:
        clauses = ClauseSplitter().split(d, "C")
        assert any("技术服务合同" in c.text for c in clauses)
