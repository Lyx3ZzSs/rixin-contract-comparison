from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock, TextRange
from app.services.page_alignment import PageAlignmentResult
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType


_SIGNING_MARKER_RE = re.compile(r"甲方|乙方|委托人|承包人|发包人|承包方|采购人|供应商|法定代表|授权代表|委托代理|签字|签名|盖章|公章|签订日期|签字日期")
_PARTICIPANT_ANCHOR_RE = re.compile(r"^\s*参与人员\s*[:：]")
_SIGNATURE_ANCHOR_RE = re.compile(r"法定代表|授权代表|委托代理|代表.{0,4}(?:签字|签名)|签字|签名")
_VISUAL_STAMP_CONTEXT_RE = re.compile(r"盖章|公章|印章|签字|签名|签署|法定代表|授权代表|审批|审核|验收")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})\s*(?:年|[-./])\s*(\d{1,2})\s*(?:月|[-./])\s*(\d{1,2})\s*日?")
_PARTY_PATTERNS = (
    (re.compile(r"设计方|设计单位"), "设计方"),
    (re.compile(r"买方"), "买方"),
    (re.compile(r"卖方"), "卖方"),
    (re.compile(r"甲方|委托人|发包人|采购人"), "甲方"),
    (re.compile(r"乙方|承包人|承包方|供应商"), "乙方"),
)
_PARTY_ANCHOR_RE = re.compile(
    r"(?P<label>设计方|设计单位|买\s*方|卖\s*方|甲方|乙方|委托人|承包人|发包人|承包方|采购人|供应商)\s*(?=[:：（(])"
)


@dataclass
class SigningVisualRecallResult:
    original_objects: list[SigningVisualObject] = field(default_factory=list)
    compare_objects: list[SigningVisualObject] = field(default_factory=list)
    additions: list[SigningVisualObject] = field(default_factory=list)
    matched_pairs: list[tuple[SigningVisualObject, SigningVisualObject]] = field(default_factory=list)
    reconciled_diff_ids: list[str] = field(default_factory=list)
    removed_duplicate_diff_ids: list[str] = field(default_factory=list)
    new_diff_ids: list[str] = field(default_factory=list)

    def to_debug_payload(self) -> dict[str, Any]:
        return {
            "original_objects": [item.model_dump(mode="json") for item in self.original_objects],
            "compare_objects": [item.model_dump(mode="json") for item in self.compare_objects],
            "additions": [item.model_dump(mode="json") for item in self.additions],
            "matched_pairs": [[a.model_dump(mode="json"), b.model_dump(mode="json")] for a, b in self.matched_pairs],
            "reconciled_diff_ids": self.reconciled_diff_ids,
            "removed_duplicate_diff_ids": self.removed_duplicate_diff_ids,
            "new_diff_ids": self.new_diff_ids,
        }


