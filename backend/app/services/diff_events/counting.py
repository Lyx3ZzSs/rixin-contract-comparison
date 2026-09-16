"""Count top-level change units, independently of children, review and UI groups."""

from collections import Counter

CONTRACT = "textin-top-level-changes-v1"
STATUSES = {"ADD": "insert", "INSERT": "insert", "DELETE": "delete", "MODIFY": "change", "CHANGE": "change"}


def count_changes(items, *, status_key="status"):
    counts = Counter()
    for item in items:
        status = item[status_key]
        if status == "MATCH":
            continue
        if status not in STATUSES:
            raise ValueError(f"Unsupported change status: {status}")
        counts[STATUSES[status]] += 1
    return {**{key: counts[key] for key in ("insert", "delete", "change")}, "total": sum(counts.values())}
