"""Strict semantic comparison and original-method timing aggregation."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics

ROOT = Path(__file__).resolve().parents[2]

def differences(left, right, path=""):
    if type(left) is not type(right):
        return [path + ":type"]
    if isinstance(left, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            result.extend([path + "/" + key + ":missing"] if key not in left or key not in right else differences(left[key], right[key], path + "/" + key))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return [path + ":length"]
        return [diff for i, (a, b) in enumerate(zip(left, right)) for diff in differences(a, b, f"{path}/{i}")]
    return [] if left == right else [path]

def semantic_task(details):
    # No business ID, score, coordinate, classification or diagnostic is dropped.
    keys = ["diffs", "event_snapshot", "event_reviews", "audit_item_reviews", "audit_item_reviews_normalized", "signing_region_outlines"]
    return {key: details.get(key) for key in keys}

def task_result(root, label, task_id):
    with sqlite3.connect(f"file:{root}/runtime-{label}/tasks.sqlite3?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT details_json FROM tasks WHERE task_id=? AND status='COMPLETED'", (task_id,)).fetchone()
    if row is None:
        raise AssertionError("Missing completed benchmark task")
    return semantic_task(json.loads(row[0]))

def aggregate(directory):
    source_path = ROOT / "experiments/e2e_diagnosis/analyze.py"
    source = source_path.read_text().replace("OUT=ROOT/'output/analysis/e2e-20260921'", "OUT=Path(" + repr(str(directory)) + ")")
    exec(compile(source, str(source_path), "exec"), {"__file__": str(source_path)})
    return json.loads((directory / "analysis.json").read_text())

def compare(directory):
    data = {variant: aggregate(directory / variant) for variant in ["baseline", "candidate"]}
    output = {}
    for label in "ABCDE":
        groups = {variant: [row for row in d["runs"] if row["run"].startswith(label)] for variant, d in data.items()}
        if not all(groups.values()):
            continue
        fields = {"E2E": lambda r: r["e2e_seconds"], "CPU": lambda r: r["cpu_seconds"], "RSS_MiB": lambda r: r["rss_peak_mb"], "remote_calls": lambda r: sum(e["count"] for e in r["http"].values()), "remote_union": lambda r: r["http_union_seconds"], "events_bytes": lambda r: r["frontend"]["events_bytes"], "event_count": lambda r: r["frontend"]["event_count"], "completed_to_events": lambda r: r["frontend"]["completion_to_events_start_seconds"], "events_fetch": lambda r: r["frontend"]["events_fetch_seconds"]}
        fields.update({s: lambda r, s=s: r["stages"][s] for s in groups["baseline"][0]["stages"]})
        metrics = {}
        for name, get in fields.items():
            a, b = [statistics.median(get(r) for r in groups[v]) for v in ["baseline", "candidate"]]
            metrics[name] = {"baseline": a, "candidate": b, "absolute_delta": b - a, "percentage_delta": (b - a) / a * 100 if a else None}
        semantics = []
        for a, b in zip(groups["baseline"], groups["candidate"], strict=True):
            old = task_result(directory/"baseline", label, a["comparison_id"])
            new = task_result(directory/"candidate", label, b["comparison_id"])
            paths = differences(old, new)
            semantics.append({"run": a["run"], "equal": not paths, "difference_paths": paths, "baseline_diff_count": len(old["diffs"]), "candidate_diff_count": len(new["diffs"]), "baseline_sha256": hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest(), "candidate_sha256": hashlib.sha256(json.dumps(new,sort_keys=True).encode()).hexdigest()})
        output[label] = {"n": {k: len(v) for k, v in groups.items()}, "metrics_p50": metrics, "semantics": semantics}
    (directory / "comparison.json").write_text(json.dumps(output, indent=2, ensure_ascii=False))
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    compare(parser.parse_args().directory.resolve())
