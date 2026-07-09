from __future__ import annotations

import httpx

from app.models import Clause
from app.services.diff_engine import DiffEngine
from app.services.matcher import ClauseMatcher, MatchCandidate, SemanticMatcher
from app.services.normalizer import TextNormalizer


normalizer = TextNormalizer()


def clause(
    clause_id: str,
    clause_no: str,
    title: str,
    body: str,
    *,
    section_type: str = "main_contract",
    clause_key: str = "",
    split_flags: list[str] | None = None,
) -> Clause:
    text = f"{clause_no}. {title}\n{body}" if clause_no else f"{title}\n{body}"
    return Clause(
        clause_id=clause_id,
        clause_no=clause_no,
        title=title,
        text=text,
        normalized_text=normalizer.normalize_for_diff(text),
        match_text=normalizer.normalize_for_match(text),
        section_type=section_type,
        clause_key=clause_key,
        split_flags=split_flags or [],
    )


def filler(prefix: str, index: int) -> Clause:
    return clause(
        f"{prefix}{index:03d}",
        str(index),
        f"Standard clause {index}",
        f"This unrelated standard provision controls administrative item {index} and no payment terms.",
    )


def candidate(left: Clause, right: Clause, score: float, *, method: str = "body_weighted_similarity") -> MatchCandidate:
    return MatchCandidate(
        left,
        right,
        score,
        method,
        {
            "clause_key_score": 0.0,
            "canonical_path_score": 0.0,
            "clause_no_score": 0.0,
            "section_score": 100.0,
            "section_mismatch_candidate": 0.0,
            "section_mismatch_score": 0.0,
            "title_score": 0.0,
            "position_score": 0.0,
            "neighbor_score": 0.0,
            "business_token_score": 100.0,
            "business_token_mismatch": 0.0,
            "weak_numeric_marker": 0.0,
            "short_clause_pair": 0.0,
            "semantic_score": 0.0,
            "semantic_rerank_available": 0.0,
            "semantic_rerank_score": 0.0,
            "semantic_rerank_reason": "",
            "semantic_rerank_reason_present": 0.0,
            "rerank_available": 0.0,
            "rerank_score": 0.0,
            "rerank_reason_present": 0.0,
            "assignment_strategy_optimal": 0.0,
            "body_ratio_score": score,
            "body_token_score": score,
            "body_token_capped_score": score,
            "body_partial_score": score,
            "body_length_coverage": 1.0,
            "body_score": score,
        },
    )


