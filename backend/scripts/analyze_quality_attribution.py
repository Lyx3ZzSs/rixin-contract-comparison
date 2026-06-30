from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

SUSPICIOUS_MATCH_METHODS = {
    "same_clause_key_weighted",
    "same_clause_no_low_similarity",
    "body_weighted_similarity",
    "partial_body_similarity",
    "section_mismatch_blocked",
}


def analyze_run_dir(run_dir: Path) -> dict[str, Any]:
    quality_path = run_dir / "quality.json"
    if not quality_path.exists():
        raise FileNotFoundError(f"quality report does not exist: {quality_path}")

    quality = _read_json_object(quality_path)
    cases = [_analyze_case(run_dir, case) for case in _quality_cases(quality)]
    return {
        "run_id": _run_id(run_dir),
        "case_count": len(cases),
        "aggregate": _aggregate_report(quality, cases),
        "cases": cases,
    }


def _quality_cases(quality: dict[str, Any]) -> list[dict[str, Any]]:
    cases = quality.get("cases", [])
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict)]


def _run_id(run_dir: Path) -> str:
    summary_path = run_dir / "run_summary.json"
    if summary_path.exists():
        summary = _read_json_object(summary_path)
        run_id = summary.get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return run_dir.name


def _analyze_case(run_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id", ""))
    warnings: list[str] = []
    summary_payload = _read_optional_debug_json(
        run_dir,
        case_id,
        "match_matrix_summary.json",
        warnings,
    )
    matches_payload = _read_optional_debug_json(
        run_dir,
        case_id,
        "clause_matches.json",
        warnings,
    )
    summary = summary_payload if isinstance(summary_payload, dict) else {}
    matches = matches_payload if isinstance(matches_payload, list) else []
    _record_malformed_match_warnings(case_id, matches, warnings)

    risk_counts = _alignment_risk_flag_counts(summary, matches)
    method_counts = _match_method_counts(summary, matches)
    low_confidence_count = _low_confidence_alignment_count(summary, matches)
    suspicious_matches = _suspicious_matches(matches)
    attribution_tags = _attribution_tags(
        case=case,
        risk_counts=risk_counts,
        method_counts=method_counts,
        low_confidence_count=low_confidence_count,
        suspicious_matches=suspicious_matches,
    )

    return {
        "case_id": case_id,
        "quality_status": case.get("status", ""),
        "false_positive_count": _int_value(case.get("false_positive_count")),
        "false_negative_count": _int_value(case.get("false_negative_count")),
        "low_confidence_alignment_count": low_confidence_count,
        "alignment_risk_flag_counts": dict(risk_counts),
        "match_method_counts": dict(method_counts),
        "suspicious_matches": suspicious_matches,
        "attribution_tags": attribution_tags,
        "warnings": warnings,
    }


def _read_optional_debug_json(
    run_dir: Path,
    case_id: str,
    filename: str,
    warnings: list[str],
) -> Any:
    for root in _debug_roots(run_dir, case_id):
        path = root / filename
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            warnings.append(f"{path}: invalid json: {error.msg}")
            return None
    warnings.append(f"{case_id}: missing debug artifact {filename}")
    return None


def _debug_roots(run_dir: Path, case_id: str) -> list[Path]:
    return [
        run_dir / "debug" / case_id,
        run_dir / case_id / "debug",
        run_dir / "cases" / case_id / "debug",
    ]


def _record_malformed_match_warnings(
    case_id: str,
    matches: list[Any],
    warnings: list[str],
) -> None:
    for index, match in enumerate(matches):
        if not isinstance(match, dict):
            continue
        if "score_details" not in match:
            continue
        score_details = match.get("score_details")
        if not isinstance(score_details, dict):
            warnings.append(
                f"{case_id}: invalid score_details in clause_matches[{index}]",
            )
            continue
        if "alignment" not in score_details:
            continue
        alignment = score_details.get("alignment")
        if not isinstance(alignment, dict):
            warnings.append(f"{case_id}: invalid alignment in clause_matches[{index}]")
            continue
        risk_flags = alignment.get("risk_flags")
        if "risk_flags" in alignment and not isinstance(risk_flags, list | tuple | set):
            warnings.append(f"{case_id}: invalid risk_flags in clause_matches[{index}]")


def _alignment_risk_flag_counts(
    summary: dict[str, Any],
    matches: list[Any],
) -> Counter[str]:
    summary_counts = _counter_from_mapping(summary.get("alignment_risk_flag_counts"))
    if summary_counts:
        return summary_counts

    counts: Counter[str] = Counter()
    for match in matches:
        counts.update(_risk_flags_from_match(match))
    return counts


def _match_method_counts(summary: dict[str, Any], matches: list[Any]) -> Counter[str]:
    summary_counts = _counter_from_mapping(summary.get("method_counts"))
    if summary_counts:
        return summary_counts

    counts: Counter[str] = Counter()
    for match in matches:
        if isinstance(match, dict):
            method = match.get("match_method")
            if isinstance(method, str) and method:
                counts[method] += 1
    return counts


def _low_confidence_alignment_count(
    summary: dict[str, Any],
    matches: list[Any],
) -> int:
    summary_count = summary.get("low_confidence_alignment_count")
    if _is_number(summary_count):
        return int(summary_count)
    return sum(1 for match in matches if _has_low_confidence_alignment(match))


def _suspicious_matches(matches: list[Any]) -> list[dict[str, Any]]:
    suspicious = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        method = _string_value(match.get("match_method"))
        confidence = _string_value(match.get("match_confidence"))
        risk_flags = _risk_flags_from_match(match)
        body_length_coverage = _body_length_coverage(match)
        if not _is_suspicious_match(method, confidence, risk_flags):
            continue
        suspicious.append(
            {
                "original_clause_id": match.get("original_clause_id"),
                "compare_clause_id": match.get("compare_clause_id"),
                "match_method": method,
                "match_confidence": confidence,
                "risk_flags": risk_flags,
                "body_similarity": _alignment_number(match, "body_similarity"),
                "critical_token_overlap": _alignment_number(
                    match,
                    "critical_token_overlap",
                ),
                "body_length_coverage": body_length_coverage,
            },
        )
    return suspicious[:50]


def _is_suspicious_match(
    method: str,
    confidence: str,
    risk_flags: list[str],
) -> bool:
    return confidence == "LOW" or bool(risk_flags) or method in SUSPICIOUS_MATCH_METHODS


def _has_low_confidence_alignment(match: Any) -> bool:
    if not isinstance(match, dict):
        return False
    return match.get("match_confidence") == "LOW" or bool(_risk_flags_from_match(match))


def _attribution_tags(
    *,
    case: dict[str, Any],
    risk_counts: Counter[str],
    method_counts: Counter[str],
    low_confidence_count: int,
    suspicious_matches: list[dict[str, Any]],
) -> list[str]:
    tags: set[str] = set()
    if low_confidence_count > 0 or risk_counts:
        tags.add("LOW_CONFIDENCE_ALIGNMENT")
    tags.update(_raw_risk_flag_tags(risk_counts))
    if risk_counts.get("CRITICAL_TOKEN_MISMATCH", 0) > 0:
        tags.add("KEY_TOKEN_CONFLICT")
    if any(
        risk_counts.get(flag, 0) > 0
        for flag in (
            "POSSIBLE_CLAUSE_MISALIGNMENT",
            "TEXT_MATCH_NUMBER_MISMATCH",
            "TITLE_MATCH_TEXT_MISMATCH",
        )
    ):
        tags.add("POSSIBLE_CLAUSE_MISALIGNMENT")
    if any(method_counts.get(method, 0) > 0 for method in SUSPICIOUS_MATCH_METHODS):
        tags.add("SUSPICIOUS_MATCH_METHOD")
    if method_counts.get("body_weighted_similarity", 0) > 0:
        tags.add("BODY_ONLY_MATCH")
    if method_counts.get("section_mismatch_blocked", 0) > 0:
        tags.add("SECTION_MISMATCH_CANDIDATE")
    if any(
        match["match_method"] == "same_clause_key_weighted"
        and _float_value(match.get("body_length_coverage"), default=1.0) < 0.70
        for match in suspicious_matches
    ):
        tags.add("SAME_KEY_LOW_BODY_COVERAGE")
    if _int_value(case.get("false_positive_count")) > 0:
        tags.add("UNEXPECTED_ACTUAL_NEEDS_LABEL")
    if _int_value(case.get("false_negative_count")) > 0:
        tags.add("KEY_TOKEN_RECALL_RISK")
        tags.add("POSSIBLE_FIELD_DIFF_SUPPRESSED")
    elif _has_missed_expected_diffs(case) and (
        risk_counts.get("CRITICAL_TOKEN_MISMATCH", 0) > 0
        or "KEY_TOKEN_RECALL_RISK" in tags
    ):
        tags.add("POSSIBLE_FIELD_DIFF_SUPPRESSED")
    if _approved_annotation_count(case) <= 1:
        tags.add("EXPECTED_DIFF_TOO_SPARSE")
        tags.add("GOLD_CASE_NEEDS_REVIEW")
    return sorted(tags)


def _raw_risk_flag_tags(risk_counts: Counter[str]) -> set[str]:
    return {
        flag
        for flag in (
            "CRITICAL_TOKEN_MISMATCH",
            "TEXT_MATCH_NUMBER_MISMATCH",
            "TITLE_MATCH_TEXT_MISMATCH",
        )
        if risk_counts.get(flag, 0) > 0
    }


def _has_missed_expected_diffs(case: dict[str, Any]) -> bool:
    missed_expected = case.get("missed_expected_diffs")
    return isinstance(missed_expected, list) and len(missed_expected) > 0


def _approved_annotation_count(case: dict[str, Any]) -> int:
    annotation_summary = case.get("annotation_summary", {})
    if not isinstance(annotation_summary, dict):
        return 0
    return _int_value(annotation_summary.get("APPROVED"))


def _aggregate_report(
    quality: dict[str, Any],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    quality_aggregate = quality.get("aggregate", {})
    aggregate = quality_aggregate if isinstance(quality_aggregate, dict) else {}
    attribution_counts: Counter[str] = Counter()
    for case in cases:
        attribution_counts.update(case["attribution_tags"])

    return {
        "false_positive_count": _int_value(aggregate.get("false_positive_count")),
        "false_negative_count": _int_value(aggregate.get("false_negative_count")),
        "low_confidence_alignment_count": sum(
            case["low_confidence_alignment_count"] for case in cases
        ),
        "alignment_risk_flag_counts": dict(
            _sum_case_counters(cases, "alignment_risk_flag_counts"),
        ),
        "match_method_counts": dict(_sum_case_counters(cases, "match_method_counts")),
        "attribution_counts": dict(attribution_counts),
    }


def _sum_case_counters(cases: list[dict[str, Any]], field: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for case in cases:
        counts.update(_counter_from_mapping(case.get(field)))
    return counts


def _counter_from_mapping(payload: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not isinstance(payload, dict):
        return counts
    for key, value in payload.items():
        if isinstance(key, str) and key and _is_number(value):
            counts[key] += int(value)
    return counts


def _risk_flags_from_match(match: Any) -> list[str]:
    alignment = _alignment_details(match)
    risk_flags = alignment.get("risk_flags")
    if not isinstance(risk_flags, list | tuple | set):
        return []
    return [flag for flag in risk_flags if isinstance(flag, str) and flag]


def _alignment_details(match: Any) -> dict[str, Any]:
    if not isinstance(match, dict):
        return {}
    score_details = match.get("score_details")
    if not isinstance(score_details, dict):
        return {}
    alignment = score_details.get("alignment")
    if not isinstance(alignment, dict):
        return {}
    return alignment


def _body_length_coverage(match: dict[str, Any]) -> float | None:
    score_details = match.get("score_details")
    if not isinstance(score_details, dict):
        return None
    value = score_details.get("body_length_coverage")
    if _is_number(value):
        return float(value)
    return None


def _alignment_number(match: Any, field: str) -> float | None:
    value = _alignment_details(match).get(field)
    if _is_number(value):
        return float(value)
    return None


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _int_value(value: Any) -> int:
    if _is_number(value):
        return int(value)
    return 0


def _float_value(value: Any, *, default: float) -> float:
    if _is_number(value):
        return float(value)
    return default


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _string_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""
