from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.models import BBox, DiffItem, DiffType, Document, EvidenceBox
from app.utils.id_utils import generate_diff_id


@dataclass(frozen=True)
class RenderedPage:
    image: Any
    width_points: float
    height_points: float


@dataclass(frozen=True)
class PageRegistration:
    homography: Any
    match_count: int
    inlier_count: int

    @property
    def reliable(self) -> bool:
        return (
            self.match_count >= 24
            and self.inlier_count >= 12
            and self.inlier_count / max(self.match_count, 1) >= 0.15
        )


@dataclass(frozen=True)
class _InkComponent:
    x0: int
    y0: int
    x1: int
    y1: int
    ink_pixels: int
    novel_pixels: int = 0

    @property
    def novel_ratio(self) -> float:
        return self.novel_pixels / max(self.ink_pixels, 1)


class FooterPageRenderer(Protocol):
    def render(self, pdf_path: str, page_no: int) -> RenderedPage | None: ...


class PageRegistrar(Protocol):
    def register(self, original: Any, compare: Any) -> PageRegistration: ...


class PyMuPdfPageRenderer:
    scale = 1.5

    def render(self, pdf_path: str, page_no: int) -> RenderedPage | None:
        try:
            import fitz
            import numpy as np
        except Exception:
            return None

        document = None
        try:
            document = fitz.open(pdf_path)
            if page_no < 1 or page_no > len(document):
                return None
            page = document[page_no - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(self.scale, self.scale), alpha=False)
            if pixmap.width <= 0 or pixmap.height <= 0 or pixmap.n not in (3, 4):
                return None
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                pixmap.n,
            )
            return RenderedPage(
                image=image[:, :, :3],
                width_points=float(page.rect.width),
                height_points=float(page.rect.height),
            )
        except Exception:
            return None
        finally:
            if document is not None:
                document.close()


class OpenCvPageRegistrar:
    max_features = 5000
    ratio_threshold = 0.72
    ransac_threshold = 4.0

    def register(self, original: Any, compare: Any) -> PageRegistration:
        try:
            import cv2
            import numpy as np

            original_gray = self._gray(original)
            compare_gray = self._gray(compare)
            detector = cv2.ORB_create(nfeatures=self.max_features, fastThreshold=10)
            original_points, original_descriptors = detector.detectAndCompute(original_gray, None)
            compare_points, compare_descriptors = detector.detectAndCompute(compare_gray, None)
            if original_descriptors is None or compare_descriptors is None:
                return PageRegistration(None, 0, 0)
            pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
                original_descriptors,
                compare_descriptors,
                k=2,
            )
            matches = [
                first
                for first, second in pairs
                if first.distance < self.ratio_threshold * second.distance
            ]
            if len(matches) < 8:
                return PageRegistration(None, len(matches), 0)
            source = np.float32([original_points[item.queryIdx].pt for item in matches])
            target = np.float32([compare_points[item.trainIdx].pt for item in matches])
            homography, inlier_mask = cv2.findHomography(
                source,
                target,
                cv2.RANSAC,
                self.ransac_threshold,
            )
            inlier_count = int(inlier_mask.sum()) if inlier_mask is not None else 0
            registration = PageRegistration(homography, len(matches), inlier_count)
            if not registration.reliable or not self._plausible_homography(
                homography,
                original_gray.shape,
                compare_gray.shape,
            ):
                return PageRegistration(None, len(matches), inlier_count)
            return registration
        except Exception:
            return PageRegistration(None, 0, 0)

    @staticmethod
    def _gray(image: Any) -> Any:
        import cv2

        if len(image.shape) == 2:
            return image
        return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

    @staticmethod
    def _plausible_homography(
        homography: Any,
        original_shape: tuple[int, ...],
        compare_shape: tuple[int, ...],
    ) -> bool:
        if homography is None:
            return False
        try:
            import cv2
            import numpy as np

            original_height, original_width = original_shape[:2]
            compare_height, compare_width = compare_shape[:2]
            corners = np.float32(
                [[0, 0], [original_width, 0], [original_width, original_height], [0, original_height]]
            ).reshape(-1, 1, 2)
            transformed = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
            area = abs(float(cv2.contourArea(transformed.astype(np.float32))))
            compare_area = float(compare_width * compare_height)
            if not 0.45 <= area / max(compare_area, 1.0) <= 1.65:
                return False
            margin_x = compare_width * 0.35
            margin_y = compare_height * 0.35
            return bool(
                np.all(transformed[:, 0] >= -margin_x)
                and np.all(transformed[:, 0] <= compare_width + margin_x)
                and np.all(transformed[:, 1] >= -margin_y)
                and np.all(transformed[:, 1] <= compare_height + margin_y)
            )
        except Exception:
            return False


