from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.models import BBox


POSITIVE_ASSERTIONS = {"MUST_EXIST", "MUST_REVIEW"}


@dataclass
class RegressionFailure:
    contract: str
    case_id: str
    assertion: str
    failure_type: str
    expected: dict[str, Any]
    actual: dict[str, Any] | None
    reason: str

    def render(self) -> str:
        return (
            f"Contract: {self.contract}\n"
            f"Case ID: {self.case_id}\n"
            f"Assertion Type: {self.assertion}\n"
            f"Expected: {self.expected}\n"
            f"Actual: {self.actual or 'missing'}\n"
            f"Failure Type: {self.failure_type}\n"
            f"Reason: {self.reason}"
        )


@dataclass
class MatchReport:
    contract: str
    matched_positive: int = 0
    matched_negative: int = 0
    matched_review: int = 0
    warnings: list[str] = field(default_factory=list)
    failures: list[RegressionFailure] = field(default_factory=list)
    consumed_actual: set[int] = field(default_factory=set)

    def assert_passed(self) -> None:
        if self.failures:
            raise AssertionError("\n\n".join(item.render() for item in self.failures))


class SemanticGoldenMatcher:
    def match(self, contract_key: str, assertions: list[dict[str, Any]], actual: list[dict[str, Any]]) -> MatchReport:
        report = MatchReport(contract=contract_key)
        positives = [item for item in assertions if item["assertion"] in POSITIVE_ASSERTIONS]
        negatives = [item for item in assertions if item["assertion"] not in POSITIVE_ASSERTIONS]

        for expected in positives:
            candidates = [
                index
                for index, diff in enumerate(actual)
                if index not in report.consumed_actual and self._matches(expected, diff)
            ]
            if not candidates:
                identity_matches = [
                    diff
                    for index, diff in enumerate(actual)
                    if index not in report.consumed_actual and self._matches_identity(expected, diff)
                ]
                failure_type = "DECISION_REGRESSION" if identity_matches else "FALSE_NEGATIVE_REGRESSION"
                reason = (
                    "Business diff exists but decision/content status differs from Golden"
                    if identity_matches
                    else "No unconsumed actual diff matched the semantic business fact"
                )
                report.failures.append(
                    RegressionFailure(
                        contract_key,
                        expected["case_id"],
                        expected["assertion"],
                        failure_type,
                        expected,
                        identity_matches[0] if identity_matches else None,
                        reason,
                    )
                )
                continue
            index = max(candidates, key=lambda item: self._score(expected, actual[item]))
            matched = actual[index]
            bbox_reason = self._bbox_failure(expected, matched)
            if bbox_reason:
                report.failures.append(
                    RegressionFailure(
                        contract_key,
                        expected["case_id"],
                        expected["assertion"],
                        "BBOX_REGRESSION",
                        expected,
                        matched,
                        bbox_reason,
                    )
                )
                continue
            report.consumed_actual.add(index)
            report.matched_positive += 1
            if expected["assertion"] == "MUST_REVIEW":
                report.matched_review += 1

        for expected in negatives:
            matches = [diff for diff in actual if self._matches(expected, diff)]
            if expected["assertion"] == "MUST_NOT_EXIST":
                violating = matches
            elif expected["assertion"] == "MUST_NOT_CONFIRM":
                violating = [diff for diff in matches if self._is_confirmed(diff)]
            else:
                raise AssertionError(f"Unsupported assertion: {expected['assertion']}")
            if violating:
                report.failures.append(
                    RegressionFailure(
                        contract_key,
                        expected["case_id"],
                        expected["assertion"],
                        "FALSE_POSITIVE_REGRESSION",
                        expected,
                        violating[0],
                        "Forbidden semantic diff is present",
                    )
                )
            else:
                report.matched_negative += 1

        for index, diff in enumerate(actual):
            if index in report.consumed_actual:
                continue
            if self._is_confirmed(diff):
                report.failures.append(
                    RegressionFailure(
                        contract_key,
                        "UNEXPECTED_CONFIRMED_DIFF",
                        "UNEXPECTED_CONFIRMED_DIFF",
                        "UNEXPECTED_CONFIRMED_DIFF",
                        {},
                        diff,
                        "Confirmed user-visible diff was not consumed by the Golden oracle",
                    )
                )
            else:
                report.warnings.append(
                    f"{contract_key}: unexpected NEEDS_REVIEW diff: {diff.get('title')}"
                )
        return report

    def _matches_identity(self, expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        relaxed = {
            key: value
            for key, value in expected.items()
            if key not in {"quality_status", "content_recognition_status", "change_decision", "bbox_expectation"}
        }
        return self._matches(relaxed, actual)

    def _matches(self, expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        checks = {
            "diff_type": actual.get("diff_type"),
            "source_type": actual.get("source_type"),
            "quality_status": actual.get("quality_status"),
            "content_recognition_status": actual.get("content_recognition_status"),
            "change_decision": (actual.get("match_score_details") or {}).get("change_decision"),
            "field_type": self._field_type(actual),
            "party": self._party(actual),
            "visual_object_type": self._visual_object_type(actual),
            "page": self._page(actual),
            "region": self._region(actual),
        }
        for key, actual_value in checks.items():
            if key in expected and expected[key] not in (None, "") and expected[key] != actual_value:
                return False
        if expected.get("clause_identity") and _norm(expected["clause_identity"]) != _norm(actual.get("title")):
            return False
        if expected.get("title") and _norm(expected["title"]) != _norm(actual.get("title")):
            return False
        if expected.get("semantic_before") and _norm(expected["semantic_before"]) not in _norm(actual.get("original_text")):
            return False
        if expected.get("semantic_after") and _norm(expected["semantic_after"]) not in _norm(actual.get("compare_text")):
            return False
        forbidden = str(expected.get("forbidden_value_pattern") or "")
        if forbidden and not re.search(forbidden, self._actual_text(actual), re.IGNORECASE):
            return False
        required_candidates = {_norm(item) for item in expected.get("content_candidates_contain") or []}
        actual_candidates = {
            _norm(item.get("text") or item.get("value") or item.get("candidate_value"))
            for item in actual.get("content_candidates") or []
            if isinstance(item, dict)
        }
        if not required_candidates.issubset(actual_candidates):
            return False
        presentation = expected.get("presentation_contains")
        if presentation and _norm(presentation) not in _norm(actual.get("readable_change")):
            return False
        return True

    @staticmethod
    def _score(expected: dict[str, Any], actual: dict[str, Any]) -> int:
        score = 0
        for key in ("title", "page", "party", "field_type", "visual_object_type", "semantic_before", "semantic_after"):
            if expected.get(key) not in (None, ""):
                score += 1
        return score

    def _bbox_failure(self, expected: dict[str, Any], actual: dict[str, Any]) -> str:
        bbox_spec = expected.get("bbox_expectation")
        if not bbox_spec:
            return ""
        side = str(bbox_spec.get("side") or "compare")
        boxes = actual.get(f"{side}_evidence") or []
        if not boxes:
            return f"expected {side} evidence bbox but actual has none"
        target = BBox.model_validate(bbox_spec["target_bbox"])
        minimum_iou = float(bbox_spec.get("min_iou", 0.35))
        candidates = [BBox.model_validate(item["bbox"]) for item in boxes]
        best_iou = max((_iou(target, candidate) for candidate in candidates), default=0.0)
        if best_iou < minimum_iou:
            return f"bbox IoU {best_iou:.3f} below semantic threshold {minimum_iou:.3f}"
        party_column = bbox_spec.get("party_column")
        page_width = float(bbox_spec.get("page_width") or 0.0)
        if party_column and page_width:
            best = max(candidates, key=lambda candidate: _iou(target, candidate))
            center = (best.x0 + best.x1) / 2
            if party_column == "left" and center >= page_width / 2:
                return "bbox moved out of left party column"
            if party_column == "right" and center < page_width / 2:
                return "bbox moved out of right party column"
        return ""

    @staticmethod
    def _page(actual: dict[str, Any]) -> int | None:
        for side in ("compare_evidence", "original_evidence"):
            boxes = actual.get(side) or []
            if boxes:
                return int(boxes[0]["page_no"])
        match = re.search(r"第\s*(\d+)\s*页", str(actual.get("title") or ""))
        return int(match.group(1)) if match else None

    @staticmethod
    def _party(actual: dict[str, Any]) -> str:
        title = str(actual.get("title") or "")
        return title.split("·", 1)[0].strip() if "·" in title else ""

    @staticmethod
    def _field_type(actual: dict[str, Any]) -> str:
        field = str((actual.get("match_score_details") or {}).get("field_key") or "")
        if field:
            return field
        title = str(actual.get("title") or "")
        if "日期" in title or title.endswith("时间"):
            return "date"
        if "税号" in title or "纳税人识别" in title:
            return "tax_id"
        return ""

    @staticmethod
    def _visual_object_type(actual: dict[str, Any]) -> str:
        source = str(actual.get("source_type") or "")
        section = str(actual.get("section_type") or "")
        title = str(actual.get("title") or "")
        if source == "seal" or "印章" in title:
            return "seal"
        if "signature" in section or "签字" in title or "签名" in title:
            return "signature"
        return ""

    @staticmethod
    def _region(actual: dict[str, Any]) -> str:
        source = str(actual.get("source_type") or "")
        if source in {"seal", "signing_region"}:
            return "signing"
        if source == "clause":
            return "clause"
        if source == "metadata":
            return "cover"
        return source

    @staticmethod
    def _is_confirmed(actual: dict[str, Any]) -> bool:
        decision = str((actual.get("match_score_details") or {}).get("change_decision") or "")
        return decision == "CONFIRMED" or (
            actual.get("quality_status") != "NEEDS_REVIEW"
            and actual.get("content_recognition_status") != "NEEDS_REVIEW"
        )

    @staticmethod
    def _actual_text(actual: dict[str, Any]) -> str:
        return " ".join(
            str(actual.get(key) or "")
            for key in ("title", "original_text", "compare_text", "readable_change")
        )


def _norm(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _iou(left: BBox, right: BBox) -> float:
    ix0 = max(left.x0, right.x0)
    iy0 = max(left.y0, right.y0)
    ix1 = min(left.x1, right.x1)
    iy1 = min(left.y1, right.y1)
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if intersection <= 0:
        return 0.0
    left_area = max(0.0, left.x1 - left.x0) * max(0.0, left.y1 - left.y0)
    right_area = max(0.0, right.x1 - right.x0) * max(0.0, right.y1 - right.y0)
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0
