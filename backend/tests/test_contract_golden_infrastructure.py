from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import fitz
import pytest

from tests.golden_contract.golden_loader import GoldenDataError, _verify_pdf, validate_execution
from tests.golden_contract.semantic_matcher import SemanticGoldenMatcher


def _actual(**overrides):
    actual = {
        "diff_type": "ADD",
        "source_type": "signing_region",
        "title": "甲方 · 法人代表或授权委托人",
        "original_text": "",
        "compare_text": "伟",
        "readable_change": "甲方 · 法人代表或授权委托人：新增手写填写；内容识别待确认",
        "quality_status": "NORMAL",
        "content_recognition_status": "NEEDS_REVIEW",
        "content_candidates": [{"text": "伟"}, {"text": "郑伟"}],
        "match_score_details": {"field_key": "legal_representative", "change_decision": "CONFIRMED"},
        "compare_evidence": [
            {"page_no": 5, "bbox": {"x0": 253.5, "y0": 275.5, "x1": 266.5, "y1": 304.5}}
        ],
    }
    actual.update(overrides)
    return actual


def _expected(**overrides):
    expected = {
        "case_id": "HANENG_AUTH_REP_HANDWRITING_ADD",
        "assertion": "MUST_REVIEW",
        "page": 5,
        "region": "signing",
        "party": "甲方",
        "field_type": "legal_representative",
        "diff_type": "ADD",
        "source_type": "signing_region",
        "title": "甲方 · 法人代表或授权委托人",
        "quality_status": "NORMAL",
        "content_recognition_status": "NEEDS_REVIEW",
        "change_decision": "CONFIRMED",
        "content_candidates_contain": ["伟", "郑伟"],
        "bbox_expectation": {
            "side": "compare",
            "target_bbox": {"x0": 253.5, "y0": 275.5, "x1": 266.5, "y1": 304.5},
            "min_iou": 0.35,
        },
    }
    expected.update(overrides)
    return expected


def test_failure_injection_missing_positive_is_detected() -> None:
    report = SemanticGoldenMatcher().match("HANENG_GONGHE", [_expected()], [])
    assert report.failures[0].failure_type == "FALSE_NEGATIVE_REGRESSION"


def test_failure_injection_forbidden_false_positive_is_detected() -> None:
    forbidden = _expected(
        case_id="LIANJIANG_FALSE_DATE",
        assertion="MUST_NOT_EXIST",
        page=13,
        party="乙方",
        field_type="date",
        title=None,
        quality_status=None,
        content_recognition_status=None,
        change_decision=None,
        content_candidates_contain=[],
        bbox_expectation=None,
        forbidden_value_pattern=r"2267[.]28京",
    )
    actual = _actual(
        title="乙方 · 签订日期",
        compare_text="2267.28京",
        content_recognition_status="CONFIRMED",
        match_score_details={"field_key": "date", "change_decision": "CONFIRMED"},
        compare_evidence=[{"page_no": 13, "bbox": {"x0": 400, "y0": 500, "x1": 480, "y1": 520}}],
    )
    report = SemanticGoldenMatcher().match("LIANJIANG_PINGPO", [forbidden], [actual])
    assert report.failures[0].failure_type == "FALSE_POSITIVE_REGRESSION"


def test_failure_injection_decision_regression_is_detected() -> None:
    actual = _actual()
    actual["match_score_details"]["change_decision"] = "NEEDS_REVIEW"
    report = SemanticGoldenMatcher().match("HANENG_GONGHE", [_expected()], [actual])
    assert report.failures[0].failure_type == "DECISION_REGRESSION"


def test_failure_injection_bbox_regression_is_detected() -> None:
    actual = _actual(compare_evidence=[{"page_no": 5, "bbox": {"x0": 80, "y0": 275, "x1": 180, "y1": 305}}])
    report = SemanticGoldenMatcher().match("HANENG_GONGHE", [_expected()], [actual])
    assert report.failures[0].failure_type == "BBOX_REGRESSION"


def test_failure_injection_unexpected_confirmed_diff_is_detected() -> None:
    unexpected = _actual(
        title="未知确认差异",
        quality_status="NORMAL",
        content_recognition_status="CONFIRMED",
        match_score_details={"change_decision": "CONFIRMED"},
    )
    report = SemanticGoldenMatcher().match("HANENG_GONGHE", [_expected()], [_actual(), unexpected])
    assert any(item.failure_type == "UNEXPECTED_CONFIRMED_DIFF" for item in report.failures)


