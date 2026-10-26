"""Coverage-based PP-OCRv6 routing with native glyph masking."""

from __future__ import annotations

import base64
from io import BytesIO
import math

import httpx
from PIL import Image, ImageDraw

from app.config import Settings, settings
from app.services.extractors.base import DocumentExtractionError
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.extractors.contract_v3.artifact import (
    assign_order,
    enclosing,
    owner,
    rectangle,
    transform_point,
    verify_conservation,
)
from app.services.extractors.contract_v3.geometry import area, overlap, union


def coverage_plan(artifact: dict, sample: dict) -> dict:
    good = [c for c in artifact["native_chars"] if c["usable"] and not c["char"].isspace()]
    if not good:
        return {
            "route": "SCANNED",
            "regions": [
                {"bbox": [0, 0, artifact["width"], artifact["height"]], "reason": "no_reliable_native", "scope": "page"}
            ],
        }
    requests = []
    for image in sample["raster_images"]:
        box = list(image["bbox"])
        if image["width"] >= 32 and image["height"] >= 16 and area(box) >= 64:
            requests.append({"bbox": box, "reason": "raster_coverage_check", "scope": "region"})
    for region in artifact["regions"]:
        if region["raw_layout_type"] in {"seal", "header_image", "footer_image"}:
            continue
        if not any(c["region_id"] == region["region_id"] for c in good):
            requests.append({"bbox": region["bbox"], "reason": "layout_region_without_native", "scope": "region"})
    for char in artifact["native_chars"]:
        if not char["usable"]:
            requests.append({"bbox": char["bbox"], "reason": "unreliable_native", "scope": "region"})
    merged = []
    while requests:
        current, requests = requests[0], requests[1:]
        changed = True
        while changed:
            changed = False
            remaining = []
            for other in requests:
                if overlap(current["bbox"], other["bbox"]) > 0:
                    current = {
                        "bbox": union([current["bbox"], other["bbox"]]),
                        "reason": "merged_uncovered_regions",
                        "scope": "region",
                    }
                    changed = True
                else:
                    remaining.append(other)
            requests = remaining
        merged.append(current)
    return {"route": "MIXED" if merged else "NATIVE_GOOD", "regions": merged}


def word_provenance(result, line_index, text, crop_origin, inverse, request_id):
    words = result.get("text_word", [])
    coordinates = result.get("text_word_region") or result.get("text_word_boxes", [])
    if line_index >= len(words) or line_index >= len(coordinates):
        return [], [i for i, char in enumerate(text) if not char.isspace()]
    evidence, cursor = [], 0
    for wi, (word, raw) in enumerate(zip(words[line_index], coordinates[line_index], strict=True)):
        start = text.find(word, cursor)
        if start < 0 or not raw:
            continue
        raw_polygon = rectangle(raw) if len(raw) == 4 and isinstance(raw[0], (float, int)) else raw
        polygon = [transform_point([p[0] + crop_origin[0], p[1] + crop_origin[1]], inverse) for p in raw_polygon]
        for offset, char in enumerate(word):
            evidence.append(
                {
                    "char": char,
                    "text_index": start + offset,
                    "bbox": enclosing(polygon),
                    "polygon": polygon,
                    "granularity": "character" if len(word) == 1 else "word",
                    "raw_word": word,
                    "raw_word_polygon": raw_polygon,
                    "ocr_request_id": request_id,
                    "word_index": wi,
                    "crop_origin": crop_origin,
                    "pixel_to_native": inverse,
                }
            )
        cursor = start + len(word)
    covered = {e["text_index"] for e in evidence}
    return evidence, [i for i, char in enumerate(text) if not char.isspace() and i not in covered]


