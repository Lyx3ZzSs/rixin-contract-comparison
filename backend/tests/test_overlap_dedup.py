from app.models import BBox, DiffItem, EvidenceBox, TextRange
from app.services.diff.overlap_dedup import deduplicate_overlaps


def _evidence(text: str, highlight_type: str) -> EvidenceBox:
    return EvidenceBox(
        page_no=7,
        bbox=BBox(x0=40, y0=300, x1=540, y1=560),
        method="signing_region",
        text=text,
        highlight_type=highlight_type,
        confidence=0.9,
        evidence_quality="HIGH",
    )


def test_overlap_dedup_preserves_signing_region_modify_evidence() -> None:
    original_text = "甲方：【南京国电南自电网自动化有限公司】\n乙方：【国能日新科技股份有限公司】（盖章）"
    compare_text = "甲方：【南京国电南自电网自动化有限公司】（盖章）\n乙方：【国能日新科技股份有限公司】（盖章）"
    signing_diff = DiffItem(
        diff_id="D007",
        diff_type="MODIFY",
        source_type="signing_region",
        title="签署区（第7页）",
        original_text=original_text,
        compare_text=compare_text,
        original_snippet=original_text,
        compare_snippet=compare_text,
        original_change_ranges=[TextRange(start=0, end=len(original_text), highlight_type="MODIFY")],
        compare_change_ranges=[TextRange(start=0, end=len(compare_text), highlight_type="MODIFY")],
        original_evidence=[_evidence(original_text, "MODIFY")],
        compare_evidence=[_evidence(compare_text, "MODIFY")],
    )
    overlapping_add = DiffItem(
        diff_id="D010",
        diff_type="ADD",
        source_type="clause",
        title="国能日新科技股份有限公司",
        compare_text=original_text,
        compare_snippet=original_text,
        compare_evidence=[
            EvidenceBox(
                page_no=8,
                bbox=BBox(x0=60, y0=70, x1=360, y1=90),
                method="char_exact",
                text=original_text,
                highlight_type="ADD",
            )
        ],
    )

    result = deduplicate_overlaps([signing_diff, overlapping_add])

    deduped_signing = next(diff for diff in result if diff.diff_id == "D007")
    assert deduped_signing.original_snippet == original_text
    assert deduped_signing.original_evidence == signing_diff.original_evidence


def test_overlap_dedup_does_not_shrink_signing_add_to_missing_clause_number() -> None:
    signing_diff = DiffItem(
        diff_id="D068",
        diff_type="ADD",
        source_type="signing_region",
        section_type="signature:field",
        title="甲方 · 签订时间",
        compare_text="2026年5月15日",
        compare_snippet="2026年5月15日",
        readable_change="甲方 · 签订时间：空白 → 2026年5月15日",
        compare_evidence=[_evidence("2026年5月15日", "ADD")],
    )
    clause_diff = DiffItem(
        diff_id="D090",
        diff_type="MODIFY",
        source_type="clause",
        original_text="签订时间：甲方 · 签订时间",
        compare_text="签订时间：2026年5月15日",
        original_evidence=[_evidence("甲方 · 签订时间", "DELETE")],
    )

    result = deduplicate_overlaps([signing_diff, clause_diff])

    preserved = next(diff for diff in result if diff.diff_id == "D068")
    assert preserved.compare_snippet == "2026年5月15日"
    assert preserved.readable_change == "甲方 · 签订时间：空白 → 2026年5月15日"


def test_unrelated_same_page_sentence_cannot_erase_single_character_evidence():
    from app.services.diff.overlap_dedup import text_is_contained
    assert not text_is_contained('符合国家电投质量验收标准。', '合')
    evidence = _evidence('合', 'MODIFY')
    mod = DiffItem(diff_id='M', diff_type='MODIFY', source_type='clause',
                   compare_text='合增值税', compare_snippet='合',
                   compare_change_ranges=[TextRange(start=0, end=1, highlight_type='MODIFY')],
                   compare_evidence=[evidence])
    delete = DiffItem(diff_id='D', diff_type='DELETE', source_type='clause',
                     original_text='2.合作方式\n符合国家电投质量验收标准。',
                     original_evidence=[_evidence('符合国家电投质量验收标准。', 'DELETE')])
    result = next(d for d in deduplicate_overlaps([mod, delete]) if d.diff_id == 'M')
    assert result.compare_evidence == [evidence]
    assert result.compare_change_ranges == mod.compare_change_ranges


def test_overlap_evidence_and_ranges_use_the_same_verified_characters():
    from app.services.diff.overlap_dedup import remove_compare_overlap
    shared = '符合国家电投质量验收标准并履行全部合同义务。'
    text = '合增值税。' + shared
    mod = DiffItem(diff_id='M', diff_type='MODIFY', source_type='clause',
                   compare_text=text,
                   compare_change_ranges=[TextRange(start=0, end=len(text), highlight_type='MODIFY')],
                   compare_evidence=[_evidence('合', 'MODIFY'), _evidence(shared, 'MODIFY')])
    delete = DiffItem(diff_id='D', diff_type='DELETE', source_type='clause', original_text=shared)
    result = remove_compare_overlap(mod, ['D'], {'D': delete}, {'D': shared})
    assert result.compare_snippet == '合增值税。'
    assert result.compare_evidence == [mod.compare_evidence[0]]


def test_partial_line_evidence_survives_when_it_contains_remaining_changes():
    from app.services.diff.overlap_dedup import trim_overlap
    shared = '本合同服务费用已经包含交通材料人工以及其他全部费用。'
    text = shared + '新增现场服务。'
    diff = DiffItem(diff_id='A', diff_type='ADD', source_type='clause', compare_text=text,
                    compare_change_ranges=[TextRange(start=0, end=len(text), highlight_type='ADD')],
                    compare_evidence=[_evidence(text, 'ADD')])
    result = trim_overlap(diff, 'compare', {shared})
    assert result.compare_snippet == '新增现场服务。'
    assert result.compare_evidence == diff.compare_evidence


def test_original_side_evidence_uses_the_same_range_coverage_rule():
    from app.services.diff.overlap_dedup import remove_delete_overlap
    shared = '符合国家电投质量验收标准并履行全部合同义务。'
    text = '合增值税。' + shared
    mod = DiffItem(diff_id='M', diff_type='MODIFY', source_type='clause', original_text=text,
        original_change_ranges=[TextRange(start=0,end=len(text),highlight_type='MODIFY')],
        original_evidence=[_evidence('合','MODIFY'),_evidence(shared,'MODIFY')])
    add = DiffItem(diff_id='A',diff_type='ADD',source_type='clause',compare_text=shared)
    result = remove_delete_overlap(mod,['A'],{'A':add},{'A':shared})
    assert result.original_snippet == '合增值税。'
    assert result.original_evidence == [mod.original_evidence[0]]


def test_containment_does_not_depend_on_rapidfuzz_availability(monkeypatch):
    from app.services.diff import overlap_dedup
    pairs = [('符合国家电投质量验收标准。','合'),('甲方要求乙方履行合同约定的全部责任义务。','补充：甲方要求乙方履行合同约定的全部责任义务。')]
    before = [overlap_dedup.text_is_contained(a,b) for a,b in pairs]
    monkeypatch.setattr(overlap_dedup,'rfuzz',None)
    assert [overlap_dedup.text_is_contained(a,b) for a,b in pairs] == before == [False,True]
