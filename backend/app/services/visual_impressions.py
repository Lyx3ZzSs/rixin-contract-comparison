"""Admit ink objects using physical evidence, retaining unresolved impressions.

A pale pixel difference is not proof of a new signature. Mirror corroboration
can explain it; contrast alone only routes it to a review observation. Strong
ink in the same detector rectangle keeps its independent business ownership.
"""
from functools import lru_cache
from pathlib import Path

import cv2
import fitz
import numpy as np

from app.models import BBox, ComparisonObservation


class ImpressionVerifier:
    def __init__(self, path):
        self.pdf = fitz.open(path) if Path(path).is_file() else None

    def close(self):
        self.render.cache_clear()
        if self.pdf is not None:
            self.pdf.close()

    @lru_cache(maxsize=8)
    def render(self, number):
        if self.pdf is None or not 1 <= number <= len(self.pdf):
            return None
        p = self.pdf[number-1]
        pix = p.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)

    @staticmethod
    def ink(image, seal=False, strong=False):
        if seal:
            rgb = image.astype(np.int16)
            return ((rgb[:, :, 0]-np.minimum(rgb[:, :, 1], rgb[:, :, 2])) > (65 if strong else 12)).astype(np.uint8)*255
        return (cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) < (110 if strong else 210)).astype(np.uint8)*255

    def mirrored_peer(self, page, bbox, crop, seal=False):
        template = self.ink(crop, seal)
        ys, xs = np.nonzero(template)
        if len(xs) < 40 or len(set(xs)) < 15 or len(set(ys)) < 12:
            return None
        template = template[ys.min():ys.max()+1, xs.min():xs.max()+1]
        best = [(-1., None), (-1., None)]
        for peer in [page-1, page+1]:
            image = self.render(peer)
            if image is None:
                continue
            # Search the reflected physical column, allowing scan translation.
            h, w = image.shape[:2]
            x0, x1 = max(0, int(w-bbox.x1*1.5-100)), min(w, int(w-bbox.x0*1.5+100))
            y0, y1 = max(0, int(bbox.y0*1.5-80)), min(h, int(bbox.y1*1.5+80))
            target = self.ink(image[y0:y1, x0:x1], seal, strong=True)
            for reflected in (0, 1):
                base = cv2.flip(template, 1) if reflected else template
                for scale in (.8, .9, 1., 1.1, 1.2, 1.3):
                    resized = cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
                    th, tw = resized.shape
                    if th > target.shape[0] or tw > target.shape[1]:
                        continue
                    for angle in (-4, 0, 4):
                        stamp = cv2.warpAffine(resized, cv2.getRotationMatrix2D((tw/2, th/2), angle, 1), (tw, th))
                        if np.std(stamp) < 1:
                            continue
                        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(target, stamp, cv2.TM_CCOEFF_NORMED))
                        if score > best[reflected][0]:
                            best[reflected] = (score, dict(page=peer, rotation_degrees=angle,
                                bbox=[(x0+loc[0])/1.5, (y0+loc[1])/1.5, (x0+loc[0]+tw)/1.5, (y0+loc[1]+th)/1.5]))
        if best[1][0] >= .48 and best[1][0] >= best[0][0]+.06:
            return dict(reason='MIRRORED_ADJACENT_PAGE_IMPRESSION', mirror_score=round(best[1][0], 3),
                        direct_score=round(best[0][0], 3), counterpart=best[1][1])
        if seal and best[1][0] >= .2:
            # Circular outlines alone cannot prove orientation. Retain this
            # competing physical explanation for review, never erase the stamp.
            return dict(reason='FAINT_INK_OR_SCAN_ARTIFACT_UNRESOLVED', mirror_score=round(best[1][0], 3),
                        direct_score=round(best[0][0], 3), counterpart=best[1][1])
        return None

    def inspect(self, evidence, *, seal=False, check_components=True):
        image = self.render(evidence.page_no)
        if image is None:
            return None
        b = evidence.bbox
        x0, y0 = max(0, int(b.x0*1.5)), max(0, int(b.y0*1.5))
        x1, y1 = min(image.shape[1], int(b.x1*1.5)+1), min(image.shape[0], int(b.y1*1.5)+1)
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        ink = self.ink(crop, seal)
        strong = self.ink(crop, seal, strong=True)
        if not seal and np.any(ink):
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
            source_tone = float(np.median(gray[ink > 0]))
            if source_tone >= 110:
                proof = self.mirrored_peer(evidence.page_no, b, crop)
                if (proof and proof['mirror_score'] >= .55
                        and proof['mirror_score'] - proof['direct_score'] >= .15):
                    peer = proof['counterpart']
                    px0, py0, px1, py1 = [round(v*1.5) for v in peer['bbox']]
                    peer_crop = self.render(peer['page'])[py0:py1, px0:px1]
                    peer_gray = cv2.cvtColor(peer_crop, cv2.COLOR_RGB2GRAY)
                    peer_ink = self.ink(peer_crop)
                    peer_tone = float(np.median(peer_gray[peer_ink > 0]))
                    stamp = self.ink(peer_crop, strong=True)
                    sh, sw = stamp.shape
                    stamp = cv2.warpAffine(stamp, cv2.getRotationMatrix2D(
                        (sw/2, sh/2), -peer['rotation_degrees'], 1), (sw, sh))
                    ys, xs = np.nonzero(ink)
                    target = strong[ys.min():ys.max()+1, xs.min():xs.max()+1] > 0
                    explained = cv2.resize(cv2.flip(stamp, 1), (target.shape[1], target.shape[0]))
                    explained = cv2.dilate(explained, np.ones((3, 3), np.uint8)) > 0
                    coverage = np.count_nonzero(target & explained)/max(1, np.count_nonzero(target))
                    # A translucent copy can contain dark strokes. Require its
                    # darker peer to explain those strokes before consuming it.
                    if source_tone-peer_tone >= 40 and coverage >= .9:
                        proof.update(explained_strong_ink_ratio=round(float(coverage), 3),
                                     source_ink_tone=source_tone, counterpart_ink_tone=peer_tone)
                        return None, b, proof
        if seal:
            components = [(ink > 0)]
        else:
            _, labels = cv2.connectedComponents(cv2.dilate(ink, np.ones((5, 7), np.uint8)))
            components = [(labels == i) & (ink > 0) for i in range(1, int(labels.max())+1)]
        definite, faint = np.zeros(ink.shape, bool), np.zeros(ink.shape, bool)
        for mask in components:
            count = int(mask.sum())
            if count < 8:
                continue
            ratio = np.count_nonzero(mask & (strong > 0))/count
            is_definite = ratio >= (.35 if seal else .25)
            if is_definite and not seal and check_components:
                gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
                if float(np.median(gray[mask])) >= 110:
                    ys, xs = np.nonzero(mask)
                    context_box = BBox(x0=max(0,(x0+xs.min())/1.5-24),
                                       y0=max(0,(y0+ys.min())/1.5-6),
                                       x1=(x0+xs.max()+1)/1.5+24,
                                       y1=(y0+ys.max()+1)/1.5+6)
                    context = self.inspect(evidence.model_copy(update={'bbox':context_box}),
                                           check_components=False)
                    if (context is not None and context[0] is None and context[2]
                            and context[2].get('explained_strong_ink_ratio',0) >= .9):
                        is_definite = False
            (definite if is_definite else faint).__ior__(mask)
        def box(mask):
            ys, xs = np.nonzero(mask)
            if not len(xs):
                return None
            return BBox(x0=(x0+xs.min())/1.5, y0=(y0+ys.min())/1.5,
                        x1=(x0+xs.max()+1)/1.5, y1=(y0+ys.max()+1)/1.5)
        certain_box, faint_box = box(definite), box(faint)
        if faint_box is None:
            if certain_box is None and not seal:
                # The visual detector can use a lower contrast threshold than
                # this verifier. No surviving pixels means unobserved, not a
                # confirmed added annotation.
                return None, b, None
            return None
        if (not seal and certain_box is not None
                and np.count_nonzero(strong)/max(1, np.count_nonzero(ink)) < .25):
            whole_proof = self.mirrored_peer(evidence.page_no, b, crop)
            if whole_proof and whole_proof['mirror_score'] >= .7:
                peer = whole_proof['counterpart']
                px0, py0, px1, py1 = [round(v*1.5) for v in peer['bbox']]
                stamp = self.ink(self.render(peer['page'])[py0:py1, px0:px1], strong=True)
                sh, sw = stamp.shape
                stamp = cv2.warpAffine(stamp, cv2.getRotationMatrix2D((sw/2, sh/2), -peer['rotation_degrees'], 1), (sw, sh))
                ys, xs = np.nonzero(ink)
                target = strong[ys.min():ys.max()+1, xs.min():xs.max()+1] > 0
                explained = cv2.resize(cv2.flip(stamp, 1), (target.shape[1], target.shape[0]))
                explained = cv2.dilate(explained, np.ones((3, 3), np.uint8)) > 0
                coverage = np.count_nonzero(target & explained)/max(1, np.count_nonzero(target))
                if coverage >= .9:
                    whole_proof['explained_strong_ink_ratio'] = round(coverage, 3)
                    return None, b, whole_proof
        # Corroborate only the faint object, never the neighbouring real signature.
        fx0, fy0 = max(0, int(faint_box.x0*1.5)-x0), max(0, int(faint_box.y0*1.5)-y0)
        fx1, fy1 = min(crop.shape[1], int(faint_box.x1*1.5)-x0+1), min(crop.shape[0], int(faint_box.y1*1.5)-y0+1)
        proof = self.mirrored_peer(evidence.page_no, faint_box, crop[fy0:fy1, fx0:fx1], seal)
        if seal and proof is None:
            # A faint model-detected seal with no adjacent counterpart remains
            # a seal. Low saturation by itself cannot overturn its detection.
            return None
        return certain_box, faint_box, proof