class SelectiveOCRRouter:
    def __init__(self, config: Settings = settings, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client
        self.request_count = 0

    def _request(self, png: bytes) -> dict:
        base = self.config.ppocrv5_url.strip().rstrip("/")
        if not base:
            raise DocumentExtractionError("未配置 PP-OCRv6 OCR 服务 URL")
        url = base if base.endswith("/ocr") else base + "/ocr"
        headers = {"Content-Type": "application/json"}
        if self.config.ppocrv5_access_token:
            headers["Authorization"] = "Bearer " + self.config.ppocrv5_access_token
        body = {
            "file": base64.b64encode(png).decode("ascii"),
            "fileType": 1,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "textRecScoreThresh": 0.0,
            "returnWordBox": True,
            "visualize": False,
        }
        try:
            if self.client is None:
                with httpx.Client(timeout=self.config.ppocrv5_timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=body)
            else:
                response = self.client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("errorCode") not in (0, None):
                raise DocumentExtractionError(
                    f"PP-OCRv6 返回业务错误: {payload.get('errorMsg', 'invalid payload') if isinstance(payload, dict) else 'invalid payload'}"
                )
            self.request_count += 1
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise DocumentExtractionError(f"PP-OCRv6 请求失败: {exc}") from exc

    def prefetch_scanned(self, image: Image.Image) -> dict:
        data = BytesIO()
        image.save(data, format="PNG")
        return self._request(data.getvalue())

    def recognize(self, artifact: dict, sample: dict, image: Image.Image, *, prefetched: dict | None = None) -> int:
        plan = artifact["coverage"]
        forward = sample["coordinate_transform"]["native_to_pixel"]
        inverse = sample["coordinate_transform"]["pixel_to_native"]
        if plan["route"] == "NATIVE_GOOD":
            if prefetched is not None:
                raise DocumentExtractionError("Native 页面不得 OCR")
            return 0
        count = 0
        for index, region in enumerate(plan["regions"]):
            request_id = f"{sample['id']}:ocr:{index}"
            pixel = enclosing([transform_point(p, forward) for p in rectangle(region["bbox"])])
            x0, y0 = max(0, math.floor(pixel[0])), max(0, math.floor(pixel[1]))
            x1, y1 = min(image.width, math.ceil(pixel[2])), min(image.height, math.ceil(pixel[3]))
            if x1 <= x0 or y1 <= y0:
                continue
            crop = image.crop((x0, y0, x1, y1))
            draw = ImageDraw.Draw(crop)
            for char in artifact["native_chars"]:
                if char["usable"] and overlap(char["bbox"], region["bbox"]) > 0:
                    polygon = [transform_point(p, forward) for p in rectangle(char["bbox"])]
                    draw.polygon([(x - x0, y - y0) for x, y in polygon], fill="white")
            if prefetched is None:
                data = BytesIO()
                crop.save(data, format="PNG")
                payload = self._request(data.getvalue())
            else:
                if plan["route"] != "SCANNED" or index != 0:
                    raise DocumentExtractionError("预取 OCR 仅支持扫描页一次全页请求")
                payload = prefetched
            count += 1
            pages = PPOCRV5Extractor(self.config)._normalize_remote_payload(payload)
            if len(pages) != 1:
                raise DocumentExtractionError("每个 OCR crop 必须返回一页")
            result = pages[0]
            texts = result.get("rec_texts", [])
            polygons = result.get("rec_polys", result.get("dt_polys", []))
            scores = result.get("rec_scores", [None] * len(texts))
            if len(texts) != len(polygons):
                raise DocumentExtractionError("OCR 文字与坐标数量不一致")
            for li, (value, raw_polygon, score) in enumerate(zip(texts, polygons, scores, strict=True)):
                polygon = [transform_point([x + x0, y + y0], inverse) for x, y in raw_polygon]
                bbox = enclosing(polygon)
                conflict = [
                    char["index"]
                    for char in artifact["native_chars"]
                    if char["usable"]
                    and not char["char"].isspace()
                    and overlap(char["bbox"], bbox) / area(char["bbox"]) > 0.5
                ]
                if conflict:
                    artifact["errors"].append(
                        {
                            "type": "OCR_ROUTING_ERROR",
                            "request_id": request_id,
                            "reason": "ocr_line_intersects_native_after_masking",
                            "native_indices": conflict,
                            "text": value,
                        }
                    )
                    continue
                visual = owner(bbox, artifact["regions"])
                from app.services.extractors.contract_v3.printed_ownership import printed_text_owner
                visual = printed_text_owner(bbox, value, score, artifact["regions"], visual)
                evidence, missing = word_provenance(result, li, value, [x0, y0], inverse, request_id)
                artifact["blocks"].append(
                    {
                        "block_id": f"{request_id}:line:{li}",
                        "region_id": visual["region_id"] if visual else None,
                        "text": value,
                        "polygon": polygon,
                        "bbox": bbox,
                        "source": "PP-OCRv6",
                        "native_char_indices": [],
                        "raw_layout_type": visual["raw_layout_type"] if visual else None,
                        "resolved_block_type": visual["raw_layout_type"] if visual else "text",
                        "confidence": score,
                        "raw_ocr_score": score,
                        "raw_ocr_text": value,
                        "reason": "selective_ocr_text_with_v3_visual_role",
                        "ocr_request_id": request_id,
                        "ocr_char_provenance": evidence,
                        "evidence_missing_text_indices": missing,
                        "raw_ocr_polygon": raw_polygon,
                        "ocr_crop_origin": [x0, y0],
                    }
                )
        assign_order(artifact["blocks"], artifact["regions"])
        verify_conservation(artifact)
        return count
