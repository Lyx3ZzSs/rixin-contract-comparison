"""Build hash-bound bidirectional mapping for the adjudicated PDF reference pair."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/analysis/textin-v2"
RUN = OUT / "release-verification"
events = json.loads((RUN / "events.json").read_text())
adjudications = json.loads((OUT / "adjudications.json").read_text())
references = json.loads((OUT / "reference-items.json").read_text())


def relevant(e, source, page=None, title=None):
    d = e["diff"]
    return (
        d["source_type"] == source
        and (page is None or any(b["page_no"] == page for b in d["compare_evidence"]))
        and (title is None or title in d["title"])
    )


mapping = []
for item in adjudications:
    n = int(item["textin_id"][1:])
    matches = []
    for e in events["events"]:
        d = e["diff"]
        match = (
            n == 1
            and relevant(e, "metadata", 1, "封面")
            or n == 3
            and relevant(e, "metadata", 2, "水印")
            or n == 5
            and relevant(e, "metadata", 7, "水印")
            or n == 4
            and relevant(e, "graphic", 2)
            or n == 19
            and relevant(e, "clause", 15)
            or n == 20
            and relevant(e, "seal", 15)
            and "兰溪" in d["compare_text"]
            or n == 21
            and relevant(e, "seal", 15)
            and "国能" in d["compare_text"]
        )
        if match:
            matches.append(e["event_id"])
    mapping.append(
        {
            **item,
            "event_ids": matches,
            "relation": "covered"
            if matches
            else "non_change"
            if n == 17
            else "suppressed_ocr_difference"
            if item["category"] == "CONFIRMED_RECOGNITION_ERROR"
            else "unresolved_not_asserted_as_change",
        }
    )
matched = {id for m in mapping for id in m["event_ids"]}
extras = [
    {
        "event_id": e["event_id"],
        "pages": sorted({b["page_no"] for b in e["diff"]["compare_evidence"]}),
        "reason": "本地额外水印区域：原图可见比对侧文字；不为贴合 TextIn 条数而隐藏",
        "raw_diff_ids": e["raw_diff_ids"],
    }
    for e in events["events"]
    if e["event_id"] not in matched
]
(OUT / "bidirectional-mapping.json").write_text(
    json.dumps(
        {
            "input_sha256": references["input_sha256"],
            "textin_response_sha256": references["textin_response_sha256"],
            "task_id": events["task_id"],
            "event_version": events["event_version"] if "event_version" in events else events.get("version"),
            "textin_items": mapping,
            "local_extra_events": extras,
        },
        ensure_ascii=False,
        indent=2,
    )
)
labels = {
    "CONFIRMED_CHANGE": "确认变化",
    "CONFIRMED_RECOGNITION_ERROR": "识读/分块误报",
    "INSUFFICIENT_EVIDENCE": "证据不足",
    "NON_CHANGE_MATCH": "MATCH",
}
rows = ["| TextIn | 原图结论 | 本地关系 |", "|---|---|---|"]
for m in mapping:
    rows.append(f"| {m['textin_id']} | {labels[m['category']]} | {'、'.join(m['event_ids']) or m['relation']} |")
(OUT / "item-comparison.md").write_text(
    "# TextIn 24 项与 v2 双向对照\n\n保存响应 total=24，包含 1 项 MATCH；本地为 10 个变化事件。两者不以总数相等作为验收。\n\n"
    + "\n".join(rows)
    + "\n\n本地额外水印页：1、5、11。逐项判断理由、坐标、PDF 哈希和证据图片见同目录 JSON。历史 D 编号只保留在原裁定叙述中，本次映射使用当前事件 ID。\n"
)
print("Mapped", len(mapping), "TextIn items;", len(extras), "local extra events")