def reconcile_impressions(diffs, original_path, compare_path):
    verifiers = {s: ImpressionVerifier(p) for s, p in [('original', original_path), ('compare', compare_path)]}
    kept, observations, decisions = [], [], []
    try:
        for raw in diffs:
            diff = raw.model_copy(deep=True)
            if diff.diff_type not in {'ADD', 'DELETE'}:
                kept.append(diff)
                continue
            side = 'compare' if diff.diff_type == 'ADD' else 'original'
            field = side+'_evidence'
            before = getattr(diff, field)
            after = []
            for index, evidence in enumerate(before):
                seal = diff.source_type == 'seal'
                if seal and diff.match_score_details.get('object_type') == 'EDGE_SEAM_SEAL':
                    after.append(evidence)
                    continue
                footer = evidence.method == 'footer_visual_registered'
                if diff.source_type == 'header_footer' and evidence.method == 'header_footer':
                    image = verifiers[side].render(evidence.page_no)
                    footer = (image is not None and evidence.bbox.y0*1.5 >= image.shape[0]*.85
                              and evidence.bbox.y1-evidence.bbox.y0 >= 20
                              and len(''.join(evidence.text.split())) <= 12)
                if not (seal or footer):
                    after.append(evidence)
                    continue
                result = verifiers[side].inspect(evidence, seal=seal)
                if footer and result is not None and result[0] is not None:
                    # Component separation changes the matching crop. Verify
                    # the remaining dark component itself, so a wide OCR box
                    # does not dilute its otherwise strong mirror agreement.
                    fragment = evidence.model_copy(update={'bbox': result[0]})
                    fragment_result = verifiers[side].inspect(fragment)
                    if (fragment_result is not None and fragment_result[0] is None
                            and fragment_result[2]
                            and fragment_result[2]['reason'] == 'MIRRORED_ADJACENT_PAGE_IMPRESSION'
                            and fragment_result[2].get('explained_strong_ink_ratio', 0) >= .9):
                        result = (None, result[1], result[2])
                if footer and evidence.method == 'header_footer':
                    # OCR may box only one dark fragment of a pale mirrored
                    # word. Check its immediate physical context before treating
                    # that fragment as an independent handwritten addition.
                    b = evidence.bbox
                    expanded = evidence.model_copy(update={'bbox': BBox(
                        x0=max(0, b.x0-24), y0=max(0, b.y0-6), x1=b.x1+24, y1=b.y1+6)})
                    context = verifiers[side].inspect(expanded)
                    if (context is not None and context[0] is None and context[2]
                            and context[2]['reason'] == 'MIRRORED_ADJACENT_PAGE_IMPRESSION'
                            and context[2]['mirror_score'] >= .65):
                        result = context
                if result is None:
                    after.append(evidence)
                    continue
                definite, faint, proof = result
                if seal:
                    # Mirror evidence explains an impression's origin, not its
                    # presence on this page. Count the one-sided image change
                    # without claiming a separate physical stamping action.
                    after.append(evidence)
                    diff.quality_status = 'NEEDS_REVIEW'
                    diff.match_score_details['seal_change_decision'] = 'CANDIDATE'
                    diff.match_score_details.setdefault('impression_evidence', []).append(proof or {})
                    diff.readable_change = '检测到单侧印章印迹，计入差异候选；是否为独立盖章或邻页透印需复核。'
                    if (proof or {}).get('reason') == 'MIRRORED_ADJACENT_PAGE_IMPRESSION':
                        diff.match_score_details['seal_origin_decision'] = 'SUSPECTED_MIRROR'
                        diff.title = '印章印迹（疑似透印）'
                        peer_page = proof['counterpart']['page']
                        diff.readable_change = (
                            f'本页印章印迹发生变化，计入差异；疑似与 PDF 第 {peer_page} 页印章形成镜像透印，'
                            '不代表已确认一次独立盖章。'
                        )
                        decisions.append(dict(diff_id=diff.diff_id, side=side, page=evidence.page_no,
                                              reason=proof['reason'], faint_bbox=faint.model_dump(), proof=proof,
                                              retained_bbox=evidence.bbox.model_dump()))
                    continue
                if definite is not None:
                    retained = evidence.model_copy(update={'bbox': definite})
                    if footer and evidence.method == 'header_footer':
                        old = evidence.bbox
                        retained_area = (definite.x1-definite.x0)*(definite.y1-definite.y0)
                        old_area = max(1, (old.x1-old.x0)*(old.y1-old.y0))
                        if (retained_area / old_area < .65
                                and (evidence.text_confidence if evidence.text_confidence is not None
                                     else diff.text_confidence if diff.text_confidence is not None else 0) < .8):
                            # A source reading does not identify a cropped subset.
                            # Keep it for audit, not as the retained ink's text.
                            diff.content_candidates.append({'text': evidence.text, 'source': 'footer_ocr_before_crop', 'side': side})
                            diff.match_score_details.setdefault('footer_crop_readings', []).append(evidence.model_dump(mode='json'))
                            retained = retained.model_copy(update={'text': '检测到页脚笔迹（具体字迹待辨认）', 'char_boxes': [], 'text_confidence': None})
                            diff.content_recognition_status = 'NEEDS_REVIEW'
                            diff.content_recognition_reason = 'FOOTER_CROP_READING_UNVERIFIED'
                            diff.quality_status = 'NEEDS_REVIEW'
                    after.append(retained)
                reason = proof['reason'] if proof else 'FAINT_INK_OR_SCAN_ARTIFACT_UNRESOLVED'
                decisions.append(dict(diff_id=diff.diff_id, side=side, page=evidence.page_no,
                                      reason=reason, faint_bbox=faint.model_dump(), proof=proof,
                                      retained_bbox=definite.model_dump() if definite else None))
                if reason == 'FAINT_INK_OR_SCAN_ARTIFACT_UNRESOLVED':
                    title = '浅色印迹待复核'
                    message = '此处存在浅色印迹，尚不能区分真实笔迹与透印、扫描残影；未认定为合同内容新增或删除。'
                    observations.append(ComparisonObservation(
                        observation_id=f'impression-{diff.diff_id}-{index}', source_type=diff.source_type, reason=reason,
                        title=title, message=message, decision_evidence=proof or {},
                        source_diff_ids=[diff.diff_id],
                        **{field: [evidence.model_copy(update={'bbox': faint, 'method': 'comparison_observation', 'highlight_type': None, 'char_boxes': []})]},
                    ))
            setattr(diff, field, after)
            if diff.content_recognition_reason == 'FOOTER_CROP_READING_UNVERIFIED':
                texts = list(dict.fromkeys(e.text for e in after if e.text))
                setattr(diff, side+'_text', ' '.join(texts))
                setattr(diff, side+'_snippet', ' '.join(texts))
                setattr(diff, side+'_change_ranges', [])
                diff.readable_change = '页脚笔迹发生变化；区域已重新定位，具体字迹需复核。'
            if after or not before:
                kept.append(diff)
        from app.services.footer_ink_changes import retain_registered_ink
        kept.extend(retain_registered_ink(diffs, decisions, verifiers))
        return kept, observations, decisions
    finally:
        for verifier in verifiers.values():
            verifier.close()