class WholePageSigningVisualRecall:
    """Low-resolution, whole-page recall for signing marks.

    The detector deliberately produces visual candidates first.  Confirmation is
    decided later, after counterpart matching and signing-context attribution.
    """

    render_scale = 1.5

    def __init__(self):
        self.render_cache = None

    def detect(self, document: Document) -> list[SigningVisualObject]:
        deps = self._dependencies()
        pdf_path = Path(document.path)
        if deps is None or not pdf_path.is_file():
            return []
        cv2, np = deps
        pages = {page.page_no: page for page in document.pages}
        objects: list[SigningVisualObject] = []
        pdf: fitz.Document | None = None
        try:
            pdf = fitz.open(pdf_path)
            for page_no in sorted(pages):
                if not 1 <= page_no <= len(pdf):
                    continue
                model_page = pages[page_no]
                image = self._render_page(pdf[page_no - 1], np)
                if image is None:
                    continue
                red_objects = self._red_objects(model_page, image, cv2, np)
                for item in red_objects:
                    item.raw_ref.setdefault("document_page_count", document.page_count)
                date_objects = self._date_objects(model_page, image, cv2, np)
                self._inherit_party_from_nearby_seals(date_objects, red_objects)
                signature_objects = self._signature_objects(model_page, image, red_objects, date_objects, cv2, np)
                objects.extend([*red_objects, *date_objects, *signature_objects])
        except Exception:
            return objects
        finally:
            if pdf is not None:
                pdf.close()
        return self._dedupe_objects(objects)

    def compare(
        self,
        original: Document,
        compare: Document,
        *,
        page_alignment: PageAlignmentResult | None = None,
    ) -> SigningVisualRecallResult:
        original_objects = self.detect(original)
        compare_objects = self.detect(compare)
        matched_original: set[int] = set()
        matched_pairs = []
        additions: list[SigningVisualObject] = []
        for compare_object in compare_objects:
            match = self._best_counterpart(
                compare_object,
                compare,
                original_objects,
                original,
                page_alignment=page_alignment,
                used=matched_original,
            )
            if match is None and compare_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
                pages = (page_alignment.counterparts_for_compare(compare_object.page_no)
                         if page_alignment is not None else [compare_object.page_no])
                recalled = self._recall_inset_edges(original, set(pages))
                for item in recalled:
                    if not any(item.page_no == old.page_no and self._bbox_overlap_ratio(item.bbox, old.bbox) >= 0.6
                               for old in original_objects):
                        original_objects.append(item)
                match = self._best_counterpart(compare_object, compare, original_objects, original,
                                               page_alignment=page_alignment, used=matched_original)
            if match is None:
                if compare_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL and any(
                    item.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL for item in original_objects
                ):
                    # A shifted/cropped fragment is not proof of an additional seal.
                    compare_object.raw_ref["counterpart_edge_present"] = True
                additions.append(compare_object)
            else:
                matched_original.add(match)
                matched_pairs.append((original_objects[match], compare_object))
        additions = self._aggregate_edge_fragments(additions)
        return SigningVisualRecallResult(
            original_objects=original_objects,
            compare_objects=compare_objects,
            additions=additions,
            matched_pairs=matched_pairs,
        )

    def reconcile_diffs(
        self,
        result: SigningVisualRecallResult,
        seal_diffs: list[DiffItem],
        signing_diffs: list[DiffItem],
        *,
        start_index: int,
    ) -> tuple[list[DiffItem], list[DiffItem]]:
        seals = list(seal_diffs)
        signing = list(signing_diffs)
        for left, right in result.matched_pairs:
            if not right.raw_ref.get("fragment_counterpart_proof", {}).get("equivalent"):
                continue
            retained = []
            for diff in signing:
                obj = right if diff.diff_type == "ADD" else left
                evidence = diff.compare_evidence if diff.diff_type == "ADD" else diff.original_evidence
                generic_presence = (diff.diff_type in {"ADD", "DELETE"} and diff.section_type == "signature:seal"
                                    and (diff.compare_text if diff.diff_type == "ADD" else diff.original_text) == "seal")
                if generic_presence and evidence and all(e.page_no == obj.page_no
                        and self._bbox_overlap_ratio(e.bbox, obj.bbox) >= .98 for e in evidence):
                    result.removed_duplicate_diff_ids.append(diff.diff_id)
                else:
                    retained.append(diff)
            signing = retained
        next_index = start_index
        for visual_object in result.additions:
            if (visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL
                    and visual_object.fragment_bboxes
                    and all(box.x1-box.x0 <= 2.5 for box in visual_object.fragment_bboxes)):
                # Sub-2.5-point scan-edge slivers have no observable seal
                # structure. Preserve the detector candidate for diagnosis,
                # but do not manufacture a business ADD from it.
                visual_object.raw_ref['change_decision'] = 'UNOBSERVED'
                visual_object.raw_ref['decision_reason'] = 'SCAN_EDGE_SLIVERS_WITHOUT_SEAL_STRUCTURE'
                continue
            matches = self._overlapping_diffs(visual_object, [*seals, *signing])
            if matches:
                if visual_object.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL:
                    matches = [item for item in matches if self._same_physical_object(visual_object, item)]
                if not matches:
                    new_diff = self._new_diff(visual_object, next_index)
                    next_index += 1
                    signing.append(new_diff)
                    result.new_diff_ids.append(new_diff.diff_id)
                    continue
                canonical = self._canonical_diff(visual_object, matches)
                if visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
                    canonical.compare_evidence = [e for item in matches for e in item.compare_evidence]
                source_evidence = [
                    {"diff_id": item.diff_id, "source_type": item.source_type,
                     "text": item.compare_text,
                     "evidence": [e.model_dump(mode="json") for e in item.compare_evidence]}
                    for item in matches
                ]
                self._enrich_diff(canonical, visual_object)
                canonical.match_score_details["fused_object_evidence"] = source_evidence
                result.reconciled_diff_ids.append(canonical.diff_id)
                if visual_object.object_type in {
                    SigningVisualObjectType.PERSONAL_NAME_SEAL,
                    SigningVisualObjectType.EDGE_SEAM_SEAL,
                }:
                    for duplicate in matches:
                        if duplicate is canonical:
                            continue
                        if (
                            visual_object.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL
                            and not self._same_physical_object(visual_object, duplicate)
                        ):
                            continue
                        if duplicate in seals:
                            seals.remove(duplicate)
                        if duplicate in signing:
                            signing.remove(duplicate)
                        result.removed_duplicate_diff_ids.append(duplicate.diff_id)
                continue
            if visual_object.raw_ref.get("reason") == "UNSCOPED_SMALL_RED_MARK":
                continue
            new_diff = self._new_diff(visual_object, next_index)
            next_index += 1
            signing.append(new_diff)
            result.new_diff_ids.append(new_diff.diff_id)
        self._correct_global_signing_dates(signing)
        return seals, signing

    def reconcile_matched_edge_diffs(self, result: SigningVisualRecallResult, diffs: list[DiffItem],
                                     original: Document, compare: Document) -> list[DiffItem]:
        """Discard OCR-only claims owned by corresponding seam fragments.

        Readable two-sided content changes and other seal types remain intact.
        A matched fragment proves presence, not equality of arbitrary seal text.
        """
        kept = []
        for diff in diffs:
            removed = False
            for left, right in result.matched_pairs:
                if not all(obj.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL
                           or obj.raw_ref.get("reason") == "PAGE_EDGE_ROUND_SEAL" for obj in (left, right)):
                    continue
                def owned(evidences, obj):
                    return bool(evidences) and all(e.page_no == obj.page_no
                        and self._bbox_overlap_ratio(e.bbox, obj.bbox) >= 0.6
                        and (e.bbox.x1-e.bbox.x0) <= (e.bbox.y1-e.bbox.y0)
                        for e in evidences)
                if diff.source_type == "header_footer" and diff.diff_type in {"ADD", "DELETE"}:
                    removed = owned(diff.original_evidence, left) if diff.diff_type == "DELETE" else owned(diff.compare_evidence, right)
                elif diff.source_type == "seal" and diff.diff_type == "MODIFY" and (not diff.original_text.strip() or not diff.compare_text.strip()):
                    if owned(diff.original_evidence, left) and owned(diff.compare_evidence, right):
                        from app.services.seal_visual_equivalence import corresponding_red_graphics
                        proof = corresponding_red_graphics(original, left, compare, right)
                        right.raw_ref["graphic_equivalence"] = proof
                        removed = proof["equivalent"]
                if removed:
                    result.removed_duplicate_diff_ids.append(diff.diff_id)
                    break
            if not removed:
                kept.append(diff)
        return kept

    def _recall_inset_edges(self, document: Document, page_numbers: set[int]) -> list[SigningVisualObject]:
        # Only search aligned counterpart pages after an observed seam fragment
        # has no match. This accommodates scan margins without globally widening
        # the page-edge detector or manufacturing a match from other pages.
        deps = self._dependencies()
        if deps is None or not Path(document.path).is_file():
            return []
        cv2, np = deps
        recalled = []
        with fitz.open(document.path) as pdf:
            for page in document.pages:
                if page.page_no not in page_numbers or not 1 <= page.page_no <= len(pdf):
                    continue
                image = self._render_page(pdf[page.page_no-1], np)
                for item in self._red_objects(page, image, cv2, np, allow_inset_edge=True):
                    if item.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
                        item.raw_ref["counterpart_guided_recall"] = True
                        recalled.append(item)
        return recalled

    def _red_objects(self, page: Page, image: Any, cv2: Any, np: Any, *, allow_inset_edge: bool = False) -> list[SigningVisualObject]:
        height, width = image.shape[:2]
        hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
        strong = cv2.bitwise_or(
            cv2.inRange(hsv, np.array([0, 60, 45]), np.array([15, 255, 255])),
            cv2.inRange(hsv, np.array([165, 60, 45]), np.array([180, 255, 255])),
        )
        rgb = image.astype(np.int16)
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        weak = ((red >= 100) & (red - green >= 5) & (red - blue >= 5)).astype("uint8") * 255
        strong_components = self._components(strong, cv2, min_area=50)
        seeds = [
            item
            for item in strong_components
            if item[4] >= 80 and min(item[2], item[3]) >= 18 and max(item[2], item[3]) <= max(width, height) * 0.35
        ]
        seeds = self._outer_components(seeds)

        closed = cv2.morphologyEx(weak, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        edge_components = [
            item
            for item in self._components(closed, cv2, min_area=10)
            if item[0] >= width * 0.84
            and (item[0] + item[2] >= width * 0.925
                 or (allow_inset_edge and item[3] >= 30 and item[3] >= item[2] * 2))
            and item[2] <= width * 0.16
        ]
        candidates: list[tuple[tuple[int, int, int, int, int], SigningVisualObjectType, float, str]] = []
        for component in edge_components:
            _x, _y, box_width, box_height, area = component
            if area < 25 or box_height < 10:
                continue
            aspect = box_height / max(1, box_width)
            if box_width >= 35 and aspect <= 2.8:
                candidates.append(
                    (component, SigningVisualObjectType.COMPANY_ROUND_SEAL, 0.78, "PAGE_EDGE_ROUND_SEAL")
                )
            else:
                candidates.append(
                    (component, SigningVisualObjectType.EDGE_SEAM_SEAL, 0.9, "PAGE_EDGE_RED_FRAGMENT")
                )

        for component in seeds:
            if component[0] >= width * 0.84 and component[2] <= width * 0.16:
                continue
            object_type = self._classify_strong_red(component)
            confidence = 0.93 if object_type == SigningVisualObjectType.COMPANY_ROUND_SEAL else 0.9
            candidates.append((component, object_type, confidence, "STRONG_RED_OBJECT"))

        for component in self._components(closed, cv2, min_area=80):
            x, y, box_width, box_height, area = component
            if x >= width * 0.84 and box_width <= width * 0.16:
                continue
            if min(box_width, box_height) < 20 or box_height < 26 or max(box_width, box_height) > width * 0.28:
                continue
            if any(self._pixel_overlap(component, existing[0]) >= 0.25 for existing in candidates):
                continue
            if area < 160:
                continue
            # Pale chromatic fringes on a printed word are not stamp evidence.
            # Require a small candidate wholly inside an ordinary long text
            # line before rejecting it; standalone and larger faint seals stay.
            px0, py0 = x * page.width / width, y * page.height / height
            px1, py1 = (x + box_width) * page.width / width, (y + box_height) * page.height / height
            if max(px1-px0, py1-py0) < 30 and any(
                b.block_type in {"text", "ocr_line"} and len(b.text.strip()) >= 12
                and b.bbox.x0 <= px0+2 and b.bbox.x1 >= px1-2
                and b.bbox.y0 <= py0+3 and b.bbox.y1 >= py1-3
                and (px1-px0) < (b.bbox.x1-b.bbox.x0) * .3
                for b in page.blocks
            ):
                continue
            candidates.append((component, SigningVisualObjectType.OTHER_STAMP, 0.64, "LOW_SATURATION_RED_STAMP"))

        # Black printed strokes can split a pale circular seal into small red islands.
        # Reunite only compact, seal-sized parents; do not label every island a name seal.
        for kernel_size in (15, 19):
            joined = cv2.morphologyEx(weak, cv2.MORPH_CLOSE, np.ones((kernel_size, kernel_size), np.uint8))
            for parent in self._outer_components(self._components(joined, cv2, min_area=250)):
                x, y, w, h, _area = parent
                if x >= width * 0.84 or min(w, h) < 105 or max(w, h) > width * 0.28 or max(w, h) / min(w, h) > 1.35:
                    continue
                region = rgb[y:y + h, x:x + w]
                colored_pixels = int(np.count_nonzero(region.max(axis=2) - region.min(axis=2) >= 10))
                red_pixels = int(np.count_nonzero(weak[y:y + h, x:x + w]))
                if red_pixels < colored_pixels * 0.5:
                    continue
                children = [item for item in candidates if self._pixel_overlap(item[0], parent) >= 0.8]
                if len(children) < 2:
                    continue
                # A complete round seal next to a separate compact name seal is
                # not a fragmented circle. Preserve both physical identities.
                circles = [item for item in children if item[1] == SigningVisualObjectType.COMPANY_ROUND_SEAL
                           and item[3] == "STRONG_RED_OBJECT"]
                if any(self._pixel_overlap(circle[0], other[0]) < 0.2
                       for circle in circles for other in children if other is not circle
                       and min(other[0][2:4]) >= 40):
                    continue
                candidates = [item for item in candidates if item not in children]
                candidates.append((parent, SigningVisualObjectType.COMPANY_ROUND_SEAL, 0.78, "FRAGMENTED_RED_SEAL_REASSEMBLED"))

        objects: list[SigningVisualObject] = []
        for index, (component, object_type, confidence, reason) in enumerate(candidates, start=1):
            bbox = self._component_bbox(component, page, width, height)
            if self._printed_logo_candidate(page, bbox):
                continue
            party, party_reason, party_confidence = self._attribute_party(page, bbox, object_type)
            objects.append(
                SigningVisualObject(
                    object_id=f"VIS-{page.page_no}-R{index}",
                    object_type=object_type,
                    page_no=page.page_no,
                    bbox=bbox,
                    visual_detection_confidence=confidence,
                    party_role=party,
                    party_attribution_reason=party_reason,
                    party_attribution_confidence=party_confidence,
                    supporting_pages=[page.page_no],
                    fragment_bboxes=[bbox] if object_type == SigningVisualObjectType.EDGE_SEAM_SEAL else [],
                    raw_ref={"reason": reason, "red_pixel_count": component[4]},
                )
            )
        self._inherit_party_from_nearby_seals(
            [item for item in objects if item.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL],
            [item for item in objects if item.object_type == SigningVisualObjectType.COMPANY_ROUND_SEAL],
        )
        company_objects = [item for item in objects if item.object_type == SigningVisualObjectType.COMPANY_ROUND_SEAL]
        for item in objects:
            if (
                item.object_type == SigningVisualObjectType.OTHER_STAMP
                and self._has_local_stamp_context(page, item.bbox)
            ):
                continue
            if (
                item.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL
                and self._page_has_signing_context(page)
            ):
                continue
            if item.object_type not in {
                SigningVisualObjectType.PERSONAL_NAME_SEAL,
                SigningVisualObjectType.OTHER_STAMP,
            }:
                continue
            if item.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL and any(
                candidate.page_no == item.page_no
                and self._bbox_distance(item.bbox, candidate.bbox) <= 90.0
                for candidate in company_objects
            ):
                continue
            item.object_type = SigningVisualObjectType.OTHER_STAMP
            item.visual_detection_confidence = min(item.visual_detection_confidence, 0.7)
            item.raw_ref = {**item.raw_ref, "reason": "UNSCOPED_SMALL_RED_MARK"}
        return objects

    @staticmethod
    def _has_local_stamp_context(page: Page, bbox: BBox) -> bool:
        return any(
            _VISUAL_STAMP_CONTEXT_RE.search(block.text or "")
            and WholePageSigningVisualRecall._bbox_distance(bbox, block.bbox) <= 150.0
            for block in page.blocks
        )

    def _date_objects(self, page: Page, image: Any, cv2: Any, np: Any) -> list[SigningVisualObject]:
        if not self._page_has_signing_context(page):
            return []
        objects: list[SigningVisualObject] = []
        for index, block in enumerate(page.blocks, start=1):
            match = _DATE_RE.search(block.text or "")
            if match is None:
                continue
            bbox = self._substring_bbox(block, match.start(), match.end())
            if not self._is_local_date_field(page, block, bbox):
                continue
            if not self._bbox_has_dark_ink(page, image, bbox, cv2, np):
                continue
            repeated_char_boxes = self._repeated_char_box_count(block, match.start(), match.end())
            confidence = 0.84 if repeated_char_boxes >= 3 or bbox.y1 - bbox.y0 >= 18 else 0.68
            party, party_reason, party_confidence = self._attribute_party(
                page,
                bbox,
                SigningVisualObjectType.HANDWRITTEN_DATE,
            )
            objects.append(
                SigningVisualObject(
                    object_id=f"VIS-{page.page_no}-D{index}",
                    object_type=SigningVisualObjectType.HANDWRITTEN_DATE,
                    page_no=page.page_no,
                    bbox=bbox,
                    visual_detection_confidence=confidence,
                    text=f"{int(match.group(1)):04d}.{int(match.group(2))}.{int(match.group(3))}",
                    text_recognition_confidence=min(0.92, float(block.confidence or 0.75)),
                    party_role=party,
                    party_attribution_reason=party_reason,
                    party_attribution_confidence=party_confidence,
                    supporting_pages=[page.page_no],
                    raw_ref={"reason": "DATE_TEXT_WITH_VISUAL_INK", "source_block_id": block.block_id},
                )
            )
        return objects

    @classmethod
    def _is_local_date_field(cls, page: Page, source: TextBlock, bbox: BBox) -> bool:
        # A signing label elsewhere on the page does not make a service period handwritten.
        label_pattern = re.compile(r"(?:签订|签署|签字)(?:日期|时间)|(?<![\u4e00-\u9fff])日期\s*[:：]?")
        for block in page.blocks:
            for match in label_pattern.finditer(block.text or ""):
                label = cls._substring_bbox(block, match.start(), match.end())
                height = min(bbox.y1 - bbox.y0, label.y1 - label.y0)
                if height <= 0 or abs((bbox.y0 + bbox.y1 - label.y0 - label.y1) / 2) > height * 0.6:
                    continue
                if block is source and "\n" not in block.text:
                    return True
                if block.char_boxes and max(label.x0 - bbox.x1, bbox.x0 - label.x1, 0) < 100:
                    return True
        return False

    def _signature_objects(
        self,
        page: Page,
        image: Any,
        red_objects: list[SigningVisualObject],
        date_objects: list[SigningVisualObject],
        cv2: Any,
        np: Any,
    ) -> list[SigningVisualObject]:
        anchors = [
            block
            for block in page.blocks
            if ((_SIGNATURE_ANCHOR_RE.search(block.text or "")
                 and self._has_local_signature_cue(page, block))
                or _PARTICIPANT_ANCHOR_RE.match(block.text or ""))
            and block.bbox.y1 - block.bbox.y0 <= 70
            and (block.bbox.x1 - block.bbox.x0) * (block.bbox.y1 - block.bbox.y0) <= page.width * page.height * 0.08
        ]
        objects: list[SigningVisualObject] = []
        seen_zones: list[BBox] = []
        for index, anchor in enumerate(anchors, start=1):
            participant = _PARTICIPANT_ANCHOR_RE.match(anchor.text or "")
            if participant:
                anchor = anchor.model_copy(update={
                    "bbox": self._substring_bbox(anchor, participant.start(), participant.end()),
                    "text": participant.group(),
                })
            zone = BBox(
                x0=max(0.0, anchor.bbox.x0 - 5.0),
                y0=max(0.0, anchor.bbox.y0 - 5.0),
                x1=min(page.width, anchor.bbox.x1 + max(90.0, page.width * (0.30 if participant else 0.18))),
                y1=min(page.height, anchor.bbox.y1 + 90.0),
            )
            # A signature slot ends at the next printed field in its column.
            # Otherwise phone/account digits become a high-confidence signature.
            boundaries = [block.bbox.y0 for block in page.blocks
                          if block.bbox.y0 >= anchor.bbox.y1
                          and block.bbox.x0 < zone.x1 and block.bbox.x1 > zone.x0
                          and re.match(r"^(?:电话|传真|开户|账号|帐号|税号|地址|邮编|邮政编码|日期|时间|联系人|统一社会信用代码)\s*[:：]",
                                       (block.text or "").strip())]
            if boundaries:
                zone.y1 = min(zone.y1, min(boundaries) - 2.0)
            if zone.y1 <= zone.y0:
                continue
            if any(self._bbox_iou(zone, previous) >= 0.75 for previous in seen_zones):
                continue
            seen_zones.append(zone)
            detected = (self._detect_dark_mark(page, image, zone, anchor.bbox, cv2, np, cluster_gap=140)
                        if participant else self._detect_dark_mark(page, image, zone, anchor.bbox, cv2, np))
            if detected is None:
                continue
            if any(self._bbox_overlap_ratio(detected, item.bbox) >= 0.7 for item in date_objects):
                continue
            if any(
                item.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL
                and self._bbox_overlap_ratio(detected, item.bbox) >= 0.65
                for item in red_objects
            ):
                continue
            party, party_reason, party_confidence = self._attribute_party(
                page,
                detected,
                SigningVisualObjectType.HANDWRITTEN_SIGNATURE,
            )
            objects.append(
                SigningVisualObject(
                    object_id=f"VIS-{page.page_no}-S{index}",
                    object_type=SigningVisualObjectType.HANDWRITTEN_SIGNATURE,
                    page_no=page.page_no,
                    bbox=detected,
                    visual_detection_confidence=0.84,
                    party_role=party,
                    party_attribution_reason=party_reason,
                    party_attribution_confidence=party_confidence,
                    supporting_pages=[page.page_no],
                    raw_ref={"reason": "DARK_HANDWRITING_NEAR_SIGNING_ANCHOR", "source_block_id": anchor.block_id,
                             "participant_signature": bool(participant)},
                )
            )
        return objects

    def _detect_dark_mark(
        self,
        page: Page,
        image: Any,
        zone: BBox,
        anchor_bbox: BBox,
        cv2: Any,
        np: Any,
        cluster_gap: float = 50,
    ) -> BBox | None:
        height, width = image.shape[:2]
        x0, y0, x1, y1 = self._bbox_pixels(zone, page, width, height)
        if x1 <= x0 or y1 <= y0:
            return None
        crop = image[y0:y1, x0:x1]
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        dark = (gray < 90).astype("uint8") * 255
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        red = cv2.bitwise_or(
            cv2.inRange(hsv, np.array([0, 35, 45]), np.array([15, 255, 255])),
            cv2.inRange(hsv, np.array([165, 35, 45]), np.array([180, 255, 255])),
        )
        dark[cv2.dilate(red, np.ones((5, 5), np.uint8)) > 0] = 0
        components = [
            item
            for item in self._components(dark, cv2, min_area=8)
            if item[4] >= 20 and min(item[2], item[3]) >= 4 and max(item[2], item[3]) <= max(crop.shape[:2]) * 0.65
        ]
        filtered: list[tuple[int, int, int, int, int]] = []
        for component in components:
            center_x = x0 + component[0] + component[2] / 2
            center_y = y0 + component[1] + component[3] / 2
            model_x = center_x / width * page.width
            model_y = center_y / height * page.height
            if anchor_bbox.x0 <= model_x <= anchor_bbox.x1 and anchor_bbox.y0 <= model_y <= anchor_bbox.y1:
                continue
            filtered.append(component)
        dominant_candidates = [item for item in filtered if item[4] >= 90 and min(item[2], item[3]) >= 8]
        if not dominant_candidates:
            return None
        dominant = max(dominant_candidates, key=lambda item: item[4])
        clustered = [
            item
            for item in filtered
            if item[4] >= dominant[4] * 0.08 and self._component_distance(item, dominant) <= cluster_gap
        ]
        union = self._union_components(clustered)
        global_component = (x0 + union[0], y0 + union[1], union[2], union[3], union[4])
        detected = self._component_bbox(global_component, page, width, height)
        if min(detected.x1 - detected.x0, detected.y1 - detected.y0) < 18.0:
            return None
        return detected

    @staticmethod
    def _has_local_signature_cue(page: Page, anchor: TextBlock) -> bool:
        text = re.sub(r"\s+", "", anchor.text or "")
        # Only a compact form label may seed a search outside its printed text.
        if len(text) > 30 or re.search(r"[，,。；;]|确认|保存|验收单|签收单", text):
            return False
        if re.search(r"签字|签名", text):
            return True
        anchor_center_x = (anchor.bbox.x0 + anchor.bbox.x1) / 2
        for block in page.blocks:
            if not re.search(r"签字|签名", block.text or ""):
                continue
            block_center_x = (block.bbox.x0 + block.bbox.x1) / 2
            vertical_gap = max(0.0, block.bbox.y0 - anchor.bbox.y1, anchor.bbox.y0 - block.bbox.y1)
            if vertical_gap <= 45.0 and abs(block_center_x - anchor_center_x) <= max(90.0, page.width * 0.16):
                return True
        return False

    @staticmethod
    def _classify_strong_red(component: tuple[int, int, int, int, int]) -> SigningVisualObjectType:
        _x, _y, width, height, _area = component
        aspect = max(width, height) / max(1, min(width, height))
        if aspect > 1.35:
            return SigningVisualObjectType.OTHER_STAMP
        if max(width, height) >= 105:
            return SigningVisualObjectType.COMPANY_ROUND_SEAL
        return SigningVisualObjectType.PERSONAL_NAME_SEAL

    @classmethod
    def _attribute_party(
        cls,
        page: Page,
        bbox: BBox,
        object_type: SigningVisualObjectType,
    ) -> tuple[str, str, float]:
        if object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
            return "unknown", "PAGE_EDGE_LOGICAL_OBJECT", 0.98
        if object_type == SigningVisualObjectType.HANDWRITTEN_DATE:
            global_labels = [block for block in page.blocks if re.search(r"签订日期|签字日期", block.text or "")]
            if global_labels and bbox.y0 >= max(block.bbox.y0 for block in global_labels) - 20:
                center_ratio = (bbox.x0 + bbox.x1) / 2 / max(1.0, page.width)
                if 0.4 <= center_ratio <= 0.6:
                    return "unknown", "GLOBAL_SIGNING_DATE_ANCHOR", 0.9
        anchors: list[tuple[str, BBox]] = []
        for block in page.blocks:
            compact = re.sub(r"\s+", "", block.text or "")
            if compact in {"甲方", "委托人", "发包人", "采购人"}:
                anchors.append(("甲方", block.bbox))
            elif compact in {"乙方", "承包人", "承包方", "供应商"}:
                anchors.append(("乙方", block.bbox))
            for match in _PARTY_ANCHOR_RE.finditer(block.text or ""):
                label = re.sub(r"\s+", "", match.group("label"))
                role = next(role for pattern, role in _PARTY_PATTERNS if pattern.search(label))
                anchors.append((role, cls._substring_bbox(block, match.start("label"), match.end("label"))))
        if anchors:
            anchor_roles = {item[0] for item in anchors}
            if anchor_roles == {"乙方"} and bbox.y1 < min(item[1].y0 for item in anchors) - 20:
                return "甲方", "BEFORE_PARTY_B_SECTION", 0.78
            center_x = (bbox.x0 + bbox.x1) / 2
            center_y = (bbox.y0 + bbox.y1) / 2

            def distance(item: tuple[str, BBox]) -> float:
                anchor = item[1]
                anchor_x = (anchor.x0 + anchor.x1) / 2
                anchor_y = (anchor.y0 + anchor.y1) / 2
                return abs(center_x - anchor_x) / max(1.0, page.width) * 1.35 + abs(center_y - anchor_y) / max(1.0, page.height)

            ordered = sorted(anchors, key=distance)
            best = ordered[0]
            best_distance = distance(best)
            second_distance = next((distance(item) for item in ordered[1:] if item[0] != best[0]), 2.0)
            confidence = min(0.96, max(0.68, 0.9 - best_distance + max(0.0, second_distance - best_distance) * 0.3))
            return best[0], "NEAREST_PARTY_ANCHOR", round(confidence, 3)
        party_b_starts = [
            block.bbox.y0
            for block in page.blocks
            if re.match(r"\s*(?:乙方|承包人|承包方|供应商)\s*[:：（(]", block.text or "")
        ]
        if party_b_starts and bbox.y1 < min(party_b_starts) - 20:
            return "甲方", "BEFORE_PARTY_B_SECTION", 0.78
        return "unknown", "PARTY_ANCHOR_NOT_OBSERVED", 0.0

    @staticmethod
    def _inherit_party_from_nearby_seals(
        targets: list[SigningVisualObject],
        seals: list[SigningVisualObject],
    ) -> None:
        for target in targets:
            if target.party_attribution_reason == "GLOBAL_SIGNING_DATE_ANCHOR":
                continue
            candidates = [item for item in seals if item.page_no == target.page_no and item.party_role != "unknown"]
            if not candidates:
                continue

            def gap(item: SigningVisualObject) -> float:
                horizontal = max(0.0, max(item.bbox.x0, target.bbox.x0) - min(item.bbox.x1, target.bbox.x1))
                vertical = max(0.0, max(item.bbox.y0, target.bbox.y0) - min(item.bbox.y1, target.bbox.y1))
                return (horizontal**2 + vertical**2) ** 0.5

            nearest = min(candidates, key=gap)
            if gap(nearest) > 90.0:
                continue
            target.party_role = nearest.party_role
            target.party_attribution_reason = "NEAREST_SIGNING_VISUAL_OBJECT"
            target.party_attribution_confidence = min(0.92, nearest.party_attribution_confidence)

    @staticmethod
    def _page_has_signing_context(page: Page) -> bool:
        return _SIGNING_MARKER_RE.search("\n".join(block.text for block in page.blocks)) is not None

    @staticmethod
    def _printed_logo_candidate(page: Page, bbox: BBox) -> bool:
        page_has_signing = WholePageSigningVisualRecall._page_has_signing_context(page)
        for block in page.blocks:
            block_type = (block.block_type or "").lower()
            looks_like_logo = block_type in {"logo", "figure", "image"} or re.search(r"logo|标识", block.text or "", re.I)
            if looks_like_logo and WholePageSigningVisualRecall._bbox_overlap_ratio(bbox, block.bbox) >= 0.5:
                return not page_has_signing
        return False

    @staticmethod
    def _substring_bbox(block: TextBlock, start: int, end: int) -> BBox:
        bboxes = [
            char_box.bbox
            for char_box in block.char_boxes
            if char_box.text_index is not None and start <= char_box.text_index < end
        ]
        if not bboxes:
            return block.bbox
        return BBox(
            x0=min(item.x0 for item in bboxes),
            y0=min(item.y0 for item in bboxes),
            x1=max(item.x1 for item in bboxes),
            y1=max(item.y1 for item in bboxes),
        )

    @staticmethod
    def _repeated_char_box_count(block: TextBlock, start: int, end: int) -> int:
        keys = [
            (round(char_box.bbox.x0, 1), round(char_box.bbox.y0, 1), round(char_box.bbox.x1, 1), round(char_box.bbox.y1, 1))
            for char_box in block.char_boxes
            if char_box.text_index is not None and start <= char_box.text_index < end
        ]
        return max((keys.count(key) for key in set(keys)), default=0)

    @staticmethod
    def _bbox_has_dark_ink(page: Page, image: Any, bbox: BBox, cv2: Any, np: Any) -> bool:
        height, width = image.shape[:2]
        x0, y0, x1, y1 = WholePageSigningVisualRecall._bbox_pixels(bbox, page, width, height)
        if x1 <= x0 or y1 <= y0:
            return False
        gray = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
        return int(np.count_nonzero(gray < 130)) >= 12

    def _render_page(self, pdf_page: fitz.Page, np: Any) -> Any | None:
        if self.render_cache is not None:
            return self.render_cache.render(pdf_page, scale=self.render_scale, colorspace=fitz.csRGB)
        pixmap = pdf_page.get_pixmap(
            matrix=fitz.Matrix(WholePageSigningVisualRecall.render_scale, WholePageSigningVisualRecall.render_scale),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        if pixmap.width <= 0 or pixmap.height <= 0:
            return None
        return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]

    @staticmethod
    def _components(mask: Any, cv2: Any, *, min_area: int) -> list[tuple[int, int, int, int, int]]:
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        return [
            tuple(int(value) for value in stats[index])
            for index in range(1, count)
            if int(stats[index][4]) >= min_area
        ]

    @staticmethod
    def _outer_components(components: list[tuple[int, int, int, int, int]]) -> list[tuple[int, int, int, int, int]]:
        kept: list[tuple[int, int, int, int, int]] = []
        for candidate in sorted(components, key=lambda item: item[2] * item[3], reverse=True):
            if any(WholePageSigningVisualRecall._pixel_contains(existing, candidate, margin=3) for existing in kept):
                continue
            kept.append(candidate)
        return kept

    @staticmethod
    def _pixel_contains(
        outer: tuple[int, int, int, int, int],
        inner: tuple[int, int, int, int, int],
        *,
        margin: int,
    ) -> bool:
        return (
            inner[0] >= outer[0] - margin
            and inner[1] >= outer[1] - margin
            and inner[0] + inner[2] <= outer[0] + outer[2] + margin
            and inner[1] + inner[3] <= outer[1] + outer[3] + margin
        )

    @staticmethod
    def _pixel_overlap(
        left: tuple[int, int, int, int, int], right: tuple[int, int, int, int, int]
    ) -> float:
        x0, y0 = max(left[0], right[0]), max(left[1], right[1])
        x1 = min(left[0] + left[2], right[0] + right[2])
        y1 = min(left[1] + left[3], right[1] + right[3])
        intersection = max(0, x1 - x0) * max(0, y1 - y0)
        return intersection / max(1, min(left[2] * left[3], right[2] * right[3]))

    @staticmethod
    def _component_bbox(
        component: tuple[int, int, int, int, int], page: Page, image_width: int, image_height: int
    ) -> BBox:
        x, y, width, height, _area = component
        return BBox(
            x0=x / image_width * page.width,
            y0=y / image_height * page.height,
            x1=(x + width) / image_width * page.width,
            y1=(y + height) / image_height * page.height,
        )

    @staticmethod
    def _bbox_pixels(bbox: BBox, page: Page, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        return (
            max(0, int(bbox.x0 / max(1.0, page.width) * image_width)),
            max(0, int(bbox.y0 / max(1.0, page.height) * image_height)),
            min(image_width, int(bbox.x1 / max(1.0, page.width) * image_width)),
            min(image_height, int(bbox.y1 / max(1.0, page.height) * image_height)),
        )

    @staticmethod
    def _component_distance(
        left: tuple[int, int, int, int, int], right: tuple[int, int, int, int, int]
    ) -> float:
        left_center = (left[0] + left[2] / 2, left[1] + left[3] / 2)
        right_center = (right[0] + right[2] / 2, right[1] + right[3] / 2)
        return ((left_center[0] - right_center[0]) ** 2 + (left_center[1] - right_center[1]) ** 2) ** 0.5

    @staticmethod
    def _union_components(components: list[tuple[int, int, int, int, int]]) -> tuple[int, int, int, int, int]:
        x0 = min(item[0] for item in components)
        y0 = min(item[1] for item in components)
        x1 = max(item[0] + item[2] for item in components)
        y1 = max(item[1] + item[3] for item in components)
        return x0, y0, x1 - x0, y1 - y0, sum(item[4] for item in components)

    @staticmethod
    def _bbox_overlap_ratio(left: BBox, right: BBox) -> float:
        x0, y0 = max(left.x0, right.x0), max(left.y0, right.y0)
        x1, y1 = min(left.x1, right.x1), min(left.y1, right.y1)
        intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        left_area = max(1.0, (left.x1 - left.x0) * (left.y1 - left.y0))
        right_area = max(1.0, (right.x1 - right.x0) * (right.y1 - right.y0))
        return intersection / min(left_area, right_area)

    @staticmethod
    def _bbox_distance(left: BBox, right: BBox) -> float:
        horizontal = max(0.0, max(left.x0, right.x0) - min(left.x1, right.x1))
        vertical = max(0.0, max(left.y0, right.y0) - min(left.y1, right.y1))
        return (horizontal**2 + vertical**2) ** 0.5

    @classmethod
    def _bbox_iou(cls, left: BBox, right: BBox) -> float:
        x0, y0 = max(left.x0, right.x0), max(left.y0, right.y0)
        x1, y1 = min(left.x1, right.x1), min(left.y1, right.y1)
        intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        union = max(1.0, (left.x1 - left.x0) * (left.y1 - left.y0) + (right.x1 - right.x0) * (right.y1 - right.y0) - intersection)
        return intersection / union

    @classmethod
    def _dedupe_objects(cls, objects: list[SigningVisualObject]) -> list[SigningVisualObject]:
        edge_reasons = {"PAGE_EDGE_RED_FRAGMENT", "PAGE_EDGE_ROUND_SEAL"}
        # Closing the red mask can yield an outer edge seal and an inner
        # fragment with different labels. Resolve containment before matching
        # counterparts, so the fragment cannot consume a second match.
        objects = [item for item in objects if not (
            item.raw_ref.get("reason") in edge_reasons and any(
                other is not item and other.page_no == item.page_no
                and other.raw_ref.get("reason") in edge_reasons
                and other.bbox.x0 <= item.bbox.x0 and other.bbox.y0 <= item.bbox.y0
                and other.bbox.x1 >= item.bbox.x1 and other.bbox.y1 >= item.bbox.y1
                and (other.bbox.x1-other.bbox.x0)*(other.bbox.y1-other.bbox.y0)
                    > (item.bbox.x1-item.bbox.x0)*(item.bbox.y1-item.bbox.y0)
                for other in objects))]
        kept: list[SigningVisualObject] = []
        for item in sorted(objects, key=lambda value: (-value.visual_detection_confidence, value.page_no, value.bbox.y0)):
            if any(
                item.page_no == existing.page_no
                and item.object_type == existing.object_type
                and cls._bbox_overlap_ratio(item.bbox, existing.bbox) >= 0.65
                for existing in kept
            ):
                continue
            kept.append(item)
        return sorted(kept, key=lambda value: (value.page_no, value.bbox.y0, value.bbox.x0, value.object_type.value))

    @classmethod
    def _best_counterpart(
        cls,
        target: SigningVisualObject,
        target_document: Document,
        candidates: list[SigningVisualObject],
        candidate_document: Document,
        *,
        page_alignment: PageAlignmentResult | None,
        used: set[int],
    ) -> int | None:
        target_page = next((page for page in target_document.pages if page.page_no == target.page_no), None)
        if target_page is None:
            return None
        counterpart_pages = (
            set(page_alignment.counterparts_for_compare(target.page_no)) if page_alignment is not None else {target.page_no}
        )
        if not counterpart_pages:
            counterpart_pages = {target.page_no}
        scored: list[tuple[float, int]] = []
        for index, candidate in enumerate(candidates):
            if index in used or candidate.page_no not in counterpart_pages:
                continue
            if not cls._counterpart_types_compatible(target, candidate):
                # A faint round seal can be detected as an unclassified
                # fragment. Only accept containment, never type similarity
                # alone, and leave distinct neighbouring seals unmatched.
                types = {target.object_type, candidate.object_type}
                if types != {SigningVisualObjectType.COMPANY_ROUND_SEAL, SigningVisualObjectType.OTHER_STAMP}:
                    continue
                cp = next((p for p in candidate_document.pages if p.page_no == candidate.page_no), None)
                if cp is None:
                    continue
                mapped = BBox(x0=candidate.bbox.x0 / cp.width * target_page.width,
                              x1=candidate.bbox.x1 / cp.width * target_page.width,
                              y0=candidate.bbox.y0 / cp.height * target_page.height,
                              y1=candidate.bbox.y1 / cp.height * target_page.height)
                if cls._bbox_overlap_ratio(target.bbox, mapped) < 0.25:
                    continue
                from app.services.seal_visual_equivalence import corresponding_red_graphics
                proof = corresponding_red_graphics(candidate_document, candidate, target_document, target)
                target.raw_ref["fragment_counterpart_proof"] = proof
                if not proof.get("equivalent"):
                    continue
            candidate_page = next((page for page in candidate_document.pages if page.page_no == candidate.page_no), None)
            if candidate_page is None:
                continue
            target_center = (
                (target.bbox.x0 + target.bbox.x1) / 2 / max(1.0, target_page.width),
                (target.bbox.y0 + target.bbox.y1) / 2 / max(1.0, target_page.height),
            )
            candidate_center = (
                (candidate.bbox.x0 + candidate.bbox.x1) / 2 / max(1.0, candidate_page.width),
                (candidate.bbox.y0 + candidate.bbox.y1) / 2 / max(1.0, candidate_page.height),
            )
            distance = abs(target_center[0] - candidate_center[0]) + abs(target_center[1] - candidate_center[1])
            if distance <= (0.18 if target.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL else 0.13):
                scored.append((distance, index))
        return min(scored)[1] if scored else None

    @staticmethod
    def _counterpart_types_compatible(
        target: SigningVisualObject,
        candidate: SigningVisualObject,
    ) -> bool:
        if target.object_type == candidate.object_type:
            return True
        edge_reasons = {"PAGE_EDGE_RED_FRAGMENT", "PAGE_EDGE_ROUND_SEAL"}
        return (
            target.raw_ref.get("reason") in edge_reasons
            and candidate.raw_ref.get("reason") in edge_reasons
        )

    @staticmethod
    def _aggregate_edge_fragments(objects: list[SigningVisualObject]) -> list[SigningVisualObject]:
        edge = [
            item
            for item in objects
            if item.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL
            or item.raw_ref.get("reason") == "PAGE_EDGE_ROUND_SEAL"
        ]
        other = [item for item in objects if item not in edge]
        if len({item.page_no for item in edge}) < 2:
            return objects
        edge.sort(key=lambda item: item.page_no)
        first = edge[0]
        aggregate = first.model_copy(
            update={
                "object_id": "VIS-EDGE-SEAM-AGGREGATE",
                "object_type": SigningVisualObjectType.EDGE_SEAM_SEAL,
                "visual_detection_confidence": min(item.visual_detection_confidence for item in edge),
                "supporting_pages": [item.page_no for item in edge],
                "fragment_bboxes": [item.bbox for item in edge],
                "raw_ref": {
                    "reason": "MULTI_PAGE_EDGE_FRAGMENT_AGGREGATION",
                    "fragment_count": len(edge),
                    "document_page_count": first.raw_ref.get("document_page_count", 0),
                    "counterpart_edge_present": any(item.raw_ref.get("counterpart_edge_present") for item in edge),
                },
            }
        )
        return sorted([*other, aggregate], key=lambda item: (item.page_no, item.bbox.y0, item.bbox.x0))

    @classmethod
    def _overlapping_diffs(cls, visual_object: SigningVisualObject, diffs: list[DiffItem]) -> list[DiffItem]:
        matches: list[DiffItem] = []
        pages = set(visual_object.supporting_pages or [visual_object.page_no])
        object_evidence = cls._object_evidence(visual_object)
        for diff in diffs:
            if diff.diff_type != "ADD":
                continue
            existing_object_type = str((diff.match_score_details or {}).get("object_type") or "")
            if existing_object_type and existing_object_type != visual_object.object_type.value:
                continue
            if not cls._diff_compatible_with_object(diff, visual_object.object_type):
                continue
            for evidence in diff.compare_evidence:
                if evidence.page_no not in pages:
                    continue
                if visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
                    if any(
                        item.page_no == evidence.page_no and (
                            cls._bbox_overlap_ratio(item.bbox, evidence.bbox) >= 0.15
                            or cls._bbox_distance(item.bbox, evidence.bbox) <= 8.0
                        ) for item in object_evidence
                    ):
                        matches.append(diff)
                        break
                elif cls._bbox_overlap_ratio(visual_object.bbox, evidence.bbox) >= 0.35:
                    matches.append(diff)
                    break
        return matches

    @classmethod
    def _same_physical_object(cls, visual_object: SigningVisualObject, diff: DiffItem) -> bool:
        # A small name seal inside a large company seal ROI is not the same
        # object. Require both boxes to describe substantially the same ink.
        for evidence in diff.compare_evidence:
            if evidence.page_no != visual_object.page_no:
                continue
            left, right = visual_object.bbox, evidence.bbox
            intersection = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0)) * max(
                0.0, min(left.y1, right.y1) - max(left.y0, right.y0)
            )
            area = max((left.x1-left.x0)*(left.y1-left.y0), (right.x1-right.x0)*(right.y1-right.y0), 1.0)
            if intersection / area >= 0.75:
                return True
            # OCR can retain either row or column of a small name seal.
            # Require containment and comparable dimensions; a company seal
            # surrounding a separate name seal must remain independent.
            widths = (left.x1-left.x0, right.x1-right.x0)
            heights = (left.y1-left.y0, right.y1-right.y0)
            smaller_area = max(1.0, min((left.x1-left.x0)*(left.y1-left.y0),
                                      (right.x1-right.x0)*(right.y1-right.y0)))
            if (visual_object.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL
                    and (diff.source_type == "seal" or (diff.source_type == "signing_region"
                         and diff.section_type.endswith((":signature", ":seal"))))
                    and max(*widths, *heights) <= 90.0
                    and widths[1] * heights[1] <= widths[0] * heights[0] * 1.05
                    and min(widths) / max(*widths, 1.0) >= 0.5
                    and min(heights) / max(*heights, 1.0) >= 0.5
                    and intersection / smaller_area >= 0.95 and intersection / area >= 0.45):
                return True
        return False

    @staticmethod
    def _diff_compatible_with_object(
        diff: DiffItem,
        object_type: SigningVisualObjectType,
    ) -> bool:
        text = f"{diff.title} {diff.section_type} {' '.join(diff.review_flags)}"
        field_key = str((diff.match_score_details or {}).get("field_key") or "")
        if object_type == SigningVisualObjectType.HANDWRITTEN_DATE:
            return field_key == "date" or "日期" in text
        if object_type == SigningVisualObjectType.HANDWRITTEN_SIGNATURE:
            return any(marker in text for marker in ("签字", "签名", "代表")) and "日期" not in text
        if object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL:
            return diff.source_type == "seal" or any(
                marker in text for marker in ("印章", "盖章", "签字", "签名", "seal", "signature")
            )
        if object_type in {
            SigningVisualObjectType.COMPANY_ROUND_SEAL,
            SigningVisualObjectType.EDGE_SEAM_SEAL,
            SigningVisualObjectType.OTHER_STAMP,
        }:
            return diff.source_type == "seal" or any(marker in text for marker in ("印章", "盖章", "seal"))
        return True

    @staticmethod
    def _canonical_diff(visual_object: SigningVisualObject, matches: list[DiffItem]) -> DiffItem:
        if visual_object.object_type == SigningVisualObjectType.PERSONAL_NAME_SEAL:
            ocr_signature = next(
                (
                    item
                    for item in matches
                    if item.source_type == "signing_region"
                    and item.section_type.endswith(":signature")
                    and item.compare_text not in {"", "seal"}
                ),
                None,
            )
            if ocr_signature is not None:
                return ocr_signature
        if visual_object.object_type == SigningVisualObjectType.COMPANY_ROUND_SEAL:
            generic = next((item for item in matches if item.source_type == "seal"), None)
            if generic is not None:
                return generic
        return max(matches, key=lambda item: max((evidence.confidence for evidence in item.compare_evidence), default=0.0))

    @classmethod
    def _enrich_diff(cls, diff: DiffItem, visual_object: SigningVisualObject) -> None:
        existing_details = dict(diff.match_score_details)
        bound_party = diff.title.split("·", 1)[0].strip() if "·" in diff.title else ""
        if bound_party in {"甲方", "乙方"} and visual_object.party_role != bound_party:
            # A region-bound field must not switch parties based on whole-page proximity.
            existing_details["visual_party_candidate"] = visual_object.party_role
            existing_details["party_attribution_conflict"] = True
            visual_object = visual_object.model_copy(update={
                "party_role": bound_party,
                "party_attribution_reason": "EXISTING_REGION_BINDING_PRESERVED",
                "party_attribution_confidence": min(visual_object.party_attribution_confidence, 0.5),
            })
        previous_decision = str((diff.match_score_details or {}).get("change_decision") or "")
        details = {**existing_details, **cls._object_details(visual_object)}
        if previous_decision:
            details["change_decision"] = previous_decision or "NEEDS_REVIEW"
            details["decision_reason"] = str(existing_details.get("decision_reason") or "VISUAL_OBJECT_REVIEW_PRESERVED")
        else:
            details.pop("change_decision", None)
            details.pop("decision_reason", None)
        flags = [flag for flag in cls._object_flags(visual_object) if flag != "VISUAL_OBJECT_EXISTENCE_CONFIRMED"]
        diff.match_method = "whole_page_visual_recall"
        diff.match_confidence = visual_object.visual_detection_confidence
        diff.match_score_details = details
        diff.review_flags = list(dict.fromkeys([*diff.review_flags, *flags]))
        visual_evidence = cls._object_evidence(visual_object)
        if visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
            # Low-resolution red recall may only see a small dark fragment of a pale seal.
            # Keep a bounded, overlapping legacy seal ROI on its own page.
            retained = [e for e in diff.compare_evidence
                        if e.method in {"seal_region", "whole_page_visual_object"}
                        and 0 < e.bbox.x1 - e.bbox.x0 <= 200 and 0 < e.bbox.y1 - e.bbox.y0 <= 250
                        and any(v.page_no == e.page_no and cls._bbox_overlap_ratio(v.bbox, e.bbox) >= 0.35
                                for v in visual_evidence)]
            visual_evidence = [v for v in visual_evidence if not any(
                e.page_no == v.page_no and e.bbox.x0 <= v.bbox.x0 and e.bbox.y0 <= v.bbox.y0
                and e.bbox.x1 >= v.bbox.x1 and e.bbox.y1 >= v.bbox.y1 for e in retained)]
            visual_evidence = sorted([*retained, *visual_evidence], key=lambda e: (e.page_no, e.bbox.y0, e.bbox.x0))
        if cls._should_replace_existing_evidence(diff, visual_evidence, visual_object):
            diff.compare_evidence = visual_evidence
        if diff.diff_type == "ADD" and diff.source_type in {"seal", "signing_region"} and visual_object.object_type in {
            SigningVisualObjectType.COMPANY_ROUND_SEAL,
            SigningVisualObjectType.PERSONAL_NAME_SEAL,
            SigningVisualObjectType.EDGE_SEAM_SEAL,
            SigningVisualObjectType.OTHER_STAMP,
        }:
            replacement = cls._new_diff(visual_object, 0)
            # Keep the OCR payload for diagnosis, but describe the visual object.
            diff.match_score_details["visual_replaced_text"] = diff.compare_text
            for name in ("title", "compare_text", "compare_snippet", "readable_change",
                         "compare_change_ranges", "content_recognition_status",
                         "content_recognition_reason", "content_candidates"):
                setattr(diff, name, getattr(replacement, name))
            if visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
                diff.compare_evidence = visual_evidence
            diff.compare_evidence = [item.model_copy(update={
                "text": replacement.compare_text, "method": "whole_page_visual_object",
            }) for item in diff.compare_evidence]
            if visual_object.raw_ref.get("counterpart_edge_present"):
                diff.match_score_details["change_decision"] = "NEEDS_REVIEW"
                diff.match_score_details["decision_reason"] = "COUNTERPART_EDGE_MARK_PRESENT"
                diff.quality_status = "NEEDS_REVIEW"
                diff.readable_change = "骑缝印迹存在差异，新增与否待复核"

    @classmethod
    def _should_replace_existing_evidence(
        cls,
        diff: DiffItem,
        recalled: list[EvidenceBox],
        visual_object: SigningVisualObject,
    ) -> bool:
        existing = diff.compare_evidence
        if not existing:
            return True
        if visual_object.object_type == SigningVisualObjectType.HANDWRITTEN_DATE:
            return False
        if visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
            if diff.source_type == "seal":
                return False
            return any(
                left.page_no == right.page_no
                and (
                    cls._bbox_overlap_ratio(left.bbox, right.bbox) >= 0.15
                    or cls._bbox_distance(left.bbox, right.bbox) <= 8.0
                )
                for left in existing
                for right in recalled
            )
        existing_area = max(
            (item.bbox.x1 - item.bbox.x0) * (item.bbox.y1 - item.bbox.y0)
            for item in existing
        )
        recalled_area = max(
            (item.bbox.x1 - item.bbox.x0) * (item.bbox.y1 - item.bbox.y0)
            for item in recalled
        )
        return recalled_area >= existing_area * 0.2

    @classmethod
    def _new_diff(cls, visual_object: SigningVisualObject, index: int) -> DiffItem:
        title, display = cls._object_labels(visual_object)
        role_prefix = f"{visual_object.party_role} · " if visual_object.party_role != "unknown" else ""
        if visual_object.raw_ref.get("participant_signature"):
            role_prefix = ""
        seal_like = visual_object.object_type in {
            SigningVisualObjectType.COMPANY_ROUND_SEAL,
            SigningVisualObjectType.PERSONAL_NAME_SEAL,
            SigningVisualObjectType.EDGE_SEAM_SEAL,
            SigningVisualObjectType.OTHER_STAMP,
        }
        content_review = visual_object.object_type in {
            SigningVisualObjectType.COMPANY_ROUND_SEAL,
            SigningVisualObjectType.PERSONAL_NAME_SEAL,
        }
        return DiffItem(
            diff_id=f"WHOLE-PAGE-{visual_object.object_id}",
            diff_type="ADD",
            title=f"{role_prefix}{title}",
            compare_text=display,
            compare_snippet=display,
            readable_change=(f"{role_prefix}{display}（新增与否待复核）"
                             if visual_object.raw_ref.get("counterpart_edge_present") else f"新增{role_prefix}{display}"),
            source_type="signing_region",
            section_type=f"signature:{visual_object.object_type.value.lower()}",
            match_method="whole_page_visual_recall",
            match_confidence=visual_object.visual_detection_confidence,
            match_score_details=cls._object_details(visual_object),
            review_flags=cls._object_flags(visual_object),
            quality_status="NORMAL" if cls._is_confirmed_object(visual_object) else "NEEDS_REVIEW",
            content_recognition_status="NEEDS_REVIEW" if content_review else "CONFIRMED",
            content_recognition_reason="VISUAL_EXISTENCE_CONFIRMED_TEXT_UNCERTAIN" if content_review else "",
            content_candidates=[{"text": "章文待识别", "source": "whole_page_visual"}] if content_review else [],
            compare_evidence=cls._object_evidence(visual_object),
            compare_change_ranges=[TextRange(start=0, end=len(display), highlight_type="ADD")],
            structural_flags=["SIGNING_VISUAL_OBJECT"] if seal_like else [],
        )

    @staticmethod
    def _object_labels(visual_object: SigningVisualObject) -> tuple[str, str]:
        labels = {
            SigningVisualObjectType.COMPANY_ROUND_SEAL: ("公司圆章", "公司圆章（章文识别待复核）"),
            SigningVisualObjectType.PERSONAL_NAME_SEAL: ("个人姓名章", "个人姓名章（章文识别待复核）"),
            SigningVisualObjectType.HANDWRITTEN_SIGNATURE: ("手写签名", "手写签名"),
            SigningVisualObjectType.HANDWRITTEN_DATE: ("手写日期", visual_object.text or "手写日期"),
            SigningVisualObjectType.EDGE_SEAM_SEAL: ("骑缝章", "骑缝章"),
            SigningVisualObjectType.OTHER_STAMP: ("其他印章", "视觉印章（类型待复核）"),
            SigningVisualObjectType.UNKNOWN_SIGNING_MARK: ("签署视觉变化", "签署视觉变化（待复核）"),
        }
        if visual_object.raw_ref.get("participant_signature"):
            return "参与人员手写签名", "参与人员手写签名（姓名待识别）"
        return labels[visual_object.object_type]

    @staticmethod
    def _is_confirmed_object(visual_object: SigningVisualObject) -> bool:
        if visual_object.raw_ref.get("counterpart_edge_present"):
            return False
        if visual_object.object_type == SigningVisualObjectType.HANDWRITTEN_DATE:
            return False
        if (
            visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL
            and (
                len(set(visual_object.supporting_pages)) < 3
                or WholePageSigningVisualRecall._dense_edge_page_coverage(visual_object)
                or WholePageSigningVisualRecall._substantial_edge_page_count(visual_object) < 4
            )
        ):
            return False
        if (
            visual_object.object_type
            in {
                SigningVisualObjectType.COMPANY_ROUND_SEAL,
                SigningVisualObjectType.PERSONAL_NAME_SEAL,
                SigningVisualObjectType.HANDWRITTEN_SIGNATURE,
            }
            and visual_object.party_attribution_confidence < 0.55
        ):
            return False
        return visual_object.object_type not in {
            SigningVisualObjectType.OTHER_STAMP,
            SigningVisualObjectType.UNKNOWN_SIGNING_MARK,
        } and visual_object.visual_detection_confidence >= 0.8

    @classmethod
    def _object_details(cls, visual_object: SigningVisualObject) -> dict[str, Any]:
        page_count = int(visual_object.raw_ref.get("document_page_count") or 0)
        coverage_ratio = (
            len(set(visual_object.supporting_pages)) / page_count
            if page_count > 0
            else 0.0
        )
        return {
            "change_decision": "CONFIRMED" if cls._is_confirmed_object(visual_object) else "NEEDS_REVIEW",
            "decision_reason": (
                "WHOLE_PAGE_VISUAL_OBJECT_ADDED" if cls._is_confirmed_object(visual_object) else "VISUAL_SIGNING_CHANGE"
            ),
            "object_type": visual_object.object_type.value,
            "visual_detection_confidence": visual_object.visual_detection_confidence,
            "text_recognition_confidence": visual_object.text_recognition_confidence,
            "party_attribution_confidence": visual_object.party_attribution_confidence,
            "party_attribution_reason": visual_object.party_attribution_reason,
            "party_role": visual_object.party_role,
            "supporting_pages": visual_object.supporting_pages,
            "fragment_bboxes": [item.model_dump(mode="json") for item in visual_object.fragment_bboxes],
            "page_coverage_ratio": round(coverage_ratio, 3),
            "visual_candidate_reason": visual_object.raw_ref.get("reason", ""),
            "substantial_fragment_page_count": cls._substantial_edge_page_count(visual_object),
        }

    @staticmethod
    def _dense_edge_page_coverage(visual_object: SigningVisualObject) -> bool:
        page_count = int(visual_object.raw_ref.get("document_page_count") or 0)
        return page_count >= 4 and len(set(visual_object.supporting_pages)) / page_count >= 0.65

    @staticmethod
    def _substantial_edge_page_count(visual_object: SigningVisualObject) -> int:
        if visual_object.object_type != SigningVisualObjectType.EDGE_SEAM_SEAL:
            return 0
        boxes = visual_object.fragment_bboxes or [visual_object.bbox]
        pages = visual_object.supporting_pages or [visual_object.page_no]
        if len(boxes) != len(pages):
            return 0
        return len(
            {
                page_no
                for page_no, bbox in zip(pages, boxes, strict=True)
                if bbox.y1 - bbox.y0 >= 45.0
            }
        )

    @staticmethod
    def _object_flags(visual_object: SigningVisualObject) -> list[str]:
        flags = ["SIGNING_VISUAL_OBJECT"]
        if WholePageSigningVisualRecall._is_confirmed_object(visual_object):
            flags.append("VISUAL_OBJECT_EXISTENCE_CONFIRMED")
        if visual_object.object_type in {
            SigningVisualObjectType.COMPANY_ROUND_SEAL,
            SigningVisualObjectType.PERSONAL_NAME_SEAL,
            SigningVisualObjectType.EDGE_SEAM_SEAL,
            SigningVisualObjectType.OTHER_STAMP,
        }:
            flags.append("SIGNING_SEAL_CHANGE")
        elif visual_object.object_type == SigningVisualObjectType.HANDWRITTEN_DATE:
            flags.append("SIGNING_DATE_CHANGE")
        else:
            flags.append("SIGNING_SIGNATURE_CHANGE")
        if visual_object.party_attribution_confidence < 0.55 and visual_object.party_role != "unknown":
            flags.append("SIGNING_PARTY_ATTRIBUTION_LOW_CONFIDENCE")
        return flags

    @staticmethod
    def _object_evidence(visual_object: SigningVisualObject) -> list[EvidenceBox]:
        boxes = visual_object.fragment_bboxes or [visual_object.bbox]
        pages = visual_object.supporting_pages or [visual_object.page_no]
        if len(boxes) != len(pages):
            pages = [visual_object.page_no] * len(boxes)
        if visual_object.object_type == SigningVisualObjectType.EDGE_SEAM_SEAL:
            # Keep raw fragments in diagnostics, but draw a physical cluster
            # once. The size/gap bounds preserve distinct stamps and page sides.
            clusters: list[tuple[int, BBox]] = []
            for page_no, box in sorted(zip(pages, boxes), key=lambda pair: (pair[0], pair[1].y0, pair[1].x0)):
                merged = True
                while merged:
                    merged = False
                    for i, (other_page, other) in enumerate(clusters):
                        gap_x = max(0, box.x0-other.x1, other.x0-box.x1)
                        gap_y = max(0, box.y0-other.y1, other.y0-box.y1)
                        union = BBox(x0=min(box.x0,other.x0), y0=min(box.y0,other.y0),
                                     x1=max(box.x1,other.x1), y1=max(box.y1,other.y1))
                        if (page_no == other_page and gap_x <= 12 and gap_y <= 12
                                and union.x1-union.x0 <= 110 and union.y1-union.y0 <= 160):
                            clusters.pop(i)
                            box = union
                            merged = True
                            break
                clusters.append((page_no, box))
            pages, boxes = zip(*clusters) if clusters else ([], [])
        return [
            EvidenceBox(
                page_no=page_no,
                bbox=bbox,
                method="whole_page_visual_object",
                text=WholePageSigningVisualRecall._object_labels(visual_object)[1],
                highlight_type="ADD",
                confidence=visual_object.visual_detection_confidence,
                evidence_quality="HIGH" if visual_object.visual_detection_confidence >= 0.8 else "MEDIUM",
                text_confidence=(visual_object.text_recognition_confidence or None),
            )
            for page_no, bbox in zip(pages, boxes, strict=True)
        ]

    @staticmethod
    def _correct_global_signing_dates(signing_diffs: list[DiffItem]) -> None:
        for diff in signing_diffs:
            if diff.source_type != "signing_region" or diff.diff_type != "ADD":
                continue
            if "签订日期" not in diff.title and "签字日期" not in diff.title:
                continue
            evidence = diff.compare_evidence
            if not evidence:
                continue
            details = dict(diff.match_score_details)
            if details.get("field_key") != "date":
                continue
            if details.get("party_attribution_reason") != "GLOBAL_SIGNING_DATE_ANCHOR":
                continue
            diff.title = re.sub(r"^(?:甲方|乙方)\s*·\s*", "", diff.title)
            diff.readable_change = re.sub(r"^(?:甲方|乙方)\s*·\s*", "", diff.readable_change)
            details.update(
                {
                    "party_role": "unknown",
                    "party_attribution_reason": "GLOBAL_SIGNING_DATE_ANCHOR",
                    "party_attribution_confidence": 0.9,
                }
            )
            diff.match_score_details = details

    @staticmethod
    def safety_net_candidate(page: Page, bbox: BBox, *, visual_delta: float, explained: bool) -> SigningVisualObject | None:
        if explained or visual_delta < 0.25 or not WholePageSigningVisualRecall._page_has_signing_context(page):
            return None
        party, reason, confidence = WholePageSigningVisualRecall._attribute_party(
            page, bbox, SigningVisualObjectType.UNKNOWN_SIGNING_MARK
        )
        return SigningVisualObject(
            object_id=f"VIS-{page.page_no}-SAFETY",
            object_type=SigningVisualObjectType.UNKNOWN_SIGNING_MARK,
            page_no=page.page_no,
            bbox=bbox,
            visual_detection_confidence=min(0.79, visual_delta),
            party_role=party,
            party_attribution_reason=reason,
            party_attribution_confidence=confidence,
            supporting_pages=[page.page_no],
            raw_ref={"reason": "UNEXPLAINED_SIGNING_VISUAL_DELTA"},
        )

    @staticmethod
    def _dependencies() -> tuple[Any, Any] | None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None
        return cv2, np
