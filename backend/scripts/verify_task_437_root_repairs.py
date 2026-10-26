"""Accept the reviewed task's root repairs against a completed isolated replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TASK_ID = "437aa215-d47e-4aaf-82e8-d32b1e6ad9f2"
ROOT = Path(__file__).resolve().parents[2]


def verify(replay: Path) -> dict:
    before = json.loads((ROOT / "output/analysis/textin-437aa215/local-task.json").read_text())
    after = json.loads((replay / TASK_ID / "result.json").read_text())
    source = {d["diff_id"]: d for d in before["diffs"]}
    diffs = after["diffs"]
    checks = []

    def check(name, passed, **evidence):
        checks.append({"check": name, "passed": bool(passed), **evidence})

    def pages(d, side):
        return {e["page_no"] for e in d[side+"_evidence"]}

    def signature(d):
        return (d["diff_type"], d["source_type"], d["original_text"], d["compare_text"],
                tuple(sorted(pages(d, "original"))), tuple(sorted(pages(d, "compare"))))

    check("queue_completed", after["status"] == "COMPLETED" and after["execution_status"] == "SUCCEEDED" and not after["errors"])
    false_ids = ["D011", "D015", "D016", "D040", "D044", "D048"]
    signatures = {signature(d) for d in diffs}
    for key in false_ids:
        check("false_positive_removed_"+key, signature(source[key]) not in signatures,
              original_text=source[key]["original_text"], compare_text=source[key]["compare_text"])
    # Check the affected channels and regions as well: splitting or renumbering
    # a former error is not evidence that it has disappeared.
    check("no_catalog_metadata_error", not any(d["source_type"] == "metadata" and 2 in pages(d,"original")|pages(d,"compare") for d in diffs))
    check("no_slash_seven_table_error", not any(d["source_type"] == "table" and d["original_text"].strip() == "7" and 17 in pages(d,"original") for d in diffs))
    check("no_continuation_four_table_error", not any(d["source_type"] == "table" and d["compare_text"].strip() == "4" and 28 in pages(d,"compare") for d in diffs))
    check("no_chipset_body_error", not any(d["source_type"] == "clause" and "芯片组" in d["original_text"]+d["compare_text"] for d in diffs))
    check("no_cover_body_error", not any(d["source_type"] == "clause" and 1 in pages(d,"original")|pages(d,"compare") for d in diffs))
    corrected = {"D023": "设计方：中誉设计有限公司", "D032": "李乐熠"}
    for key, expected in corrected.items():
        old = source[key]
        found = [d for d in diffs if d["source_type"] == old["source_type"] and d["diff_type"] == old["diff_type"]
                 and d["compare_text"] == expected and pages(d,"compare") == pages(old,"compare")]
        check("corrected_content_"+key, len(found) == 1 and found[0]["content_recognition_status"] == "CONFIRMED",
              before=old["compare_text"], after=expected, diff_ids=[d["diff_id"] for d in found])
        check("no_wrong_visible_content_"+key, not any(d["compare_text"] == old["compare_text"] for d in diffs))
    for page in (1,54):
        found = [d for d in diffs if d["match_score_details"].get("object_type") == "COMPANY_ROUND_SEAL" and page in pages(d,"compare")]
        check(f"designer_seal_page_{page}", len(found) == 1 and found[0]["match_score_details"].get("party_role") == "设计方")
    # Every other historical raw change must survive with the same text and
    # page evidence. Party titles may change, but the seal object must remain.
    for old in before["diffs"]:
        if old["diff_id"] in false_ids or old["diff_id"] in corrected:
            continue
        check("retained_change_"+old["diff_id"], signature(old) in signatures)
    snap = after["event_snapshot"]
    linked = [key for event in snap["events"] for key in event["raw_diff_ids"]]
    check("event_snapshot_covers_all_raw_changes", sorted(linked) == sorted(d["diff_id"] for d in diffs))
    check("counting_contract_unchanged", before["event_snapshot"]["version"] == snap["version"] == "textin-events-v3")
    calls = json.loads((replay / TASK_ID / "local-ocr-calls.json").read_text())
    check("all_local_ocr_responses_reused", bool(calls) and all(call["cache_hit"] for call in calls), calls=len(calls))
    diagnostics = json.loads(next(replay.rglob("signing_region.json")).read_text())
    check("no_local_ocr_errors", not any(x.get("status") in {"CACHE_MISS","OCR_ERROR"} for x in diagnostics["targeted_value_ocr"]))
    hashes = json.loads((replay / TASK_ID / "input-hashes.json").read_text())
    check("input_hashes_unchanged", all(hashlib.sha256((ROOT/key).read_bytes()).hexdigest() == value for key,value in hashes.items()))
    code = json.loads((replay / "code-hashes.json").read_text())
    drift = [key for key,value in code.items() if hashlib.sha256((ROOT/'backend'/key).read_bytes()).hexdigest() != value]
    check("executed_code_matches_workspace", not drift, workspace_changes_after_replay=drift)
    return {"task_id":TASK_ID,"passed":all(c["passed"] for c in checks),
            "before":{"raw":len(before["diffs"]),"events":len(before["event_snapshot"]["events"])},
            "after":{"raw":len(diffs),"events":len(snap["events"])},"checks":checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.replay.resolve())
    (args.replay / "root-fix-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"passed":result["passed"],"before":result["before"],"after":result["after"],
                      "checks":len(result["checks"]),"failed":[c['check'] for c in result['checks'] if not c['passed']]}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
