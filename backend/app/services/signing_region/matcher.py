from __future__ import annotations

import re

from app.services.signing_region.models import SigningRegion


BUSINESS_FIELD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("address", re.compile(r"地址")),
    ("contact", re.compile(r"联系人")),
    ("phone", re.compile(r"电话")),
    ("fax", re.compile(r"传真")),
    ("email", re.compile(r"邮箱|电子邮箱|E-?mail", re.IGNORECASE)),
    ("bank", re.compile(r"开户")),
    ("account", re.compile(r"账号|帐")),
    ("credit", re.compile(r"统一社会信用代码|税号|纳税人识别号")),
)


class SigningRegionMatcher:
    threshold = 0.55
    strong_position_threshold = 0.8

    def match(
        self,
        original: list[SigningRegion],
        compare: list[SigningRegion],
        *,
        page_alignment=None,
    ) -> list[tuple[SigningRegion | None, SigningRegion | None, float]]:
        self.page_alignment = page_alignment
        original = self._join_continuation_groups(original)
        compare = self._join_continuation_groups(compare)
        pairs: list[tuple[SigningRegion | None, SigningRegion | None, float]] = []
        used_compare: set[int] = set()
        for orig in original:
            best_index = -1
            best_score = 0.0
            for index, comp in enumerate(compare):
                if index in used_compare:
                    continue
                score = self._score(orig, comp)
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index >= 0 and best_score >= self.threshold:
                used_compare.add(best_index)
                pairs.append((orig, compare[best_index], best_score))
            else:
                pairs.append((orig, None, 0.0))
        for index, comp in enumerate(compare):
            if index not in used_compare:
                pairs.append((None, comp, 0.0))
        return pairs

    @staticmethod
    def _join_continuation_groups(regions: list[SigningRegion]) -> list[SigningRegion]:
        """Join only continuations established by same-side form boundaries.

        Elements retain their original page and bounding box; coordinates on
        different pages must never be unioned into a fictitious page region.
        """
        joined: list[SigningRegion] = []
        groups: dict[str, SigningRegion] = {}
        for region in regions:
            if not region.group_id:
                joined.append(region)
                continue
            if region.group_id not in groups:
                copy = region.model_copy(deep=True)
                groups[region.group_id] = copy
                joined.append(copy)
            else:
                target = groups[region.group_id]
                target.elements.extend(region.elements)
                target.confidence_reasons = list(dict.fromkeys([
                    *target.confidence_reasons, "cross_page_signing_group",
                ]))
        return joined

    @staticmethod
    def _explicit_form_roles(region: SigningRegion) -> set[str]:
        text = "\n".join(element.text for element in region.elements)
        if not re.search(r"签字页|签署页", text):
            return set()
        return set(re.findall(r"(甲方|乙方|丙方|丁方|买方|卖方|设计方|采购方|供货方)[:：]", text))

    def _score(self, original: SigningRegion, compare: SigningRegion) -> float:
        text_score = self._text_structure_score(original, compare)
        page_score = self._page_score(original, compare)
        same_explicit_role = original.region_role == compare.region_role and original.region_role.value != "unknown"
        original_roles = self._explicit_form_roles(original)
        compare_roles = self._explicit_form_roles(compare)
        same_explicit_form = (page_score > 0 and len(original_roles & compare_roles) >= 2
                              and original_roles == compare_roles)
        if same_explicit_form:
            text_score = max(text_score, 0.8)
        semantic_pair = (same_explicit_role and text_score >= 0.65) or same_explicit_form
        if page_score == 0.0 and not semantic_pair:
            return 0.0
        if page_score == 0.0:
            page_score = 0.25
        iou_score = self._iou(original, compare)
        position_score = self._position_score(original, compare)
        role_score = 1.0 if original.region_role == compare.region_role else 0.4
        if page_score == 1.0 and role_score == 1.0 and iou_score == 1.0 and position_score == 1.0:
            return 1.0
        adjacent_page_shift = abs(original.page_no - compare.page_no) == 1 and text_score >= 0.65
        if (
            iou_score == 0.0
            and position_score < self.strong_position_threshold
            and not adjacent_page_shift
            and not semantic_pair
        ):
            return 0.0
        score = round(
            page_score * 0.25 + role_score * 0.2 + iou_score * 0.15 + position_score * 0.1 + text_score * 0.3,
            4,
        )
        high_confidence_adjacent_signing_pair = (
            adjacent_page_shift
            and role_score == 1.0
            and min(original.confidence, compare.confidence) >= 0.7
            and text_score >= 0.65
        )
        if high_confidence_adjacent_signing_pair:
            return max(score, self.threshold)
        if same_explicit_form and min(original.confidence, compare.confidence) >= 0.5:
            return max(score, self.threshold)
        if semantic_pair and min(original.confidence, compare.confidence) >= 0.7:
            return max(score, self.threshold)
        return score

    def _page_score(self, original: SigningRegion, compare: SigningRegion) -> float:
        if original.page_no == compare.page_no:
            return 1.0
        relationship_score = getattr(getattr(self, "page_alignment", None), "relationship_score", None)
        if callable(relationship_score):
            aligned = float(relationship_score({original.page_no}, {compare.page_no}))
            if aligned:
                return aligned
        if abs(original.page_no - compare.page_no) <= 1:
            return 0.5
        return 0.0

    @staticmethod
    def _text_structure_score(original: SigningRegion, compare: SigningRegion) -> float:
        def signing_tokens(region: SigningRegion) -> set[str]:
            text = "".join(element.text for element in region.elements)
            result: set[str] = set()
            for token in ("甲方", "乙方", "盖章", "签字", "日期", "法人", "授权"):
                if token in text:
                    result.add(token)
            return result

        def business_tokens(region: SigningRegion) -> set[str]:
            text = re.sub(r"\s+", "", "".join(element.text for element in region.elements))
            return {name for name, pattern in BUSINESS_FIELD_PATTERNS if pattern.search(text)}

        def jaccard(left: set[str], right: set[str]) -> float:
            if not left or not right:
                return 0.0
            return len(left & right) / len(left | right)

        return max(
            jaccard(signing_tokens(original), signing_tokens(compare)),
            jaccard(business_tokens(original), business_tokens(compare)),
        )

    @staticmethod
    def _iou(original: SigningRegion, compare: SigningRegion) -> float:
        x0 = max(original.bbox.x0, compare.bbox.x0)
        y0 = max(original.bbox.y0, compare.bbox.y0)
        x1 = min(original.bbox.x1, compare.bbox.x1)
        y1 = min(original.bbox.y1, compare.bbox.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        orig_area = max(1.0, (original.bbox.x1 - original.bbox.x0) * (original.bbox.y1 - original.bbox.y0))
        comp_area = max(1.0, (compare.bbox.x1 - compare.bbox.x0) * (compare.bbox.y1 - compare.bbox.y0))
        return inter / (orig_area + comp_area - inter)

    @staticmethod
    def _position_score(original: SigningRegion, compare: SigningRegion) -> float:
        orig_width = max(1.0, original.bbox.x1 - original.bbox.x0)
        comp_width = max(1.0, compare.bbox.x1 - compare.bbox.x0)
        orig_height = max(1.0, original.bbox.y1 - original.bbox.y0)
        comp_height = max(1.0, compare.bbox.y1 - compare.bbox.y0)
        orig_cx = (original.bbox.x0 + original.bbox.x1) / 2
        comp_cx = (compare.bbox.x0 + compare.bbox.x1) / 2
        orig_cy = (original.bbox.y0 + original.bbox.y1) / 2
        comp_cy = (compare.bbox.y0 + compare.bbox.y1) / 2
        dx = abs(orig_cx - comp_cx) / max(orig_width, comp_width)
        dy = abs(orig_cy - comp_cy) / max(orig_height, comp_height)
        return max(0.0, 1.0 - max(dx, dy))
