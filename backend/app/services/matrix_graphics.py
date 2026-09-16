"""Compare located matrix-code graphics without trusting OCR or successful decoding.

Finder geometry proposes objects; image evidence decides whether an observable
counterpart exists. Low-contrast remnants never become a certain insertion.
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from itertools import combinations
from pathlib import Path

import cv2
import fitz
import numpy as np

from app.models import BBox, DiffItem, EvidenceBox
from app.services.page_render_cache import PageRenderCache
from app.services.ordered_analysis import ordered_analysis
from app.services.pipeline_metrics import PerformanceRecorder


class MatrixGraphicComparator:
    scale = 3.0

    def __init__(self, render_cache=None, *, max_inflight: int = 2):
        self.render_cache = render_cache
        self.max_inflight = max_inflight
        self.performance = PerformanceRecorder()

    @staticmethod
    def candidates(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        finders = []
        for threshold in (220, 240, 250):
            contours, hierarchy = cv2.findContours(
                np.uint8(gray < threshold) * 255, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
            )
            if hierarchy is None:
                continue
            for index, contour in enumerate(contours):
                child = hierarchy[0, index, 2]
                if child < 0 or hierarchy[0, child, 2] < 0:
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                if not (7 <= w <= 100 and 7 <= h <= 100 and 0.75 < w / h < 1.33):
                    continue
                grandchild = hierarchy[0, child, 2]
                area = max(1, cv2.contourArea(contour))
                ratios = [cv2.contourArea(contours[j]) / area for j in (child, grandchild)]
                centers = []
                for j in (child, grandchild):
                    cx, cy, cw, ch = cv2.boundingRect(contours[j])
                    centers.append(np.array([cx + cw / 2, cy + ch / 2]))
                center = np.array([x + w / 2, y + h / 2])
                if (
                    not 0.25 < ratios[0] < 0.85
                    or not 0.05 < ratios[1] < 0.5
                    or any(np.linalg.norm(c - center) > max(w, h) * 0.15 for c in centers)
                ):
                    continue
                approx = cv2.approxPolyDP(contour, 0.05 * cv2.arcLength(contour, True), True)
                if len(approx) != 4:
                    continue
                center = np.array([x + w / 2, y + h / 2])
                if not any(np.linalg.norm(center - f[0]) < max(w, h) / 2 for f in finders):
                    finders.append((center, (w + h) / 2))
        boxes = []
        for corner, size in finders:
            neighbors = [
                (p, s)
                for p, s in finders
                if 0.65 < s / size < 1.55 and 1.5 * size < np.linalg.norm(p - corner) < 25 * size
            ]
            for (a, _), (b, _) in combinations(neighbors, 2):
                va, vb = a - corner, b - corner
                da, db = np.linalg.norm(va), np.linalg.norm(vb)
                if abs(np.dot(va, vb)) / (da * db) > 0.15 or not 0.8 < da / db < 1.25:
                    continue
                points = np.array([corner, a, b, a + b - corner])
                lo = np.floor(points.min(axis=0) - size / 2 - 1).astype(int)
                hi = np.ceil(points.max(axis=0) + size / 2 + 1).astype(int)
                x0, y0 = np.maximum(lo, 0)
                x1, y1 = np.minimum(hi, [gray.shape[1], gray.shape[0]])
                crop = gray[y0:y1, x0:x1]
                # Reject sparse table corners, outlines and most nested text.
                if not crop.size or not 0.08 < np.mean(crop < 240) < 0.8:
                    continue
                ink = crop < 240
                if np.mean(ink.sum(axis=1) == 0) > 0.12 or np.mean(ink.sum(axis=0) == 0) > 0.12:
                    continue
                grid = [part.mean() for row in np.array_split(ink, 4) for part in np.array_split(row, 4, axis=1)]
                if min(grid) < 0.05 or np.std(grid) / max(0.01, np.mean(grid)) > 0.55:
                    continue
                components = cv2.connectedComponents(np.uint8(crop < 235))[0]
                if components < 20:
                    continue
                box = (int(x0), int(y0), int(x1), int(y1))
                if not any(_iou(box, old) > 0.5 for old in boxes):
                    boxes.append(box)
        return sorted(boxes, key=lambda b: (b[1], b[0]))

    def compare(self, original_path: Path, compare_path: Path, alignment) -> tuple[list[DiffItem], list[dict]]:
        diffs, observations = [], []
        if not original_path.is_file() or not compare_path.is_file() or alignment is None:
            return diffs, [{"decision": "UNOBSERVED", "reason": "MISSING_INPUT_OR_ALIGNMENT"}]
        with fitz.open(original_path) as original, fitz.open(compare_path) as compare:
            docs = {"original": original, "compare": compare}
            cache = self.render_cache or PageRenderCache()

            def gray(side, page):
                return cache.render(docs[side][page - 1], scale=self.scale, colorspace=fitz.csGRAY)

            def inputs():
                for side, document in docs.items():
                    for page in range(1, len(document) + 1):
                        with self.performance.measure("page_render"):
                            image = gray(side, page)
                        yield side, page, image

            def analyze(item):
                side, page, image = item
                with self.performance.measure("candidate_detection"):
                    boxes = self.candidates(image)
                self.performance.increment("pages_scanned")
                return side, page, image, boxes

            seen_pairs = []
            self.performance.set_counter("analysis_max_inflight", self.max_inflight)
            with closing(ordered_analysis(analyze, inputs(), self.max_inflight)) as analyzed:
                for side, page, image, boxes in analyzed:
                    other = "compare" if side == "original" else "original"
                    entry = (
                        alignment.entry_for_original(page) if side == "original" else alignment.entry_for_compare(page)
                    )
                    targets = getattr(entry, f"{other}_pages", ()) if entry else ()
                    # A blank leading member of the next merge can explain an
                    # otherwise unpaired page. Require a single blank candidate
                    # bounded by neighboring alignment entries, never page number equality.
                    if entry and not targets:
                        position = alignment.entries.index(entry)
                        following = alignment.entries[position + 1] if position + 1 < len(alignment.entries) else None
                        previous = alignment.entries[position - 1] if position else None
                        if following and previous and len(getattr(following, f"{other}_pages")) > 1:
                            possible = getattr(following, f"{other}_pages")[:-1]
                            before = getattr(previous, f"{other}_pages")
                            blank = [
                                p
                                for p in possible
                                if before and p == max(before) + 1 and np.mean(gray(other, p) < 250) < 0.001
                            ]
                            if len(blank) == 1:
                                targets = tuple(blank)

                    for box in boxes:
                        observation = {
                            "side": side,
                            "page": page,
                            "bbox": [v / self.scale for v in box],
                            "object_type": "matrix_code",
                        }
                        if not entry or not targets or entry.alignment_score < 0.46:
                            observations.append(
                                {**observation, "decision": "UNOBSERVED", "reason": "NO_UNAMBIGUOUS_PAGE_PAIR"}
                            )
                            continue
                        candidates = [(p, self.counterpart(image, box, gray(other, p))) for p in targets]
                        target_page, evidence = max(candidates, key=lambda item: item[1].get("correlation", 0))
                        if len(targets) > 1:
                            evidence = {**evidence, "candidate_pages": list(targets)}
                            if evidence["decision"] != "SAME":
                                evidence.update(decision="UNOBSERVED", reason="MULTIPAGE_COUNTERPART_UNRESOLVED")
                        key = (page, target_page) if side == "original" else (target_page, page)
                        # Suppress reverse observations of a matched physical object.
                        source_box = tuple(v / self.scale for v in box)
                        counterpart_box = tuple(evidence.get("counterpart_bbox", source_box))
                        original_box, compare_box = (
                            (source_box, counterpart_box) if side == "original" else (counterpart_box, source_box)
                        )
                        if any(
                            k == key and _iou(original_box, ob) > 0.5 and _iou(compare_box, cb) > 0.5
                            for k, ob, cb in seen_pairs
                        ):
                            continue
                        seen_pairs.append((key, original_box, compare_box))
                        observation.update(counterpart_page=target_page, **evidence)
                        observations.append(observation)
                        if evidence["decision"] not in {"ABSENT", "CHANGED"}:
                            continue
                        direction = (
                            "MODIFY" if evidence["decision"] == "CHANGED" else "DELETE" if side == "original" else "ADD"
                        )
                        bbox = BBox(
                            x0=box[0] / self.scale,
                            y0=box[1] / self.scale,
                            x1=box[2] / self.scale,
                            y1=box[3] / self.scale,
                        )
                        graphic = DiffItem(
                            diff_id=f"GRAPHIC-{side}-{page}-{box[0]}-{box[1]}",
                            diff_type=direction,
                            source_type="graphic",
                            title="矩阵码图形",
                            quality_status="NORMAL",
                            match_method="matrix_finders_and_blank_counterpart",
                            match_score_details=observation,
                            **{
                                f"{side}_text": "矩阵码图形",
                                f"{side}_evidence": [
                                    EvidenceBox(
                                        page_no=page,
                                        bbox=bbox,
                                        text="矩阵码图形",
                                        method="matrix_finder_geometry",
                                        confidence=0.95,
                                        evidence_quality="HIGH",
                                        highlight_type=direction,
                                    )
                                ],
                            },
                        )
                        if direction == "MODIFY":
                            other_box = BBox(
                                x0=evidence["counterpart_bbox"][0],
                                y0=evidence["counterpart_bbox"][1],
                                x1=evidence["counterpart_bbox"][2],
                                y1=evidence["counterpart_bbox"][3],
                            )
                            setattr(graphic, f"{other}_text", "矩阵码图形")
                            setattr(
                                graphic,
                                f"{other}_evidence",
                                [
                                    EvidenceBox(
                                        page_no=target_page,
                                        bbox=other_box,
                                        text="矩阵码图形",
                                        method="decoded_matrix_content",
                                        confidence=0.95,
                                        evidence_quality="HIGH",
                                        highlight_type="MODIFY",
                                    )
                                ],
                            )
                        diffs.append(graphic)
        return diffs, observations

    @staticmethod
    def counterpart(image: np.ndarray, box, target: np.ndarray) -> dict:
        x0, y0, x1, y1 = box
        sx, sy = target.shape[1] / image.shape[1], target.shape[0] / image.shape[0]
        template = cv2.resize(image[y0:y1, x0:x1], (max(1, round((x1 - x0) * sx)), max(1, round((y1 - y0) * sy))))
        padding = 90  # 30 display PDF points at the fixed raster scale.
        rx, ry = max(0, round(x0 * sx) - padding), max(0, round(y0 * sy) - padding)
        ex, ey = min(target.shape[1], round(x1 * sx) + padding), min(target.shape[0], round(y1 * sy) + padding)
        search = target[ry:ey, rx:ex]
        if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
            return {"decision": "UNOBSERVED", "reason": "CLIPPED_COUNTERPART"}
        scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, correlation, _, location = cv2.minMaxLoc(scores)
        patch = search[location[1] : location[1] + template.shape[0], location[0] : location[0] + template.shape[1]]
        # Blankness is checked across the entire search window, not a convenient
        # blank patch selected by low correlation. Scanned remnants count as ink.
        ink_ratio = float(np.mean(search < 250))
        result = {
            "correlation": round(correlation, 4),
            "counterpart_ink_ratio": round(ink_ratio, 6),
            "counterpart_std": round(float(patch.std()), 4),
            "counterpart_bbox": [
                (rx + location[0]) / 3,
                (ry + location[1]) / 3,
                (rx + location[0] + template.shape[1]) / 3,
                (ry + location[1] + template.shape[0]) / 3,
            ],
        }

        def decode(a):
            padded = cv2.copyMakeBorder(a, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
            return cv2.QRCodeDetector().detectAndDecode(padded)[0]

        left_value, right_value = decode(template), decode(patch)
        if left_value and right_value:
            result["payload_hashes"] = [hashlib.sha256(v.encode()).hexdigest() for v in (left_value, right_value)]
            return {
                **result,
                "decision": "SAME" if left_value == right_value else "CHANGED",
                "reason": "DECODED_MATRIX_CONTENT",
            }
        if correlation >= 0.7:
            return {**result, "decision": "SAME", "reason": "SAME_GRAPHIC_DESPITE_CONTRAST"}
        if ink_ratio <= 0.0002 and float(search.std()) < 1:
            return {**result, "decision": "ABSENT", "reason": "OBSERVABLE_BLANK_COUNTERPART"}
        return {**result, "decision": "UNOBSERVED", "reason": "VISIBLE_COUNTERPART_CONTENT_UNRESOLVED"}


def _iou(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return area / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - area)
