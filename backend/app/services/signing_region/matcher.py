from __future__ import annotations

from app.services.signing_region.models import SigningRegion


class SigningRegionMatcher:
    threshold = 0.55
    strong_position_threshold = 0.8

    def match(
        self,
        original: list[SigningRegion],
        compare: list[SigningRegion],
    ) -> list[tuple[SigningRegion | None, SigningRegion | None, float]]:
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

    def _score(self, original: SigningRegion, compare: SigningRegion) -> float:
        page_score = self._page_score(original, compare)
        if page_score == 0.0:
            return 0.0
        iou_score = self._iou(original, compare)
        position_score = self._position_score(original, compare)
        if iou_score == 0.0 and position_score < self.strong_position_threshold:
            return 0.0
        role_score = 1.0 if original.region_role == compare.region_role else 0.4
        return round(page_score * 0.35 + role_score * 0.25 + iou_score * 0.25 + position_score * 0.15, 4)

    @staticmethod
    def _page_score(original: SigningRegion, compare: SigningRegion) -> float:
        if original.page_no == compare.page_no:
            return 1.0
        if abs(original.page_no - compare.page_no) <= 1:
            return 0.5
        return 0.0

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