def test_new_needs_review_diff_is_warning_not_blocking() -> None:
    uncertain = _actual(
        title="新的待复核候选",
        quality_status="NEEDS_REVIEW",
        content_recognition_status="NEEDS_REVIEW",
        match_score_details={"change_decision": "CANDIDATE"},
    )
    report = SemanticGoldenMatcher().match("HANENG_GONGHE", [_expected()], [_actual(), uncertain])
    assert not report.failures
    assert report.warnings


def test_one_actual_cannot_satisfy_two_positive_cases() -> None:
    second = deepcopy(_expected(case_id="HANENG_SECOND_PHYSICAL_SIGNATURE"))
    report = SemanticGoldenMatcher().match("HANENG_GONGHE", [_expected(), second], [_actual()])
    assert report.matched_positive == 1
    assert any(item.failure_type == "FALSE_NEGATIVE_REGRESSION" for item in report.failures)


def test_missing_pdf_fails_instead_of_skipping(tmp_path: Path) -> None:
    with pytest.raises(GoldenDataError, match="PDF missing"):
        _verify_pdf(
            tmp_path,
            {"file": "missing.pdf", "sha256": "0" * 64, "pages": 1},
            contract_key="MISSING",
            side="original",
        )


def test_modified_pdf_hash_fails(tmp_path: Path) -> None:
    path = tmp_path / "modified.pdf"
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()
    with pytest.raises(GoldenDataError, match="SHA256 mismatch"):
        _verify_pdf(
            tmp_path,
            {"file": path.name, "sha256": "0" * 64, "pages": 1},
            contract_key="MODIFIED",
            side="compare",
        )


def test_missing_executed_contract_fails() -> None:
    expected = [f"CONTRACT_{index}" for index in range(15)]
    with pytest.raises(GoldenDataError, match="all 15 contracts"):
        validate_execution(expected, expected, expected[:-1])


def _v2_cases(filename):
    import json
    return json.loads((Path(__file__).parent / 'fixtures/contract_golden/oracle' / filename).read_text())['assertions']


def test_v2_aggregated_seal_still_requires_each_original_page_box():
    cases=[c for c in _v2_cases('haneng_gonghe.json') if c.get('object_type')=='EDGE_SEAM_SEAL']
    actual=_actual(source_type='seal',title='乙方 · 骑缝章',compare_text='骑缝章',
                   quality_status='NEEDS_REVIEW',content_recognition_status='CONFIRMED',
                   match_score_details={'object_type':'EDGE_SEAM_SEAL'},
                   compare_evidence=[{'page_no':c['page'],'bbox':c['bbox_expectation']['target_bbox']} for c in cases])
    SemanticGoldenMatcher().match('HANENG_GONGHE',cases,[actual]).assert_passed()
    actual['compare_evidence'][1]['page_no']=99
    assert SemanticGoldenMatcher().match('HANENG_GONGHE',cases,[actual]).failures


def test_v2_partial_signature_candidate_does_not_excuse_conflicting_presentation():
    case=next(c for c in _v2_cases('haneng_gonghe.json') if c['case_id'].endswith('A1EB09EADF'))
    actual=_actual(compare_text='手写填写（姓名未可靠识别）',quality_status='NORMAL',
                   section_type='signature:field',readable_change='空白 → 未观察',
                   match_score_details={'field_key':'legal_representative','change_decision':'CONFIRMED'})
    assert SemanticGoldenMatcher().match('HANENG_GONGHE',[case],[actual]).failures
    actual['readable_change']='新增手写填写；内容识别待确认'
    SemanticGoldenMatcher().match('HANENG_GONGHE',[case],[actual]).assert_passed()


def test_v2_underlined_one_and_existing_heading_remain_forbidden_changes():
    cases=_v2_cases('suizhou_changyuan.json')
    for suffix,page,before,after in [('4736BD8B27',11,'第一种','第二种'),
                                     ('EXISTING_OBLIGATIONS_HEADING_NOT_ADD',4,'','5. 双方义务')]:
        case=next(c for c in cases if c['case_id'].endswith(suffix))
        actual=_actual(source_type='clause',diff_type=case['diff_type'],title='任意展示标题',
                       original_text=before,compare_text=after,
                       compare_evidence=[{'page_no':page,'bbox':{'x0':60,'y0':700,'x1':150,'y1':730}}])
        assert any(f.failure_type=='FALSE_POSITIVE_REGRESSION' for f in
                   SemanticGoldenMatcher().match('SUIZHOU_CHANGYUAN',[case],[actual]).failures)