class ControlledCandidateMatcher(ClauseMatcher):
    def __init__(self, canned_candidates: list[MatchCandidate], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.canned_candidates = canned_candidates

    def _build_candidates(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        canned = []
        for item in self.canned_candidates:
            details = dict(item.details)
            details["assignment_strategy_optimal"] = 1.0 if self.assignment_strategy == "optimal" else 0.0
            canned.append(MatchCandidate(item.original, item.compare, item.score, item.method, details, item.sources))
        candidates = self._apply_rerank(canned)
        candidates.sort(
            key=lambda item: (
                item.score,
                item.details["body_score"],
                item.details["clause_no_score"],
                item.details["title_score"],
            ),
            reverse=True,
        )
        return candidates


def test_prefilter_uses_body_top_k_for_far_reordered_clause_without_same_number() -> None:
    original = [filler("O", index) for index in range(1, 16)]
    original[12] = clause(
        "O013",
        "13",
        "Service credits",
        "The supplier shall grant service credits equal to 1.5% of monthly fees when uptime is below 99.9%.",
    )
    compare = [filler("N", index) for index in range(1, 16)]
    compare[1] = clause(
        "N002",
        "B",
        "Service credit adjustments",
        "The supplier shall grant service credits equal to 1.5% of monthly fees when uptime is below 99.5%.",
    )

    pairs = ClauseMatcher().match(original, compare)
    matched = next(pair for pair in pairs if pair.original and pair.original.clause_id == "O013")

    assert matched.compare is not None
    assert matched.compare.clause_id == "N002"
    assert matched.score_details["candidate_source_body_top_k"] == 1.0


def test_clause_matcher_includes_alignment_diagnostics() -> None:
    original = [clause("O001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]
    compare = [clause("N001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]

    pair = ClauseMatcher().match(original, compare)[0]

    alignment = pair.score_details["alignment"]
    assert alignment["number_match"] is True
    assert alignment["critical_token_overlap"] == 1.0
    assert alignment["risk_flags"] == []
    assert pair.match_candidates[0]["score_details"]["alignment"]["number_match"] is True


def test_clause_matcher_marks_low_confidence_alignment_for_token_conflict() -> None:
    original = [clause("O001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]
    compare = [clause("N001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币5000元。")]

    pair = ClauseMatcher().match(original, compare)[0]

    flags = pair.score_details["alignment"]["risk_flags"]
    assert "CRITICAL_TOKEN_MISMATCH" in flags
    assert "POSSIBLE_CLAUSE_MISALIGNMENT" in flags
    assert pair.match_confidence == "LOW"


def test_same_number_low_body_similarity_is_low_confidence_guarded_match() -> None:
    original = [
        clause(
            "O001",
            "5.1",
            "验收",
            "甲方应在系统上线后十个工作日内完成验收并出具书面验收意见。",
        )
    ]
    compare = [
        clause(
            "N001",
            "5.1",
            "验收",
            "乙方逾期交付的,应按合同总价每日千分之一向甲方支付违约金。",
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert "SAME_NUMBER_LOW_BODY_SIMILARITY" in pair.score_details["matcher_risk_flags"]
    assert pair.match_confidence == "LOW"
    assert pair.match_method in {"same_clause_no_low_similarity", "same_clause_no_weighted"}


def test_body_only_alignment_risk_is_low_confidence() -> None:
    original = [
        clause(
            "O001",
            "",
            "付款",
            "甲方应在2026年6月30日前支付人民币1000元,逾期应承担违约责任。",
        )
    ]
    compare = [
        clause(
            "N001",
            "",
            "结算",
            "甲方应在2027年7月31日前支付人民币5000元,逾期应承担违约责任。",
        )
    ]

    pair = ClauseMatcher(threshold=70).match(original, compare)[0]

    assert pair.compare is not None
    assert pair.match_method == "body_weighted_similarity"
    assert "BODY_ONLY_ALIGNMENT_RISK" in pair.score_details["matcher_risk_flags"]
    assert "CRITICAL_TOKEN_CONFLICT" in pair.score_details["matcher_risk_flags"]
    assert pair.match_confidence == "LOW"


def test_clause_matcher_writes_matcher_guard_for_low_coverage_same_key() -> None:
    original = [
        clause(
            "O001",
            "",
            "签署页",
            "甲方:国家电网有限公司华北分部\n乙方:国能日新科技股份有限公司\n地址:北京市海淀区建材城中路2482号",
            section_type="main_contract",
            clause_key="main_contract/签署页",
        )
    ]
    compare = [
        clause(
            "N001",
            "",
            "签署页",
            "地址:北京市海淀区建材城中路2482号",
            section_type="main_contract",
            clause_key="main_contract/签署页",
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.score_details["matcher_risk_flags"] == ["SAME_KEY_LOW_BODY_COVERAGE"]
    assert pair.score_details["matcher_guard_applied"] == 1.0
    assert pair.match_candidates[0]["score_details"]["matcher_risk_flags"] == ["SAME_KEY_LOW_BODY_COVERAGE"]


def test_clause_matcher_writes_matcher_guard_for_critical_token_conflict() -> None:
    original = [clause("O001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]
    compare = [clause("N001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币5000元。")]

    pair = ClauseMatcher().match(original, compare)[0]

    assert "CRITICAL_TOKEN_CONFLICT" in pair.score_details["matcher_risk_flags"]
    assert pair.score_details["matcher_guard_applied"] == 1.0


def test_low_coverage_same_key_with_weak_body_and_title_is_not_accepted_by_key_only() -> None:
    original = [
        clause(
            "O001",
            "",
            "服务范围",
            "乙方应提供功率预测平台部署、模型训练、接口联调、历史数据迁移、验收支持和上线后运维服务。",
            clause_key="main_contract/服务范围",
        )
    ]
    compare = [
        clause(
            "N001",
            "",
            "项目联系人",
            "联系人:张三。",
            clause_key="main_contract/服务范围",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert {pair.match_method for pair in pairs} == {"delete", "add"}
    delete_pair = next(pair for pair in pairs if pair.match_method == "delete")
    assert delete_pair.match_candidates[0]["score_details"]["matcher_risk_flags"] == [
        "SAME_KEY_LOW_BODY_COVERAGE"
    ]


def test_critical_token_conflict_caps_score_but_keeps_modify_candidate() -> None:
    original = [clause("O001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]
    compare = [clause("N001", "3.1", "付款条款", "乙方应在2027年7月31日前支付人民币5000元。")]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert pair.score_details["body_score"] < 85
    assert pair.score_details["alignment"]["critical_token_overlap"] == 0.0
    assert pair.score <= 84.0
    assert "CRITICAL_TOKEN_CONFLICT" in pair.score_details["matcher_risk_flags"]
    assert pair.match_confidence == "LOW"


def test_high_similarity_critical_token_conflict_does_not_score_as_full_match() -> None:
    original = [clause("O001", "3.1", "付款条款", "甲方应在2026年6月30日前支付人民币1000元。")]
    compare = [clause("N001", "3.1", "付款条款", "甲方应在2027年7月31日前支付人民币5000元。")]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert pair.score_details["body_score"] >= 85
    assert pair.score_details["alignment"]["critical_token_overlap"] == 0.0
    assert "CRITICAL_TOKEN_CONFLICT" in pair.score_details["matcher_risk_flags"]
    assert pair.score <= 84.0
    assert pair.match_confidence == "LOW"


def test_optimal_assignment_prefers_two_good_pairs_over_one_greedy_pair() -> None:
    original = [
        clause("O001", "", "A", "Template service scope with payment support."),
        clause("O002", "", "B", "Distinct reporting obligation for monthly operations."),
    ]
    compare = [
        clause("N001", "", "A-like", "Ambiguous template candidate for both obligations."),
        clause("N002", "", "B-like", "Template service scope with payment support."),
    ]
    candidates = [
        candidate(original[0], compare[0], 95.0),
        candidate(original[0], compare[1], 86.0),
        candidate(original[1], compare[0], 85.0),
    ]

    greedy_pairs = ControlledCandidateMatcher(candidates, assignment_strategy="greedy").match(original, compare)
    optimal_pairs = ControlledCandidateMatcher(candidates, assignment_strategy="optimal").match(original, compare)

    greedy_matches = {
        (pair.original.clause_id, pair.compare.clause_id)
        for pair in greedy_pairs
        if pair.original is not None and pair.compare is not None
    }
    optimal_matches = {
        (pair.original.clause_id, pair.compare.clause_id)
        for pair in optimal_pairs
        if pair.original is not None and pair.compare is not None
    }
    optimal_match = next(pair for pair in optimal_pairs if pair.original and pair.original.clause_id == "O001")

    assert greedy_matches == {("O001", "N001")}
    assert optimal_matches == {("O001", "N002"), ("O002", "N001")}
    assert optimal_match.score_details["assignment_strategy_optimal"] == 1.0


def test_optimal_assignment_ignores_unacceptable_low_score_edges() -> None:
    original = [
        clause("O001", "", "付款", "Customer shall pay invoices within thirty days."),
        clause("O002", "", "服务", "Supplier shall provide platform maintenance services."),
    ]
    compare = [
        clause("N001", "", "付款", "Customer shall pay invoices within forty five days."),
        clause("N002", "", "无关", "A short unrelated administrative note."),
    ]
    candidates = [
        candidate(original[0], compare[0], 90.0),
        candidate(original[1], compare[1], 40.0),
    ]

    pairs = ControlledCandidateMatcher(candidates, assignment_strategy="optimal").match(original, compare)
    matches = {
        (pair.original.clause_id, pair.compare.clause_id)
        for pair in pairs
        if pair.original is not None and pair.compare is not None
    }

    assert matches == {("O001", "N001")}
    assert any(pair.match_method == "delete" and pair.original and pair.original.clause_id == "O002" for pair in pairs)
    assert any(pair.match_method == "add" and pair.compare and pair.compare.clause_id == "N002" for pair in pairs)


def test_optimal_assignment_falls_back_to_greedy_when_solver_fails(monkeypatch, caplog) -> None:
    original = [
        clause("O001", "", "A", "Payment support obligation."),
        clause("O002", "", "B", "Reporting support obligation."),
    ]
    compare = [
        clause("N001", "", "A-like", "Ambiguous template candidate."),
        clause("N002", "", "B-like", "Payment support obligation."),
    ]
    candidates = [
        candidate(original[0], compare[0], 95.0),
        candidate(original[0], compare[1], 86.0),
        candidate(original[1], compare[0], 85.0),
    ]

    def fail_solver(self: ClauseMatcher, edges: list[tuple[int, int, float]], *, left_count: int, right_count: int) -> set[tuple[int, int]]:
        raise RuntimeError("solver unavailable")

    monkeypatch.setattr("app.services.matcher.ClauseMatcher._max_weight_matching", fail_solver)

    pairs = ControlledCandidateMatcher(candidates, assignment_strategy="optimal").match(original, compare)
    matches = {
        (pair.original.clause_id, pair.compare.clause_id)
        for pair in pairs
        if pair.original is not None and pair.compare is not None
    }

    assert matches == {("O001", "N001")}
    assert "falling back to greedy selection" in caplog.text


def test_private_rerank_score_can_promote_recalled_candidate(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"score": 0.0}, {"score": 100.0, "reason": "same obligation"}]}

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
            calls.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
            return FakeResponse()

    monkeypatch.setattr("app.services.matcher.httpx.Client", FakeClient)
    original = [clause("O001", "", "付款", "甲方应在收到发票后三十日内付款。")]
    compare = [
        clause("N001", "", "结算", "甲方应在收到发票后四十五日内付款。"),
        clause("N002", "", "付款安排", "客户应在收到有效发票后三十日内完成付款。"),
    ]
    candidates = [
        candidate(original[0], compare[0], 88.0),
        candidate(original[0], compare[1], 78.0),
    ]

    pairs = ControlledCandidateMatcher(
        candidates,
        enable_rerank=True,
        rerank_base_url="http://rerank.local/v1",
        rerank_api_key="secret",
        rerank_model="contract-reranker",
        rerank_timeout_seconds=9,
        rerank_weight=0.5,
    ).match(original, compare)

    matched = next(pair for pair in pairs if pair.original is not None and pair.compare is not None)
    assert matched.compare.clause_id == "N002"
    assert matched.score_details["semantic_rerank_available"] == 1.0
    assert matched.score_details["semantic_rerank_score"] == 100.0
    assert matched.score_details["semantic_rerank_reason"] == "same obligation"
    assert matched.score_details["semantic_rerank_reason_present"] == 1.0
    assert matched.score_details["rerank_available"] == 1.0
    assert matched.score_details["rerank_score"] == 100.0
    assert matched.score_details["rerank_reason_present"] == 1.0
    assert calls[0]["url"] == "http://rerank.local/v1/rerank"
    assert calls[0]["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer secret"}
    assert calls[0]["json"]["model"] == "contract-reranker"
    assert calls[0]["timeout"] == 9


def test_private_rerank_failure_falls_back_to_rule_scores(monkeypatch, caplog) -> None:
    class FailingClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> FailingClient:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]) -> object:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.matcher.httpx.Client", FailingClient)
    original = [clause("O001", "", "付款", "甲方应在收到发票后三十日内付款。")]
    compare = [
        clause("N001", "", "结算", "甲方应在收到发票后四十五日内付款。"),
        clause("N002", "", "付款安排", "客户应在收到有效发票后三十日内完成付款。"),
    ]
    candidates = [
        candidate(original[0], compare[0], 88.0),
        candidate(original[0], compare[1], 78.0),
    ]

    matcher = ControlledCandidateMatcher(
        candidates,
        enable_rerank=True,
        rerank_base_url="http://rerank.local/v1",
        rerank_model="contract-reranker",
        rerank_max_retries=0,
        rerank_weight=0.5,
    )
    pairs = matcher.match(original, compare)

    matched = next(pair for pair in pairs if pair.original is not None and pair.compare is not None)
    assert matched.compare.clause_id == "N001"
    assert matched.score_details["semantic_rerank_available"] == 0.0
    assert matched.score_details["rerank_available"] == 0.0
    assert matcher.rerank_matcher.enabled is False
    assert "Clause rerank disabled" in caplog.text


def test_private_rerank_is_limited_to_top_k_candidates_per_original(monkeypatch) -> None:
    calls: list[list[dict[str, object]]] = []

    class FakeResponse:
        def __init__(self, count: int) -> None:
            self.count = count

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"score": 1.0, "reason": f"candidate-{index}"} for index in range(self.count)]}

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
            pairs = json["pairs"]
            assert isinstance(pairs, list)
            calls.append(pairs)
            return FakeResponse(len(pairs))

    monkeypatch.setattr("app.services.matcher.httpx.Client", FakeClient)
    original = [
        clause("O001", "", "付款", "Customer shall pay invoices within thirty days."),
        clause("O002", "", "服务", "Supplier shall provide platform maintenance services."),
    ]
    compare = [
        clause("N001", "", "候选1", "Candidate one."),
        clause("N002", "", "候选2", "Candidate two."),
        clause("N003", "", "候选3", "Candidate three."),
        clause("N004", "", "候选4", "Candidate four."),
        clause("N005", "", "候选5", "Candidate five."),
        clause("N006", "", "候选6", "Candidate six."),
    ]
    candidates = [
        candidate(original[0], compare[0], 91.0),
        candidate(original[0], compare[1], 90.0),
        candidate(original[0], compare[2], 89.0),
        candidate(original[1], compare[3], 88.0),
        candidate(original[1], compare[4], 87.0),
        candidate(original[1], compare[5], 86.0),
    ]

    pairs = ControlledCandidateMatcher(
        candidates,
        enable_rerank=True,
        rerank_base_url="http://rerank.local/v1",
        rerank_top_k=2,
        rerank_weight=0.01,
    ).match(original, compare)

    reranked_pairs = [
        pair
        for pair in pairs
        if pair.original is not None
        and pair.compare is not None
        and pair.score_details.get("semantic_rerank_available") == 1.0
    ]
    assert [len(call) for call in calls] == [2, 2]
    assert {pair.compare.clause_id for pair in reranked_pairs} <= {"N001", "N002", "N004", "N005"}


def test_matcher_keeps_match_when_title_changes_but_body_stays_similar() -> None:
    original = [
        clause(
            "O001",
            "1",
            "Payment terms",
            "Customer shall pay all invoices within 30 days after receipt of a valid invoice.",
        )
    ]
    compare = [
        clause(
            "N001",
            "A",
            "Settlement schedule",
            "Customer shall pay all invoices within 45 days after receipt of a valid invoice.",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert pairs[0].compare is not None
    assert pairs[0].compare.clause_id == "N001"
    assert pairs[0].score_details["body_score"] >= 70


def test_numeric_change_still_matches_and_reports_modify_diff() -> None:
    original = [
        clause(
            "O001",
            "1",
            "Late fee",
            "Party B shall pay a late fee of 1.5% of the overdue amount for each month of delay.",
        )
    ]
    compare = [
        clause(
            "N001",
            "1",
            "Late fee",
            "Party B shall pay a late fee of 15% of the overdue amount for each month of delay.",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)

    assert pairs[0].compare is not None
    assert diffs[0].diff_type == "MODIFY"
    assert "BUSINESS_TOKEN_MISMATCH_REVIEW" in diffs[0].review_flags


def test_formal_chinese_and_arabic_clause_numbers_match_as_same_number() -> None:
    original = [
        clause(
            "O011",
            "第十一条",
            "付款",
            "甲方应在收到合格发票后30日内付款。",
        )
    ]
    compare = [
        clause(
            "N011",
            "第11条",
            "付款",
            "甲方应在收到合格发票后45日内付款。",
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert pair.match_method == "same_clause_no_weighted"
    assert pair.match_confidence != "LOW"
    assert pair.score_details["clause_no_score"] == 100.0
    assert pair.score_details["weak_numeric_marker"] == 0.0
    assert pair.score_details["alignment"]["number_match"] is True
    assert pair.score_details["alignment"]["risk_flags"] == []


def test_equivalent_amount_formatting_does_not_trigger_critical_token_conflict() -> None:
    original = [
        clause(
            "O001",
            "3.1",
            "付款",
            "甲方应支付人民币1000.00元。",
        )
    ]
    compare = [
        clause(
            "N001",
            "3.1",
            "付款",
            "甲方应支付人民币1000元。",
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert pair.match_confidence != "LOW"
    assert "CRITICAL_TOKEN_CONFLICT" not in pair.score_details["matcher_risk_flags"]
    assert "CRITICAL_TOKEN_MISMATCH" not in pair.score_details["alignment"]["risk_flags"]


def test_match_score_is_capped_and_low_coverage_clause_key_match_is_reviewed() -> None:
    original = [
        clause(
            "O001",
            "",
            "签署页",
            "甲方:国家电网有限公司华北分部\n乙方:国能日新科技股份有限公司\n地址:北京市海淀区建材城中路2482号",
            section_type="main_contract",
            clause_key="main_contract/签署页",
        )
    ]
    compare = [
        clause(
            "N001",
            "",
            "签署页",
            "地址:北京市海淀区建材城中路2482号",
            section_type="main_contract",
            clause_key="main_contract/签署页",
        )
    ]

    pair = ClauseMatcher().match(original, compare)[0]
    diffs = DiffEngine().build_diffs([pair])

    assert pair.score <= 100
    assert pair.match_confidence in {"LOW", "MEDIUM"}
    assert "LOW_COVERAGE_CLAUSE_KEY_MATCH" in diffs[0].review_flags


def test_business_token_score_prefers_more_specific_template_candidate() -> None:
    original = [
        clause(
            "O001",
            "",
            "Late fee",
            "Party B shall pay a late fee of 1.5% of the overdue amount within 30 days after notice.",
        )
    ]
    compare = [
        clause(
            "N_BAD",
            "",
            "Late fee",
            "Party B shall pay a late fee of 15% of the overdue amount within 30 days after notice.",
        ),
        clause(
            "N_GOOD",
            "",
            "Late fee",
            "Party B shall pay a late fee of 1.5% of the overdue amount within 45 days after notice.",
        ),
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert pairs[0].compare is not None
    assert pairs[0].compare.clause_id == "N_GOOD"
    assert pairs[0].score_details["business_token_score"] > 40


def test_short_partial_overlap_does_not_create_false_match() -> None:
    original = [clause("O001", "", "", "Payment")]
    compare = [
        clause(
            "N001",
            "",
            "",
            "Payment terms are subject to a separate approval workflow and detailed settlement calendar.",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert len(pairs) == 2
    assert {pair.match_method for pair in pairs} == {"delete", "add"}


def test_weak_numeric_same_clause_number_does_not_override_unrelated_body() -> None:
    original = [
        clause(
            "O001",
            "2",
            "2.",
            "Party A shall provide daily weather forecast data to the project control center.",
            split_flags=["WEAK_NUMERIC_MARKER"],
        )
    ]
    compare = [
        clause(
            "N001",
            "2",
            "2.",
            "The supplier shall submit invoice copies and bank account records before payment.",
            split_flags=["WEAK_NUMERIC_MARKER"],
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert {pair.match_method for pair in pairs} == {"delete", "add"}


def test_matcher_does_not_match_different_document_sections_by_same_number() -> None:
    original = [
        clause(
            "O001",
            "1",
            "Service scope",
            "The service scope covers power forecast platform maintenance.",
            section_type="main_contract",
            clause_key="main_contract/1服务范围",
        )
    ]
    compare = [
        clause(
            "N001",
            "1",
            "Service scope",
            "The service scope covers power forecast platform maintenance.",
            section_type="quote",
            clause_key="quote/1服务范围",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert {pair.match_method for pair in pairs} == {"delete", "add"}


def test_section_mismatch_is_blocked_but_reported_as_candidate() -> None:
    original = [
        clause(
            "O001",
            "1",
            "Service scope",
            "The service scope covers power forecast platform maintenance.",
            section_type="main_contract",
            clause_key="main_contract/n1服务范围",
        )
    ]
    compare = [
        clause(
            "N001",
            "1",
            "Service scope",
            "The service scope covers power forecast platform maintenance.",
            section_type="quote",
            clause_key="quote/n1服务范围",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)

    delete_pair = next(pair for pair in pairs if pair.match_method == "delete")
    assert delete_pair.match_candidates
    assert delete_pair.match_candidates[0]["score_details"]["section_mismatch_candidate"] == 1.0
    assert any("POSSIBLE_SECTION_MISCLASSIFICATION" in diff.review_flags for diff in diffs)


def test_canonical_path_score_matches_chinese_and_arabic_section_paths() -> None:
    original = [
        clause(
            "O011",
            "第十一条",
            "付款",
            "甲方应在收到合格发票后30日内付款。",
            clause_key="main_contract/n11付款",
        ).model_copy(update={"section_path": ["第十一条 付款"]})
    ]
    compare = [
        clause(
            "N011",
            "第11条",
            "付款",
            "甲方应在收到合格发票后45日内付款。",
            clause_key="main_contract/n11付款",
        ).model_copy(update={"section_path": ["第11条 付款"]})
    ]

    pair = ClauseMatcher().match(original, compare)[0]

    assert pair.compare is not None
    assert pair.score_details["canonical_path_score"] == 100.0


def test_unmatched_original_contained_in_matched_compare_clause_is_not_deleted() -> None:
    supplement_text = "甲方在合同履行过程中,要求乙方提供合同约定范围之外的硬件设备,双方应签订书面补充协议。"
    permission_text = (
        "乙方须在系统部署完成后,向甲方提供完整的系统管理权限(含管理员账号、配置文件访问权限、"
        "日志查看权限),确保甲方在不依赖乙方人员的情况下,可独立完成:\n"
        "上报通道配置\n上报参数调整\n日志查看与问题定位\n配置备份与恢复"
    )
    original = [
        clause("O059", "14.2", "补充采购", supplement_text),
        clause("O061", "", "系统管理权限", permission_text),
    ]
    compare = [
        clause("N059", "14.2", "补充采购", f"{supplement_text}\n{permission_text}"),
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)
    contained = next(pair for pair in pairs if pair.original and pair.original.clause_id == "O061")

    assert contained.compare is not None
    assert contained.match_method == "contained_compare"
    assert contained.compare.clause_id.startswith("N059S")
    assert not any(diff.diff_type == "DELETE" and diff.original_clause_id == "O061" for diff in diffs)


def test_short_unmatched_original_contained_in_matched_compare_clause_is_not_deleted() -> None:
    base_text = "甲方应按合同约定验收服务成果。"
    short_text = "质保期12个月。"
    original = [
        clause("O001", "1", "验收", base_text),
        clause("O002", "", "质保期", short_text, split_flags=["PARAGRAPH_MERGED"]),
    ]
    compare = [
        clause("N001", "1", "验收", f"{base_text}\n{short_text}"),
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)
    contained = next(pair for pair in pairs if pair.original and pair.original.clause_id == "O002")

    assert contained.compare is not None
    assert contained.match_method == "contained_compare"
    assert not any(diff.diff_type == "DELETE" and diff.original_clause_id == "O002" for diff in diffs)


def test_multiple_original_clauses_merged_into_one_compare_clause_are_explicitly_matched() -> None:
    payment_text = "甲方应在收到合格发票后30日内完成付款。"
    invoice_text = "乙方应在付款前提供合法有效的增值税专用发票。"
    original = [
        clause("O001", "1", "付款", payment_text),
        clause("O002", "2", "发票", invoice_text),
    ]
    compare = [
        clause(
            "N001",
            "",
            "付款及发票",
            f"付款及发票\n{payment_text}\n{invoice_text}",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)
    matched_methods = {
        pair.original.clause_id: pair.match_method
        for pair in pairs
        if pair.original is not None and pair.compare is not None
    }

    assert matched_methods == {"O001": "merged_compare", "O002": "merged_compare"}
    assert not any(pair.match_method == "delete" for pair in pairs)
    assert not any(pair.match_method == "add" for pair in pairs)
    assert all(pair.score_details["merged_compare_clause"] == 1.0 for pair in pairs)
    assert all("POSSIBLE_MERGED_CLAUSE" in diff.review_flags for diff in diffs)
    assert all("PARTIAL_CLAUSE_MATCH" in diff.review_flags for diff in diffs)


def test_one_original_clause_split_into_multiple_compare_clauses_is_explicitly_matched() -> None:
    payment_text = "甲方应在收到合格发票后30日内完成付款。"
    invoice_text = "乙方应在付款前提供合法有效的增值税专用发票。"
    original = [
        clause(
            "O001",
            "",
            "付款及发票",
            f"付款及发票\n{payment_text}\n{invoice_text}",
        )
    ]
    compare = [
        clause("N001", "1", "付款", payment_text),
        clause("N002", "2", "发票", invoice_text),
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)
    matched_methods = {
        pair.compare.clause_id: pair.match_method
        for pair in pairs
        if pair.original is not None and pair.compare is not None
    }

    assert matched_methods == {"N001": "split_original", "N002": "split_original"}
    assert not any(pair.match_method == "delete" for pair in pairs)
    assert not any(pair.match_method == "add" for pair in pairs)
    assert all(pair.score_details["split_original_clause"] == 1.0 for pair in pairs)
    assert all("POSSIBLE_SPLIT_CLAUSE" in diff.review_flags for diff in diffs)
    assert all("PARTIAL_CLAUSE_MATCH" in diff.review_flags for diff in diffs)


def test_contained_compare_modify_is_flagged_as_segmentation_drift() -> None:
    supplement_text = "甲方在合同履行过程中,要求乙方提供合同约定范围之外的硬件设备,双方应签订书面补充协议。"
    original_permission = "乙方须向甲方提供完整的系统管理权限,并支持配置备份与恢复。"
    compare_permission = "乙方须向甲方提供完整的系统管理员权限,并支持配置备份与恢复。"
    original = [
        clause("O059", "14.2", "补充采购", supplement_text),
        Clause(
            clause_id="O061",
            clause_no="",
            title="系统管理权限",
            text=original_permission,
            normalized_text=normalizer.normalize_for_diff(original_permission),
            match_text=normalizer.normalize_for_match(original_permission),
        ),
    ]
    compare = [
        clause("N059", "14.2", "补充采购", f"{supplement_text}\n{compare_permission}"),
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)
    diff = next(item for item in diffs if item.original_clause_id == "O061")

    assert diff.diff_type == "MODIFY"
    assert diff.match_method == "contained_compare"
    assert "TEXT_FOUND_IN_OTHER_CLAUSE" in diff.review_flags
    assert "POSSIBLE_SEGMENTATION_DRIFT" in diff.review_flags


def test_openai_semantic_matcher_calls_embeddings_endpoint(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
            calls.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
            inputs = json["input"]
            assert isinstance(inputs, list)
            data = []
            for text in inputs:
                vector = [1.0, 0.0] if "付款" in str(text) else [0.0, 1.0]
                data.append({"embedding": vector})
            return FakeResponse({"data": data})

    monkeypatch.setattr("app.services.matcher.httpx.Client", FakeClient)

    matcher = SemanticMatcher(
        enabled=True,
        provider="openai",
        base_url="http://embedding.local/v1",
        api_key="secret",
        model="bge-small-zh-v1.5",
        timeout_seconds=12,
    )
    original = clause("O001", "1", "付款条款", "甲方应在收到发票后三十日内付款。")
    compare = [
        clause("N001", "A", "服务范围", "乙方提供平台维护服务。"),
        clause("N002", "B", "付款安排", "客户应在收到有效发票后三十日内完成付款。"),
    ]

    choices = matcher.prepare(compare)
    top = matcher.top_k(original, compare, choices, limit=1, score_cutoff=60)

    assert top == [1]
    assert matcher.score(original, compare[1], choices[1]) == 100.0
    assert calls[0]["url"] == "http://embedding.local/v1/embeddings"
    assert calls[0]["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer secret"}
    assert calls[0]["json"] == {
        "model": "bge-small-zh-v1.5",
        "input": [SemanticMatcher._semantic_text(item) for item in compare],
    }
    assert calls[0]["timeout"] == 12


def test_openai_semantic_matcher_disables_on_http_error(monkeypatch, caplog) -> None:
    class FailingClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> FailingClient:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]) -> object:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.matcher.httpx.Client", FailingClient)
    matcher = SemanticMatcher(
        enabled=True,
        provider="openai",
        base_url="http://embedding.local/v1",
        model="bge-small-zh-v1.5",
        max_retries=0,
    )

    vectors = matcher.prepare([clause("N001", "1", "付款", "收到发票后三十日内付款。")])

    assert vectors == {}
    assert matcher.enabled is False
    assert "OpenAI-compatible embedding request failed" in caplog.text
