"""Explicit PDF/page <-> upright model-local table coordinates.

The page raster is already in PDF display orientation (the renderer applies
/Rotate). Applying that rotation again would turn upright text sideways. Project
all region corners through the renderer's affine, then translate the crop origin.
No OCR, guessed axis swaps, or identity substitution is involved.
"""

import math
import numpy as np
from app.services.extractors.contract_v3.artifact import transform_point, enclosing


def inverse_affine(matrix):
    a, b, c, d, e, f = matrix
    det = a * d - b * c
    if not math.isfinite(det) or abs(det) < 1e-12:
        raise ValueError("Singular table transform")
    return [d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det]


def compose_affine(after, before):
    a, b, c, d, e, f = after
    g, h, i, j, tx, ty = before
    return [a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*tx+c*ty+e, b*tx+d*ty+f]


def orient_table_crop(crop, transform, angle_ccw):
    """Quarter-turn pixel *edges*, after PDF display rotation, before model deskew."""
    h, w = crop.shape[:2]
    rotations = {0: [1, 0, 0, 1, 0, 0], 90: [0, -1, 1, 0, 0, w],
                 270: [0, 1, -1, 0, h, 0]}
    if angle_ccw not in rotations:
        raise ValueError("Unsupported table reading angle")
    rotation = rotations[angle_ccw]
    forward = compose_affine(rotation, transform["page_to_model"])
    return np.rot90(crop, angle_ccw // 90).copy(), {
        **transform, "reading_angle_ccw": angle_ccw, "crop_to_oriented": rotation,
        "page_to_model": forward, "model_to_page": inverse_affine(forward),
        "model_polygon": [transform_point(p, forward) for p in transform["page_polygon"]],
    }


def upright_table_crop(image, page_polygon, page_to_render):
    a, b, c, d, e, f = map(float, page_to_render)
    # Axis-aligned rotations at any positive raster scale, including anisotropic
    # 90/270 renders. Refuse skew/reflection; do not silently reinterpret them.
    axis = abs(b) < 1e-9 and abs(c) < 1e-9 and a * d > 0
    quarter = abs(a) < 1e-9 and abs(d) < 1e-9 and -b * c > 0
    if not (axis or quarter) or not all(math.isfinite(v) for v in page_to_render):
        raise ValueError("Expected a finite 0/90/180/270 PDF display transform")
    bounds = enclosing([transform_point(p, page_to_render) for p in page_polygon])
    height, width = image.shape[:2]
    x0, y0 = max(0, math.floor(bounds[0])), max(0, math.floor(bounds[1]))
    x1, y1 = min(width, math.floor(bounds[2]) + 1), min(height, math.floor(bounds[3]) + 1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Table crop is outside the rendered page")
    page_to_model = [a, b, c, d, e - x0, f - y0]
    model_to_page = inverse_affine(page_to_model)
    return image[y0:y1, x0:x1].copy(), dict(
        page_to_render=list(page_to_render),
        render_crop=[x0, y0, x1, y1],
        page_to_model=page_to_model,
        model_to_page=model_to_page,
        normalization="PDF_DISPLAY_ROTATION_THEN_REGION_TRANSLATION",
        page_polygon=page_polygon,
        model_polygon=[transform_point(p, page_to_model) for p in page_polygon],
    )
