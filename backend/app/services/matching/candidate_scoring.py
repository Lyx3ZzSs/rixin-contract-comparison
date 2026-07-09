from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.models import Clause
from app.services.clause_keys import canonical_path_label
from app.services.matching._fuzz import partial_score, ratio_score, token_score
from app.services.matching.constants import (
    BODY_ONLY_ALIGNMENT_RISK,
    CRITICAL_TOKEN_CONFLICT,
    SAME_KEY_LOW_BODY_COVERAGE,
    SAME_NUMBER_LOW_BODY_SIMILARITY,
)


class CandidateScoringMixin:
    def _score_details(
        self,
        left: Clause,
        right: Clause,
        original_index: int,
        compare_index: int,
        original: list[Clause],
        compare: list[Clause],
        original_count: int,
        compare_count: int,
    ) -> dict[str, Any]:
        title_score = self._token_score(left.title, right.title) if left.title and right.title else 0.0
        body_details = self._body_score_details(self._match_text(left), self._match_text(right))
        clause_no_score = self._clause_no_score(left.clause_no, right.clause_no)
        clause_key_score = self._clause_key_score(left, right)
        canonical_path_score = self._canonical_path_score(left, right)
        section_score = self._section_score(left, right)
        section_mismatch_score = self._section_mismatch_score(
            left,
            right,
            body_details["body_score"],
            clause_no_score,
            title_score,
        )
        position_score = self._position_score(original_index / original_count, compare_index / compare_count)
        neighbor_score = self._neighbor_score(original, compare, original_index, compare_index)
        business_token_score, business_mismatch = self._business_token_score(
            left.text,
            right.text,
            body_details["body_score"],
        )
        details = {
            "clause_key_score": round(clause_key_score, 2),
            "canonical_path_score": round(canonical_path_score, 2),
            "clause_no_score": round(clause_no_score, 2),
            "section_score": round(section_score, 2),
            "section_path_mismatch": 1.0 if self._section_path_mismatch(left, right) else 0.0,
            "section_mismatch_candidate": 1.0 if section_mismatch_score >= 88 else 0.0,
            "section_mismatch_score": round(section_mismatch_score, 2),
            "title_score": round(title_score, 2),
            "position_score": round(position_score, 2),
            "neighbor_score": round(neighbor_score, 2),
            "business_token_score": round(business_token_score, 2),
            "business_token_mismatch": 1.0 if business_mismatch else 0.0,
            "weak_numeric_marker": 1.0 if self._has_weak_numeric_marker(left, right) else 0.0,
            "short_clause_pair": 1.0 if self._is_short_clause(left) or self._is_short_clause(right) else 0.0,
            "semantic_score": 0.0,
            "semantic_recall_applied": 0.0,
            "semantic_recall_mode": getattr(self, "semantic_recall_mode", "sparse"),
            "semantic_recall_min_rule_candidates": float(getattr(self, "semantic_min_rule_candidates", 3)),
            "semantic_recall_rule_candidate_count": 0.0,
            "semantic_rerank_available": 0.0,
            "semantic_rerank_score": 0.0,
            "semantic_rerank_reason": "",
            "semantic_rerank_reason_present": 0.0,
            "rerank_available": 0.0,
            "rerank_score": 0.0,
            "rerank_reason_present": 0.0,
            "assignment_strategy_optimal": 1.0 if self.assignment_strategy == "optimal" else 0.0,
        }
        details.update({key: round(value, 2) for key, value in body_details.items()})
        details["alignment"] = self.alignment_analyzer.diagnostics(left, right)
        self._apply_matcher_guard_details(details)
        return details

    def _weighted_score(self, details: dict[str, Any]) -> float:
        weighted = (
            details["clause_key_score"] * 0.16
            + details.get("canonical_path_score", 0.0) * 0.05
            + details["section_score"] * 0.10
            + details["clause_no_score"] * 0.14
            + details["title_score"] * 0.18
            + details["body_score"] * 0.32
            + details["business_token_score"] * 0.07
            + details["position_score"] * 0.06
            + details["neighbor_score"] * 0.05
            + details.get("semantic_score", 0.0) * self.semantic_weight
            + float(details.get("semantic_rerank_score", 0.0))
            * float(details.get("semantic_rerank_available", 0.0))
            * self.rerank_weight
        )
        if details["section_score"] < 100 and details["body_score"] < 90:
            weighted = min(weighted, 72.0)
        if details["clause_key_score"] == 100 and details["body_score"] >= 45:
            weighted = max(weighted, 88.0 + min(12.0, details["body_score"] * 0.12))
        elif details["clause_no_score"] == 100 and details["title_score"] >= 90 and details["body_score"] >= 45:
            weighted = max(weighted, 100.0)
        elif details["clause_no_score"] == 100 and (details["body_score"] >= 55 or details["title_score"] >= 80):
            weighted = max(
                weighted,
                min(100.0, 70.0 + details["body_score"] * 0.20 + details["title_score"] * 0.10),
            )
        elif details["body_score"] >= self.threshold:
            if details["business_token_score"] < 45:
                weighted = max(weighted, details["body_score"] * 0.90)
            else:
                weighted = max(weighted, details["body_score"])
        elif details.get("semantic_score", 0.0) >= 88 and details["body_score"] >= 60:
            weighted = max(weighted, details["semantic_score"] * 0.92)
        elif details["body_score"] >= min(self.threshold, 78):
            weighted = max(weighted, details["body_score"] * 0.92)
        if details["weak_numeric_marker"] >= 1 and details["body_score"] < 80 and details["title_score"] < 75:
            weighted = min(weighted, 68.0)
        risk_flags = set(details.get("matcher_risk_flags", []))
        if SAME_KEY_LOW_BODY_COVERAGE in risk_flags:
            weighted = min(weighted, 82.0)
        if (
            CRITICAL_TOKEN_CONFLICT in risk_flags
            and self._alignment_number(details.get("alignment"), "critical_token_overlap", default=1.0) <= 0.0
        ):
            weighted = min(weighted, 84.0)
        if (
            SAME_NUMBER_LOW_BODY_SIMILARITY in risk_flags
            and details["title_score"] < 80
        ):
            weighted = min(weighted, 78.0)
        if BODY_ONLY_ALIGNMENT_RISK in risk_flags:
            weighted = min(weighted, 84.0)
        return round(max(0.0, min(100.0, weighted)), 2)

    def _clause_no_score(self, left: str, right: str) -> float:
        left_norm = self._normalize_clause_no(left)
        right_norm = self._normalize_clause_no(right)
        if left_norm and right_norm and left_norm == right_norm:
            return 100.0
        if not left_norm or not right_norm:
            return 0.0
        left_parts = left_norm.split(".")
        right_parts = right_norm.split(".")
        if left_parts[-1] == right_parts[-1] and len(left_parts) == len(right_parts):
            return 70.0
        if len(left_parts) == len(right_parts):
            return 40.0
        return self._score(left_norm, right_norm) * 0.5

    def _clause_key_score(self, left: Clause, right: Clause) -> float:
        if left.clause_key and right.clause_key and left.clause_key == right.clause_key:
            return 100.0
        if not left.clause_key or not right.clause_key:
            return 0.0
        if left.section_type and right.section_type and left.section_type != right.section_type:
            return 0.0
        return self._token_score(left.clause_key, right.clause_key)

    def _canonical_path_score(self, left: Clause, right: Clause) -> float:
        left_path = self._canonical_path(left)
        right_path = self._canonical_path(right)
        if left_path and right_path and left_path == right_path:
            return 100.0
        if not left_path or not right_path:
            return 0.0
        if (left.section_type or "main_contract") != (right.section_type or "main_contract"):
            return 0.0
        return self._token_score(left_path, right_path)

    def _canonical_path(self, clause: Clause) -> str:
        parts = [clause.section_type or "main_contract"]
        for item in clause.section_path:
            parts.append(self._canonical_path_item(item))
        if clause.clause_no:
            parts.append(self._canonical_clause_no_path_item(clause.clause_no))
        elif clause.title:
            parts.append(self.normalizer.normalize_for_match(clause.title)[:32])
        return "/".join(part for part in parts if part)

    def _canonical_path_item(self, item: str) -> str:
        return self.normalizer.normalize_for_match(canonical_path_label(item, self.number_parser))

    def _canonical_clause_no_path_item(self, clause_no: str) -> str:
        normalized = self._normalize_clause_no(clause_no).replace(".", "_")
        return self.normalizer.normalize_for_match(f"n{normalized}") if normalized else ""

    def _section_mismatch_score(
        self,
        left: Clause,
        right: Clause,
        body_score: float,
        clause_no_score: float,
        title_score: float,
    ) -> float:
        if (left.section_type or "main_contract") == (right.section_type or "main_contract"):
            return 0.0
        score = body_score
        if clause_no_score >= 100:
            score = max(score, 90.0)
        if title_score >= 92 and body_score >= 80:
            score = max(score, 88.0)
        return score

    def _section_score(self, left: Clause, right: Clause) -> float:
        left_type = left.section_type or "main_contract"
        right_type = right.section_type or "main_contract"
        if left_type != right_type:
            return 0.0
        left_path = "/".join(left.section_path[:-1])
        right_path = "/".join(right.section_path[:-1])
        if not left_path and not right_path:
            return 100.0
        if left_path and right_path:
            return self._token_score(left_path, right_path)
        return 70.0

    def _section_path_mismatch(self, left: Clause, right: Clause) -> bool:
        if (left.section_type or "main_contract") != (right.section_type or "main_contract"):
            return False
        left_path = "/".join(left.section_path[:-1])
        right_path = "/".join(right.section_path[:-1])
        if not left_path and not right_path:
            return False
        return left_path != right_path

    def _has_weak_numeric_marker(self, left: Clause, right: Clause) -> bool:
        flags = {*left.split_flags, *right.split_flags}
        if "WEAK_NUMERIC_MARKER" in flags:
            return True
        left_raw = (left.clause_no or "").strip()
        right_raw = (right.clause_no or "").strip()
        left_norm = self._normalize_clause_no(left.clause_no)
        right_norm = self._normalize_clause_no(right.clause_no)
        return bool(
            left_norm
            and right_norm
            and left_norm == right_norm
            and re.fullmatch(r"\d+", left_raw)
            and re.fullmatch(r"\d+", right_raw)
            and (len(left.title.strip()) < 4 or len(right.title.strip()) < 4)
        )

    def _has_weak_titles(self, left: Clause, right: Clause) -> bool:
        return self._weak_title(left.title) or self._weak_title(right.title)

    def _is_short_clause(self, clause: Clause) -> bool:
        compact = self._match_text(clause)
        return 0 < len(compact) < 20

    def _weak_title(self, title: str) -> bool:
        compact = re.sub(r"[\s、.．:：]+", "", title or "")
        return not compact or bool(re.fullmatch(r"\d{1,3}", compact))

    def _same_clause_no(self, left: str, right: str) -> bool:
        left_norm = self._normalize_clause_no(left)
        right_norm = self._normalize_clause_no(right)
        return bool(left_norm and right_norm and left_norm == right_norm)

    def _normalize_clause_no(self, value: str) -> str:
        return self.number_parser.normalize_number(value)

    def _position_score(self, left_ratio: float, right_ratio: float) -> float:
        distance = abs(left_ratio - right_ratio)
        return max(0.0, 100.0 - distance * 180.0)

    def _neighbor_score(
        self,
        original: list[Clause],
        compare: list[Clause],
        original_index: int,
        compare_index: int,
    ) -> float:
        scores: list[float] = []
        if original_index > 0 and compare_index > 0:
            scores.append(
                self._clause_no_score(
                    original[original_index - 1].clause_no,
                    compare[compare_index - 1].clause_no,
                )
            )
        if original_index + 1 < len(original) and compare_index + 1 < len(compare):
            scores.append(
                self._clause_no_score(
                    original[original_index + 1].clause_no,
                    compare[compare_index + 1].clause_no,
                )
            )
        return sum(scores) / len(scores) if scores else 0.0

    def _score(self, left: str, right: str) -> float:
        return self._token_score(left, right)

    def _token_score(self, left: str, right: str) -> float:
        return token_score(left, right)

    def _ratio_score(self, left: str, right: str) -> float:
        return ratio_score(left, right)

    def _partial_score(self, left: str, right: str) -> float:
        return partial_score(left, right)

    def _body_score_details(self, left: str, right: str) -> dict[str, float]:
        ratio_score = self._ratio_score(left, right)
        token_score = self._token_score(left, right)
        partial_score = self._partial_score(left, right)
        min_len = min(len(left or ""), len(right or ""))
        max_len = max(len(left or ""), len(right or ""))
        length_coverage = (min_len / max_len) if max_len else 1.0
        capped_token = token_score
        capped_partial = partial_score
        if min_len < 8:
            capped_token = min(capped_token, 70.0)
            capped_partial = min(capped_partial, 70.0)
        elif length_coverage < 0.50:
            capped_token = min(capped_token, 76.0)
            capped_partial = min(capped_partial, 72.0)
        elif length_coverage < 0.70:
            capped_token = min(capped_token, 86.0)
            capped_partial = min(capped_partial, 82.0)
        elif length_coverage < 0.85:
            capped_partial = min(capped_partial, 92.0)
        body_score = max(ratio_score, capped_token, capped_partial)
        return {
            "body_ratio_score": ratio_score,
            "body_token_score": token_score,
            "body_token_capped_score": capped_token,
            "body_partial_score": partial_score,
            "body_length_coverage": length_coverage,
            "body_score": body_score,
        }

    def _business_token_score(self, left: str, right: str, body_score: float) -> tuple[float, bool]:
        left_tokens = self._business_tokens(left)
        right_tokens = self._business_tokens(right)
        if not left_tokens and not right_tokens:
            return 100.0, False
        if not left_tokens or not right_tokens:
            return 70.0, body_score >= 80
        intersection = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        score = (intersection / union) * 100 if union else 100.0
        return score, bool(score < 45 and body_score >= 70)

    def _business_tokens(self, text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", text or "").lower()
        tokens: set[str] = set()
        token_patterns = [
            r"\bv\s*\d+(?:\.\d+)*\b",
            r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|‰)",
            r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:元|万元|亿元|usd|rmb|cny|人民币|美元)",
            r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?",
            r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}",
            r"\d+(?:,\d{3})*(?:\.\d+)?",
            r"\bparty\s+[ab]\b",
            r"\bcompany\b",
            r"\bbank\b",
            r"\baccount\b",
        ]
        for pattern in token_patterns:
            tokens.update(re.sub(r"\s+", "", match.group(0)) for match in re.finditer(pattern, normalized))
        for keyword in ("甲方", "乙方", "丙方", "公司", "银行", "账号", "合同金额", "违约金", "质保期", "付款", "交货", "期限"):
            if keyword in normalized:
                tokens.add(keyword)
        return tokens

    def _match_text(self, clause: Clause) -> str:
        return clause.match_text or clause.normalized_text
