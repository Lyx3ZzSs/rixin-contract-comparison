"""Replay frozen real table crops and controlled quarter turns; never edit Golden.

Known-angle transformations measure invariance, not unseen-contract accuracy.
The frozen OCR text is retained verbatim, so this does not measure OCR correction.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from app.config import Settings
from app.services.extractors.contract_v3.artifact import enclosing, rectangle, transform_point
from app.services.extractors.contract_v3.table_orientation import TableOrientation
from app.services.extractors.contract_v3.table_transform import orient_table_crop
from app.services.extractors.contract_v3.tables import TableResolver

ROOT = Path(__file__).resolve().parents[2]


def sample(region, angle):
    path = ROOT / region["crop_path"]
    image = Image.open(path).convert("RGB")
    w, h = image.size
    transform = dict(page_to_model=[1, 0, 0, 1, 0, 0], page_polygon=rectangle([0, 0, w, h]))
    pixels, transform = orient_table_crop(np.asarray(image)[:, :, ::-1], transform, angle)
    height, width = pixels.shape[:2]
    blocks = []
    for i, line in enumerate(region["ocr"]):
        box = line["bbox"]
        if not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
            continue
        polygon = [transform_point(p, transform["page_to_model"]) for p in rectangle(box)]
        blocks.append(
            dict(
                block_id=f"b{i}",
                region_id="t",
                source="PP-OCRv6",
                text=line["text"],
                bbox=enclosing(polygon),
                polygon=polygon,
                ocr_request_id="frozen",
                ocr_char_provenance=[
                    dict(char=c, text_index=j, bbox=enclosing(polygon)) for j, c in enumerate(line["text"])
                ],
            )
        )
    artifact = dict(
        native_chars=[],
        blocks=blocks,
        regions=[
            dict(
                region_id="t",
                raw_layout_type="table",
                bbox=[0, 0, width, height],
                polygon=rectangle([0, 0, width, height]),
            )
        ],
        coordinate_transform=dict(native_to_pixel=[1, 0, 0, 1, 0, 0]),
    )
    return artifact, Image.fromarray(pixels[:, :, ::-1])


def topology(table):
    return [(c["row"], c["column"], c["rowspan"], c["colspan"], c["text"]) for c in table["cells"]]


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "output/analysis/table-structure-integration-20261008/input-manifest.json",
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--structure", action="store_true")
    p.add_argument("--orientation-endpoint", default="http://127.0.0.1:8092")
    args = p.parse_args()
    if args.limit < 0:
        p.error("--limit must be nonnegative")
    regions = json.loads(args.manifest.read_text())["regions"]
    if args.limit:
        # Evenly distributed across the existing corpus, not selected by results.
        count = min(args.limit, len(regions))
        regions = [regions[round(i * (len(regions) - 1) / max(1, count - 1))] for i in range(count)]
    config = Settings(
        table_orientation_mode="enabled",
        table_orientation_endpoint=args.orientation_endpoint,
        table_orientation_document_budget=60,
    )
    probe = TableOrientation(config)
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for index, region in enumerate(regions):
        reference = None
        for angle in (0, 90, 270):
            artifact, image = sample(region, angle)
            probe.reset()
            started = time.perf_counter()
            decision = probe.analyze(
                np.asarray(image)[:, :, ::-1],
                dict(page_to_model=[1, 0, 0, 1, 0, 0]),
                artifact,
                artifact["regions"][0],
                [],
            )
            elapsed = time.perf_counter() - started
            row = dict(
                region_id=region["region_id"],
                crop_path=region["crop_path"],
                crop_sha256=hashlib.sha256((ROOT / region["crop_path"]).read_bytes()).hexdigest(),
                input_angle_ccw=angle,
                expected_angle_ccw=(360 - angle) % 360,
                decision=decision,
                seconds=elapsed,
            )
            if args.structure:
                baseline = deepcopy(artifact)
                TableResolver(config.model_copy(update={"table_orientation_mode": "off"})).resolve(
                    baseline, None, image
                )
                if angle == 0:
                    reference = topology(baseline["tables"][0]) or None

                class FrozenDecision:
                    def analyze(self, *args):
                        return decision

                TableResolver(config, orientation=FrozenDecision()).resolve(artifact, None, image)
                old, new = baseline["tables"][0], artifact["tables"][0]
                row.update(
                    baseline_status=old["status"],
                    candidate_status=new["status"],
                    baseline_matches_reference=topology(old) == reference if reference else None,
                    candidate_matches_reference=topology(new) == reference if reference else None,
                    adopted=new.get("model_diagnostics", {}).get("orientation", {}).get("adopted", False),
                    baseline_topology=topology(old),
                    candidate_topology=topology(new),
                    reference_topology=reference,
                    orientation=new.get("model_diagnostics", {}).get("orientation", {}),
                )
                assert [(b["text"], b["ocr_char_provenance"]) for b in artifact["blocks"]] == [
                    (b["text"], b["ocr_char_provenance"]) for b in baseline["blocks"]
                ]
            results.append(row)
            print(index, angle, decision["status"], decision["angle_ccw"], round(elapsed, 3), flush=True)
        summary = dict(
            samples=len(results),
            originals=sum(r["input_angle_ccw"] == 0 for r in results),
            original_rotations=sum(r["input_angle_ccw"] == 0 and r["decision"]["status"] == "ROTATE" for r in results),
            rotated_correct=sum(
                r["input_angle_ccw"] != 0
                and r["decision"]["status"] == "ROTATE"
                and r["decision"]["angle_ccw"] == r["expected_angle_ccw"]
                for r in results
            ),
            rotated_wrong=sum(
                r["input_angle_ccw"] != 0
                and r["decision"]["status"] == "ROTATE"
                and r["decision"]["angle_ccw"] != r["expected_angle_ccw"]
                for r in results
            ),
            failures=sum(r["decision"]["status"] == "FAILED" for r in results),
        )
        args.output.write_text(json.dumps(dict(summary=summary, results=results), ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
