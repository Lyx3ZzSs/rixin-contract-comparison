from __future__ import annotations

import httpx

from app.models import Clause
from app.services.diff_engine import DiffEngine
from app.services.matcher import ClauseMatcher, SemanticMatcher
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
