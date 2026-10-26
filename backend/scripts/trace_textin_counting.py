"""Trace the saved TextIn reference into a specified isolated local run.

Direct diagnostic links are distinguished from contextual records. No missing
candidate is invented and external adjudications do not change engine output.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.diff_events.counting import CONTRACT, count_changes  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run", type=Path, required=True)
args = parser.parse_args()
run = args.run.resolve()
events = json.loads((run / "events.json").read_text())
task = json.loads((run / "task.json").read_text())
source = ROOT / "output/analysis/textin-8eb0a225/textin-result.json"
external = json.loads(source.read_text())["data"]["result"][0]
oldroot = ROOT / "output/analysis/textin-v2"
oldmap = json.loads((oldroot / "bidirectional-mapping.json").read_text())
old_events = json.loads((oldroot / "release-verification/events.json").read_text())["events"]
debug = run / "runtime/tasks" / task["task_id"] / "diagnostics/debug"
signing = json.loads((debug / "signing_region.json").read_text())


def signature(e):
    d = e["diff"]
    return json.dumps(
        {k: d[k] for k in ["source_type", "original_text", "compare_text", "original_evidence", "compare_evidence"]},
        sort_keys=True,
    )


old_by_id = {e["event_id"]: e for e in old_events}
new_by_signature = {signature(e): e for e in events["events"]}
quality = json.loads((debug / "diff_quality.json").read_text())
tables = json.loads((debug / "table_repair.json").read_text())
clauses = json.loads((debug / "clauses_original.json").read_text())
clause_matches = json.loads((debug / "clause_matches.json").read_text())
rows = []
for prior in oldmap["textin_items"]:
    n = int(prior["textin_id"][1:])
    reference = external["diff_result"][n - 1]
    current = [
        new_by_signature[signature(old_by_id[eid])]
        for eid in prior["event_ids"]
        if signature(old_by_id[eid]) in new_by_signature
    ]
    row = {
        **prior,
        "event_ids": [e["event_id"] for e in current],
        "raw_diff_ids": sorted({rid for e in current for rid in e["raw_diff_ids"]}),
        "textin_status": reference["status"],
        "textin_type": reference["type"],
        "diagnostics": [],
    }
    if current:
        row.update(stage="FINAL_CHANGE_UNIT", reason="已输出，按当前真实内容与几何映射，不依赖历史 D 编号")
    elif n == 17:
        row.update(stage="MATCH_EXCLUDED_FROM_COUNT", reason="相同印章，不属于变化")
    elif n in [6, 11, 15]:
        page = {6: 7, 11: 8, 15: 11}[n]
        row.update(stage="GRAPHIC_OBSERVATION_UNRESOLVED", reason="已定位双侧区域；残影或多页对应不足以确定新增/修改")
        row["diagnostics"] = [
            {"path": "task.json#/metrics/graphic_observations", "record": o, "causal": True}
            for o in task["metrics"]["graphic_observations"]
            if o.get("side") == "compare" and o.get("page") == page
        ]
    elif n in [2, 12]:
        page = {2: 1, 12: 11}[n]
        row.update(stage="VISUAL_EQUIVALENCE", reason="可见区域配准后被判为等价；不等于证明被遮挡部分相同")
        row["diagnostics"] = [
            {
                "path": str((debug / "signing_region.json").relative_to(run)) + "#/seal_equivalence",
                "record": v,
                "causal": True,
            }
            for v in signing["seal_equivalence"]
            if v.get("compare_page") == page
        ]
    elif n in [7, 8, 9, 10, 13, 14]:
        page = 8 if n in [7, 8, 9, 10] else 11
        relevant = [(i, c) for i, c in enumerate(signing["comparisons"]) if c["compare_region"]["page_no"] == page]
        row.update(
            stage="REGION_COMPARISON_CONTEXT",
            reason="区域已进入签章比较；以下为阶段记录，不能仅凭同页证明某一原始候选的抑制链路",
        )
        for i, c in relevant:
            row["diagnostics"].append(
                {
                    "path": str((debug / "signing_region.json").relative_to(run)) + f"#/comparisons/{i}",
                    "causal": False,
                    "record": {
                        k: c[k]
                        for k in ["comparison_id", "field_changes", "date_changes", "seal_changes", "signature_changes"]
                    },
                }
            )
    elif n == 18:
        candidates = [
            c for c in quality["candidates"] if "报价说明" in c["original_text"] and "报价说明" in c["compare_text"]
        ]
        row.update(
            stage="LOCATED_READING_CONSENSUS",
            reason="报价说明候选进入质量阶段，独立版面识读一致后被消除；与 TextIn 的具体识读不同",
        )
        for candidate in candidates:
            row["diagnostics"].append(
                {
                    "path": str((debug / "diff_quality.json").relative_to(run)),
                    "causal": True,
                    "record": {
                        "candidate": candidate,
                        "decisions": [d for d in quality["decisions"] if d["diff_id"] == candidate["diff_id"]],
                    },
                }
            )
    elif n in [22, 23, 24]:
        originals = [c for c in clauses if "超时每项扣乙方1000" in c["text_preview"]]
        matches = [m for m in clause_matches if m["original_clause_id"] in {c["clause_id"] for c in originals}]
        row.update(
            stage="MATCHED_CLAUSE_NO_CHANGE_CANDIDATE",
            reason="对应质量目标条款已配对，归一化正文相似度为 1；质量阶段未生成该条款变化候选",
        )
        row["diagnostics"] = [
            {"path": str((debug / "clause_matches.json").relative_to(run)), "causal": True, "record": m}
            for m in matches
        ]
        row["local_candidate_ids"] = [
            c["diff_id"]
            for c in quality["candidates"]
            if c.get("original_clause_id") in {m["original_clause_id"] for m in matches}
        ]
    elif n == 16:
        records = [
            d
            for d in tables["suppressed_diffs"]["suppressed_diffs"]
            if d["original_source_block_id"].startswith("p15_") and d["compare_source_block_id"].startswith("p13_")
        ]
        row.update(
            stage="TABLE_GEOMETRY_GATE",
            reason="报价表对应行因几何不可靠交由其他通道；未生成可唯一对应到 TextIn 金额的单元格候选，不将其认定为确定金额误报",
        )
        row["diagnostics"] = [
            {
                "path": str((debug / "table_repair.json").relative_to(run)) + "#/suppressed_diffs",
                "causal": False,
                "record": d,
            }
            for d in records
        ]
    else:
        row.update(
            stage="NO_EXACT_CANDIDATE_LINEAGE",
            reason="没有可唯一绑定到该 TextIn 项的本地候选记录；保留原图裁定，不捏造过滤原因",
        )
    rows.append(row)
covered = {eid for row in rows for eid in row["event_ids"]}
result = {
    "counting_contract": CONTRACT,
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "task_id": task["task_id"],
    "reference_counts": count_changes(external["diff_result"]),
    "provider_total_including_match": external["diff_info"]["total"],
    "local_counts": events["counts"],
    "items": rows,
    "local_extra_events": [e["event_id"] for e in events["events"] if e["event_id"] not in covered],
}
(run / "textin-trace.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
lines = [
    "# TextIn 计数与逐项去向",
    "",
    f"统计契约：{CONTRACT}。TextIn 23 个变化项，本地 {events['event_count']} 项。",
    "",
    "| TextIn | 类型/状态 | 当前差异项 | 本地阶段 |",
    "|---|---|---|---|",
]
for row in rows:
    lines.append(
        f"| {row['textin_id']} | {row['textin_type']} / {row['textin_status']} | {len(row['event_ids'])} | {row['stage']} |"
    )
lines += [
    "",
    "字段 causal=false 的记录仅为相关区域上下文，不是已查明的具体抑制原因。NO_EXACT_CANDIDATE_LINEAGE 为明确的追踪缺口，不等同于误报。",
    "",
    "本地额外项：" + ", ".join(result["local_extra_events"]),
]
(run / "textin-trace.md").write_text("\n".join(lines) + "\n")
print(result["reference_counts"], result["local_counts"])
