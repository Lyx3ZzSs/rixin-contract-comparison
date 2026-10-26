from __future__ import annotations

from pathlib import Path
import fitz

from app.models import Document
from app.services.signing_region.models import SigningVisualObject


def corresponding_red_graphics(
    original: Document, left: SigningVisualObject, compare: Document, right: SigningVisualObject,
    *, verify_ink: bool = False,
) -> dict:
    """Require geometrically consistent red-ink features, allowing scan rotation/crop.

    Presence and bounding-box proximity alone never establish graphic equivalence.
    Unavailable dependencies, missing pixels and ambiguous features fail closed.
    """
    result = {"method": "RED_FEATURE_AFFINE", "equivalent": False}
    try:
        import cv2
        import numpy as np

        page_edges = []
        def crop(document, obj):
            if not Path(document.path).is_file():
                return None
            model_page = next(p for p in document.pages if p.page_no == obj.page_no)
            with fitz.open(document.path) as pdf:
                page = pdf[obj.page_no - 1]
                sx, sy = page.rect.width / model_page.width, page.rect.height / model_page.height
                bb = obj.bbox
                pad = 60 if verify_ink else 0
                clip = fitz.Rect((bb.x0-pad) * sx, (bb.y0-pad) * sy,
                                 (bb.x1+pad) * sx, (bb.y1+pad) * sy) & page.rect
                page_edges.append((clip.x0 <= 0, clip.x1 >= page.rect.width,
                                   clip.y0 <= 0, clip.y1 >= page.rect.height))
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(4, 4), clip=clip, colorspace=fitz.csRGB, alpha=False, annots=False
                )
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(np.int16)
                ink = np.clip((rgb[:, :, 0] - np.minimum(rgb[:, :, 1], rgb[:, :, 2])) * 3, 0, 255).astype("uint8")
                return ink

        a, b = crop(original, left), crop(compare, right)
        if a is None or b is None:
            return result
        sift = cv2.SIFT_create(contrastThreshold=0.015)
        ka, da = sift.detectAndCompute(a, None)
        kb, db = sift.detectAndCompute(b, None)
        if da is None or db is None or len(da) < 12 or len(db) < 12:
            return result
        matches = [
            pair[0]
            for pair in cv2.BFMatcher().knnMatch(da, db, k=2)
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
        ]
        # Each feature can support only one correspondence.
        unique = {}
        for match in sorted(matches, key=lambda m: m.distance):
            unique.setdefault(match.trainIdx, match)
        matches = list(unique.values())
        if len(matches) < 12:
            return result
        src = np.float32([ka[m.queryIdx].pt for m in matches])
        dst = np.float32([kb[m.trainIdx].pt for m in matches])
        estimator = cv2.estimateAffine2D if verify_ink else cv2.estimateAffinePartial2D
        transform, inliers = estimator(src, dst, method=cv2.RANSAC, ransacReprojThreshold=6)
        if transform is None or inliers is None:
            return result
        count = int(inliers.sum())
        scale = float(np.hypot(transform[0, 0], transform[1, 0]))
        support = src[inliers.ravel().astype(bool)]
        spread = np.ptp(support, axis=0) if count else np.zeros(2)
        result.update(
            matches=len(matches),
            inliers=count,
            inlier_ratio=count / len(matches),
            scale=scale,
            equivalent=bool(
                count >= 12
                and count / len(matches) >= 0.5
                and 0.5 <= scale <= 2
                and spread[0] >= a.shape[1] * 0.25
                and spread[1] >= a.shape[0] * 0.25
            ),
        )
        if verify_ink and result['equivalent']:
            # Geometric matches alone can match the unchanged ring of a
            # changed seal. Check all red strokes in both directions as well.
            aw = cv2.warpAffine(a, transform, (b.shape[1], b.shape[0]))
            am, bm = (aw > 45).astype('uint8'), (b > 45).astype('uint8')
            inverse = cv2.invertAffineTransform(transform)
            visible_a = cv2.warpAffine(np.ones(b.shape, np.uint8), inverse, (a.shape[1], a.shape[0]))
            visible_b = cv2.warpAffine(np.ones(a.shape, np.uint8), transform, (b.shape[1], b.shape[0]))
            clipped = [int(((a > 45) & (visible_a == 0)).sum()),
                       int(((b > 45) & (visible_b == 0)).sum())]
            def unexplained_clip(ink, visible, matrix, shape, edges):
                ys, xs = np.where((ink > 45) & (visible == 0))
                pts = np.column_stack((xs, ys, np.ones(len(xs)))) @ matrix.T
                outside_page = ((edges[0] & (pts[:, 0] <= 1)) |
                                (edges[1] & (pts[:, 0] >= shape[1]-2)) |
                                (edges[2] & (pts[:, 1] <= 1)) |
                                (edges[3] & (pts[:, 1] >= shape[0]-2)))
                return int((~outside_page).sum())
            unexplained = [unexplained_clip(a, visible_a, transform, b.shape, page_edges[1]),
                           unexplained_clip(b, visible_b, inverse, a.shape, page_edges[0])]
            # Only actual PDF edges may explain missing scan content; a tight
            # region crop is never allowed to hide changed ink.
            common = cv2.erode(visible_b, np.ones((7, 7), np.uint8), borderValue=0)
            # Scanner edge bleed is not a readable inscription. Limit this
            # guard to physical page boundaries, never arbitrary object ROIs.
            edge_guard = 16  # 4 PDF points at the render scale of 4.
            for boundary, enabled in enumerate(page_edges[1]):
                if not enabled:
                    continue
                if boundary == 0:
                    common[:, :edge_guard] = 0
                elif boundary == 1:
                    common[:, -edge_guard:] = 0
                elif boundary == 2:
                    common[:edge_guard, :] = 0
                else:
                    common[-edge_guard:, :] = 0
            metrics = []
            for x, y in ((am, (b > 15).astype('uint8')), (bm, (aw > 15).astype('uint8'))):
                novel = x * common * (1-cv2.dilate(y, np.ones((5, 5), np.uint8)))
                _, _, stats, _ = cv2.connectedComponentsWithStats(novel, 8)
                metrics.append({'novel_ratio': float(novel.sum()/max(1, x.sum())),
                                'largest_area': max((int(s[cv2.CC_STAT_AREA]) for s in stats[1:]), default=0), 'components': sorted([s.tolist() for s in stats[1:]], key=lambda s:s[4], reverse=True)[:3]})
            result.update(ink_metrics=metrics, clipped_ink=clipped, unexplained_clipped_ink=unexplained,
                          comparison_scope='visible_page_intersection' if max(clipped) else 'full_region',
                          equivalent=max(unexplained) <= 16 and all(m['novel_ratio'] <= .01
                                                               and m['largest_area'] <= 16 for m in metrics))
    except (ImportError, OSError, ValueError, RuntimeError, StopIteration):
        return result
    return result
