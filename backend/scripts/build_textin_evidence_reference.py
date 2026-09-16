"""Build hash-bound evidence crops from an existing TextIn response; no network."""

import hashlib
import json
from pathlib import Path
import fitz

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "storage/tasks/2eddbd7a-f029-4af1-9c97-c637d937e75f/input"
OUT = ROOT / "output/analysis/textin-v2"
OUT.mkdir(parents=True, exist_ok=True)
response = ROOT / "output/analysis/textin-8eb0a225/textin-result.json"
data = json.loads(response.read_text())["data"]["result"][0]
files = {side: next((SOURCE / side).glob("*.pdf")) for side in ["original", "compare"]}
docs = {side: fitz.open(path) for side, path in files.items()}


def lines(value):
    if isinstance(value, dict):
        if "doc_index" in value and "page_index" in value:
            yield value
        else:
            for v in value.values():
                yield from lines(v)
    elif isinstance(value, list):
        for v in value:
            yield from lines(v)


references = []
for i, item in enumerate(data["diff_result"], 1):
    regions = {}
    for line in lines(item["diff"]):
        side = "original" if line["doc_index"] == 1 else "compare"
        page = line["page_index"]
        polygons = line.get("char_polygons") or [line.get("polygon")]
        for polygon in polygons:
            if not polygon or len(polygon) != 8:
                continue
            x, y = polygon[::2], polygon[1::2]
            if max(x) - min(x) < 2 or max(y) - min(y) < 2:
                continue
            page_info = data["pages"]["page_list1" if side == "original" else "page_list2"][page]
            rect = docs[side][page].rect
            region = fitz.Rect(
                min(x) * rect.width / page_info["page_width"],
                min(y) * rect.height / page_info["page_height"],
                max(x) * rect.width / page_info["page_width"],
                max(y) * rect.height / page_info["page_height"],
            )
            key = side, page
            regions[key] = regions[key] | region if key in regions else region
    crops = []
    for (side, page), rect in regions.items():
        rect = (rect + (-12, -12, 12, 12)) & docs[side][page].rect
        path = OUT / "evidence" / f"T{i:02d}-{side}-p{page + 1}.png"
        docs[side][page].get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect).save(path)
        crops.append(
            {
                "side": side,
                "page": page + 1,
                "bbox": list(rect),
                "unit": "display_pdf_point",
                "file": str(path.relative_to(ROOT)),
            }
        )
    references.append(
        {
            "id": f"T{i:02d}",
            "type": item["type"],
            "status": item["status"],
            "source_element_id": item["element_id"],
            "crops": crops,
        }
    )
(OUT / "reference-items.json").write_text(
    json.dumps(
        {
            "input_sha256": {s: hashlib.sha256(p.read_bytes()).hexdigest() for s, p in files.items()},
            "textin_response_sha256": hashlib.sha256(response.read_bytes()).hexdigest(),
            "items": references,
        },
        ensure_ascii=False,
        indent=2,
    )
)
previous = json.loads((ROOT / "output/analysis/textin-8eb0a225/reviewed-textin-items.json").read_text())
confirmed = {1, 3, 5, 19, 20, 21}
negative = {8, 9, 10, 22, 23, 24}
reason = {
    4: "比对第2页有明确矩阵码；原侧空白页及相邻页对应关系支持该区域新增。",
    6: "本次原图复核发现原侧也有淡色残影，不能定为新增；图形内容能否相同尚不能确认。",
    11: "原側淡色残影已定位，当前不能确认图形内容变化。",
    15: "本次原图复核发现原侧淡色残影，原来的新增定性过强；内容变化仍待确认。",
}
adjudications = []
for i, item in enumerate(previous, 1):
    category = (
        "CONFIRMED_CHANGE"
        if i in confirmed or i == 4
        else "CONFIRMED_RECOGNITION_ERROR"
        if i in negative
        else "NON_CHANGE_MATCH"
        if i == 17
        else "INSUFFICIENT_EVIDENCE"
    )
    adjudications.append(
        {
            "textin_id": f"T{i:02d}",
            "category": category,
            "assessment": reason.get(i, item["assessment"]),
            "basis": "本轮双侧原图与图形诊断"
            if i in reason
            else "此前已保存原图裁定，本轮补充可定位证据；不足以确定误报的项保持证据不足",
            "reference_id": f"T{i:02d}",
        }
    )
(OUT / "adjudications.json").write_text(json.dumps(adjudications, ensure_ascii=False, indent=2))
for d in docs.values():
    d.close()
print(f"{len(references)} reference items with evidence crops; adjudications saved.")
