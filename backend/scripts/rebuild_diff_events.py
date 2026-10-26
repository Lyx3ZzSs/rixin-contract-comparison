"""Explicit historical event rebuild into a new JSON artifact, never into the DB."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import CompareTask
from app.services.diff_events import build_snapshot

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-json", required=True, type=Path)
    p.add_argument("--debug-dir", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        p.error("Output already exists; choose a new artifact path.")
    task = CompareTask.model_validate_json(args.task_json.read_text())
    clauses = {s: json.loads((args.debug_dir / f"clauses_{s}.json").read_text()) for s in ("original", "compare")}
    matches = json.loads((args.debug_dir / "clause_matches.json").read_text())
    ids = {s: {c["clause_id"] for c in clauses[s]} for s in clauses}
    for d in task.diffs:
        for s in ids:
            cid = getattr(d, f"{s}_clause_id")
            if cid and cid not in ids[s]:
                p.error(f"Context does not contain clause {cid}")
    previous_snapshot = task.event_snapshot
    task.event_snapshot = build_snapshot(
        task,
        clauses=clauses,
        matches=matches,
        page_alignment=json.loads((args.debug_dir / "page_alignment.json").read_text())["entries"],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as f:
        f.write(task.model_dump_json(indent=2))
    mapping = {
        "source_task_json": str(args.task_json.resolve()),
        "previous_snapshot": previous_snapshot,
        "new_version": task.event_snapshot["version"],
        "event_mapping": [
            {
                "old_event_id": old["event_id"],
                "new_event_ids": [
                    new["event_id"]
                    for new in task.event_snapshot["events"]
                    if set(old["raw_diff_ids"]) & set(new["raw_diff_ids"])
                ],
            }
            for old in (previous_snapshot or {}).get("events", [])
        ],
        "rollback": "Use the unchanged source artifact; no database was modified.",
    }
    args.output.with_suffix(".migration.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
    print(f"Wrote {len(task.event_snapshot['events'])} events; source and review history preserved.")