class FooterAnnotationVisualComparator:
    """Find handwritten lower-left footer annotations after page registration."""

    roi_left = 0.02
    roi_right = 0.34
    roi_top = 0.915
    roi_bottom = 0.99
    source_ink_threshold = 240
    source_seed_threshold = 160
    aligned_ink_threshold = 190
    min_ink_pixels = 15
    min_novel_pixels = 15
    min_secondary_novel_pixels = 30
    min_secondary_width_pixels = 8
    min_faint_width_pixels = 20
    min_novel_ratio = 0.45
    dense_column_ink_ratio = 0.35
    dense_column_min_pixels = 25
    dense_column_padding_pixels = 5
    annotation_horizontal_gap_height_ratio = 1.75
    ocr_overlap_coverage_threshold = 0.60
    ocr_horizontal_coverage_threshold = 0.60
    ocr_vertical_coverage_threshold = 0.50
    visual_containment_threshold = 0.90
    contained_visual_horizontal_ratio = 0.45
    contained_visual_vertical_ratio = 0.60
    visual_flag = "VISUAL_FOOTER_ANNOTATION"

    def __init__(
        self,
        renderer: FooterPageRenderer | None = None,
        registrar: PageRegistrar | None = None,
    ) -> None:
        self.renderer = renderer or PyMuPdfPageRenderer()
        self.registrar = registrar or OpenCvPageRegistrar()

    def build_diffs(
        self,
        original: Document,
        compare: Document,
        *,
        start_index: int = 1,
    ) -> list[DiffItem]:
        original_pages = {page.page_no for page in original.pages}
        compare_pages = {page.page_no for page in compare.pages}
        diffs: list[DiffItem] = []
        next_index = start_index
        for page_no in sorted(original_pages & compare_pages):
            original_page = self.renderer.render(str(original.path), page_no)
            compare_page = self.renderer.render(str(compare.path), page_no)
            if original_page is None or compare_page is None:
                continue
            page_diffs = self._page_diffs(
                original_page,
                compare_page,
                page_no=page_no,
                start_index=next_index,
            )
            diffs.extend(page_diffs)
            next_index += len(page_diffs)
        return diffs

    def remove_overlapping_ocr_diffs(
        self,
        ocr_diffs: list[DiffItem],
        visual_diffs: list[DiffItem],
    ) -> list[DiffItem]:
        visual_original = self._group_visual_evidences(
            [diff.original_evidence for diff in visual_diffs]
        )
        visual_compare = self._group_visual_evidences(
            [diff.compare_evidence for diff in visual_diffs]
        )
        kept: list[DiffItem] = []
        for source in ocr_diffs:
            if self.visual_flag in source.review_flags:
                kept.append(source)
                continue
            diff = source.model_copy(deep=True)
            had_original_evidence = bool(diff.original_evidence)
            had_compare_evidence = bool(diff.compare_evidence)
            if diff.diff_type in {"DELETE", "MODIFY"}:
                diff.original_evidence = self._without_overlaps(diff.original_evidence, visual_original)
            if diff.diff_type in {"ADD", "MODIFY"}:
                diff.compare_evidence = self._without_overlaps(diff.compare_evidence, visual_compare)
            if diff.diff_type == "ADD" and had_compare_evidence and not diff.compare_evidence:
                continue
            if diff.diff_type == "DELETE" and had_original_evidence and not diff.original_evidence:
                continue
            if (
                diff.diff_type == "MODIFY"
                and (had_original_evidence or had_compare_evidence)
                and not diff.original_evidence
                and not diff.compare_evidence
            ):
                continue
            kept.append(diff)
        return kept

    def remove_overlapping_diffs(self, diffs: list[DiffItem]) -> list[DiffItem]:
        visual_diffs = [diff for diff in diffs if self.visual_flag in diff.review_flags]
        if not visual_diffs:
            return diffs
        non_visual_diffs = [diff for diff in diffs if self.visual_flag not in diff.review_flags]
        filtered = self.remove_overlapping_ocr_diffs(non_visual_diffs, visual_diffs)
        filtered_by_id = {diff.diff_id: diff for diff in filtered}
        return [
            diff if self.visual_flag in diff.review_flags else filtered_by_id[diff.diff_id]
            for diff in diffs
            if self.visual_flag in diff.review_flags or diff.diff_id in filtered_by_id
        ]

    @staticmethod
    def _group_visual_evidences(
        evidence_groups: list[list[EvidenceBox]],
    ) -> list[EvidenceBox]:
        grouped: list[EvidenceBox] = []
        for evidences in evidence_groups:
            by_page: dict[int, list[EvidenceBox]] = {}
            for evidence in evidences:
                by_page.setdefault(evidence.page_no, []).append(evidence)
            for page_evidences in by_page.values():
                first = page_evidences[0]
                bbox = BBox(
                    x0=min(evidence.bbox.x0 for evidence in page_evidences),
                    y0=min(evidence.bbox.y0 for evidence in page_evidences),
                    x1=max(evidence.bbox.x1 for evidence in page_evidences),
                    y1=max(evidence.bbox.y1 for evidence in page_evidences),
                )
                grouped.append(first.model_copy(deep=True, update={"bbox": bbox}))
        return grouped

    def _page_diffs(
        self,
        original: RenderedPage,
        compare: RenderedPage,
        *,
        page_no: int,
        start_index: int,
    ) -> list[DiffItem]:
        try:
            import cv2
            import numpy as np

            original_image = original.image
            compare_image = compare.image
            compare_height, compare_width = compare_image.shape[:2]
            if original_image.shape[:2] != compare_image.shape[:2]:
                original_image = cv2.resize(original_image, (compare_width, compare_height))
            registration = self.registrar.register(original_image, compare_image)
            if not registration.reliable or registration.homography is None:
                return []
            aligned_original = cv2.warpPerspective(
                original_image,
                registration.homography,
                (compare_width, compare_height),
                borderValue=(255, 255, 255),
            )
            inverse = np.linalg.inv(registration.homography)
            aligned_compare = cv2.warpPerspective(
                compare_image,
                inverse,
                (compare_width, compare_height),
                borderValue=(255, 255, 255),
            )
        except Exception:
            return []

        compare_components = self._annotation_regions(
            self._one_sided_components(compare_image, aligned_original)
        )
        original_components = self._annotation_regions(
            self._one_sided_components(original_image, aligned_compare)
        )
        diffs: list[DiffItem] = []
        next_index = start_index
        if original_components:
            diffs.append(
                self._make_diff(
                    original_components,
                    original,
                    page_no=page_no,
                    diff_type="DELETE",
                    index=next_index,
                    registration=registration,
                )
            )
            next_index += 1
        if compare_components:
            diffs.append(
                self._make_diff(
                    compare_components,
                    compare,
                    page_no=page_no,
                    diff_type="ADD",
                    index=next_index,
                    registration=registration,
                )
            )
        return diffs

    def _annotation_regions(self, components: list[_InkComponent]) -> list[_InkComponent]:
        if not components:
            return []
        grouped: list[_InkComponent] = []
        for component in sorted(components, key=lambda item: (item.x0, item.y0)):
            if grouped and self._same_annotation_region(grouped[-1], component):
                grouped[-1] = self._merge_components(grouped[-1], component)
            else:
                grouped.append(component)

        strongest = max(grouped, key=self._component_score)
        retained = [
            component
            for component in grouped
            if component == strongest
            or (
                component.novel_pixels >= self.min_secondary_novel_pixels
                and component.x1 - component.x0 >= self.min_secondary_width_pixels
            )
        ]
        return sorted(retained, key=lambda item: (item.x0, item.y0))

    @classmethod
    def _same_annotation_region(cls, left: _InkComponent, right: _InkComponent) -> bool:
        left_height = left.y1 - left.y0
        right_height = right.y1 - right.y0
        max_height = max(left_height, right_height)
        left_center = (left.y0 + left.y1) / 2
        right_center = (right.y0 + right.y1) / 2
        horizontal_gap = max(0, right.x0 - left.x1)
        return (
            abs(left_center - right_center) <= max_height * 0.5
            and horizontal_gap
            <= max(18, int(max_height * cls.annotation_horizontal_gap_height_ratio))
        )

    @staticmethod
    def _merge_components(left: _InkComponent, right: _InkComponent) -> _InkComponent:
        return _InkComponent(
            x0=min(left.x0, right.x0),
            y0=min(left.y0, right.y0),
            x1=max(left.x1, right.x1),
            y1=max(left.y1, right.y1),
            ink_pixels=left.ink_pixels + right.ink_pixels,
            novel_pixels=left.novel_pixels + right.novel_pixels,
        )

    def _one_sided_components(self, source: Any, aligned_opposite: Any) -> list[_InkComponent]:
        try:
            import cv2
            import numpy as np

            source_mask = self._ink_mask(source, self.source_ink_threshold)
            source_seed_mask = self._ink_mask(source, self.source_seed_threshold)
            opposite_mask = self._ink_mask(aligned_opposite, self.aligned_ink_threshold)
            opposite_mask = cv2.dilate(opposite_mask, np.ones((7, 7), dtype=np.uint8))
            candidates = self._candidate_components(source_mask)
            result: list[_InkComponent] = []
            for component in candidates:
                seed_pixels = int(
                    np.count_nonzero(
                        source_seed_mask[
                            component.y0 : component.y1,
                            component.x0 : component.x1,
                        ]
                    )
                )
                if (
                    seed_pixels < self.min_ink_pixels
                    and component.x1 - component.x0 < self.min_faint_width_pixels
                ):
                    continue
                source_area = source_mask[component.y0 : component.y1, component.x0 : component.x1] > 0
                opposite_area = opposite_mask[component.y0 : component.y1, component.x0 : component.x1] > 0
                novel_pixels = int(np.count_nonzero(source_area & ~opposite_area))
                enriched = _InkComponent(
                    x0=component.x0,
                    y0=component.y0,
                    x1=component.x1,
                    y1=component.y1,
                    ink_pixels=component.ink_pixels,
                    novel_pixels=novel_pixels,
                )
                if (
                    enriched.novel_pixels >= self.min_novel_pixels
                    and enriched.novel_ratio >= self.min_novel_ratio
                ):
                    result.append(enriched)
            return result
        except Exception:
            return []

    def _candidate_components(self, ink_mask: Any) -> list[_InkComponent]:
        import cv2
        import numpy as np

        height, width = ink_mask.shape[:2]
        x0 = int(width * self.roi_left)
        x1 = int(width * self.roi_right)
        y0 = int(height * self.roi_top)
        y1 = int(height * self.roi_bottom)
        roi_mask = np.zeros_like(ink_mask)
        roi_mask[y0:y1, x0:x1] = ink_mask[y0:y1, x0:x1]
        roi_view = roi_mask[y0:y1, x0:x1]
        column_ink_counts = np.count_nonzero(roi_view, axis=0)
        dense_column_threshold = max(
            self.dense_column_min_pixels,
            int((y1 - y0) * self.dense_column_ink_ratio),
        )
        dense_columns = (column_ink_counts >= dense_column_threshold).astype(np.uint8)
        if np.any(dense_columns):
            dense_columns = cv2.dilate(
                dense_columns.reshape(1, -1),
                np.ones((1, self.dense_column_padding_pixels * 2 + 1), dtype=np.uint8),
            ).reshape(-1)
            roi_view[:, dense_columns > 0] = 0
        edge_padding = max(2, int(min(width, height) * 0.003))
        roi_mask[:, : x0 + edge_padding] = 0
        roi_mask[:, x1 - edge_padding :] = 0
        roi_mask[: y0 + edge_padding, :] = 0
        roi_mask[y1 - edge_padding :, :] = 0
        vertical_length = max(25, int(height * 0.04))
        horizontal_length = max(40, int(width * 0.08))
        vertical_lines = cv2.morphologyEx(
            ink_mask,
            cv2.MORPH_OPEN,
            np.ones((vertical_length, 1), dtype=np.uint8),
        )
        horizontal_lines = cv2.morphologyEx(
            ink_mask,
            cv2.MORPH_OPEN,
            np.ones((1, horizontal_length), dtype=np.uint8),
        )
        scan_lines = cv2.dilate(
            cv2.bitwise_or(vertical_lines, horizontal_lines),
            np.ones((3, 3), dtype=np.uint8),
        )
        roi_mask = cv2.bitwise_and(roi_mask, cv2.bitwise_not(scan_lines))
        roi_mask = cv2.morphologyEx(
            roi_mask,
            cv2.MORPH_CLOSE,
            np.ones((3, 3), dtype=np.uint8),
        )
        grouped = cv2.dilate(roi_mask, np.ones((7, 11), dtype=np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(grouped)
        components: list[_InkComponent] = []
        for label in range(1, count):
            group = labels == label
            source_pixels = np.argwhere(group & (roi_mask > 0))
            if not len(source_pixels):
                continue
            ys = source_pixels[:, 0]
            xs = source_pixels[:, 1]
            component = _InkComponent(
                x0=int(xs.min()),
                y0=int(ys.min()),
                x1=int(xs.max()) + 1,
                y1=int(ys.max()) + 1,
                ink_pixels=int(len(source_pixels)),
            )
            if self._plausible_component(component, width, height, stats[label]):
                components.append(component)
        return components

    def _plausible_component(
        self,
        component: _InkComponent,
        page_width: int,
        page_height: int,
        grouped_stats: Any,
    ) -> bool:
        width = component.x1 - component.x0
        height = component.y1 - component.y0
        grouped_width = int(grouped_stats[2])
        grouped_height = int(grouped_stats[3])
        if component.ink_pixels < self.min_ink_pixels or width < 2 or height < 4:
            return False
        if width > max(40, page_width * 0.13) or height > max(30, page_height * 0.06):
            return False
        if height > width * 8 or width > height * 8:
            return False
        if grouped_width > page_width * 0.16 or grouped_height > page_height * 0.12:
            return False
        return True

    def _make_diff(
        self,
        components: list[_InkComponent],
        rendered: RenderedPage,
        *,
        page_no: int,
        diff_type: DiffType,
        index: int,
        registration: PageRegistration,
    ) -> DiffItem:
        label = "新增" if diff_type == "ADD" else "删除"
        text = "检测到左下角手写签注"
        evidences = [
            self._make_evidence(
                component,
                rendered,
                page_no=page_no,
                diff_type=diff_type,
                registration=registration,
                text=text,
            )
            for component in components
        ]
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=diff_type,
            title=f"第{page_no}页左下角手写签注",
            original_text=text if diff_type == "DELETE" else "",
            compare_text=text if diff_type == "ADD" else "",
            original_snippet=text if diff_type == "DELETE" else "",
            compare_snippet=text if diff_type == "ADD" else "",
            readable_change=f"{label}左下角手写签注：第{page_no}页",
            source_type="header_footer",
            review_flags=["HEADER_FOOTER_REVIEW", self.visual_flag],
            quality_status="NEEDS_REVIEW",
            original_evidence=evidences if diff_type == "DELETE" else [],
            compare_evidence=evidences if diff_type == "ADD" else [],
        )

    @staticmethod
    def _make_evidence(
        component: _InkComponent,
        rendered: RenderedPage,
        *,
        page_no: int,
        diff_type: DiffType,
        registration: PageRegistration,
        text: str,
    ) -> EvidenceBox:
        image_height, image_width = rendered.image.shape[:2]
        bbox = BBox(
            x0=component.x0 / image_width * rendered.width_points,
            y0=component.y0 / image_height * rendered.height_points,
            x1=component.x1 / image_width * rendered.width_points,
            y1=component.y1 / image_height * rendered.height_points,
        )
        confidence = min(
            0.97,
            0.62
            + component.novel_ratio * 0.22
            + registration.inlier_count / max(registration.match_count, 1) * 0.13,
        )
        return EvidenceBox(
            page_no=page_no,
            bbox=bbox,
            method="footer_visual_registered",
            text=text,
            highlight_type=diff_type,
            confidence=round(confidence, 3),
            evidence_quality="HIGH" if confidence >= 0.82 else "MEDIUM",
        )

    @staticmethod
    def _gray(image: Any) -> Any:
        import cv2

        if len(image.shape) == 2:
            return image
        return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

    @classmethod
    def _ink_mask(cls, image: Any, threshold: int) -> Any:
        """Keep neutral ink while excluding colored scan-capture artifacts."""
        import cv2
        import numpy as np

        gray = cls._gray(image)
        dark = gray < threshold
        if len(image.shape) == 2:
            return dark.astype(np.uint8) * 255

        hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2HSV)
        saturation = hsv[:, :, 1]
        neutral = saturation < 80
        return (dark & neutral).astype(np.uint8) * 255

    @staticmethod
    def _component_score(component: _InkComponent) -> tuple[int, float]:
        return component.novel_pixels, component.novel_ratio

    @classmethod
    def _without_overlaps(
        cls,
        evidences: list[EvidenceBox],
        visual_evidences: list[EvidenceBox],
    ) -> list[EvidenceBox]:
        return [
            evidence
            for evidence in evidences
            if not any(
                evidence.page_no == visual.page_no and cls._bbox_overlaps(evidence.bbox, visual.bbox)
                for visual in visual_evidences
            )
        ]

    @classmethod
    def _bbox_overlaps(cls, left: BBox, right: BBox) -> bool:
        intersection_width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
        intersection_height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
        intersection = intersection_width * intersection_height
        left_width = max(1.0, left.x1 - left.x0)
        left_height = max(1.0, left.y1 - left.y0)
        right_width = max(1.0, right.x1 - right.x0)
        right_height = max(1.0, right.y1 - right.y0)
        area_coverage = intersection / (left_width * left_height)
        horizontal_coverage = intersection_width / left_width
        vertical_coverage = intersection_height / left_height
        visual_horizontal_coverage = intersection_width / right_width
        visual_vertical_coverage = intersection_height / right_height
        return area_coverage >= cls.ocr_overlap_coverage_threshold or (
            horizontal_coverage >= cls.ocr_horizontal_coverage_threshold
            and vertical_coverage >= cls.ocr_vertical_coverage_threshold
        ) or (
            visual_horizontal_coverage >= cls.visual_containment_threshold
            and visual_vertical_coverage >= cls.visual_containment_threshold
            and horizontal_coverage >= cls.contained_visual_horizontal_ratio
            and vertical_coverage >= cls.contained_visual_vertical_ratio
        )
