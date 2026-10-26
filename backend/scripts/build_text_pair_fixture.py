"""Build a realistic text/text contract pair with exactly known changes.

Takes a digital contract, rewrites three concrete facts in its text layer, and
writes the revised copy next to it. Comparing the pair must surface exactly those
three changes and nothing else - the ground truth the text/text OCR routing case
needs, which the golden corpus cannot provide (no golden pair is text/text).

    .venv/bin/python backend/scripts/build_text_pair_fixture.py \
        --source storage/golden-intake/contract-golden-v3/HUANENG_DINGBIAN/original.pdf \
        --output output/analysis/ocr-routing-optimization-20260920/fixtures
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

# (old text in the text layer, replacement text)
EDITS = [
    ("¥70000.00", "¥80000.00"),
    ("人民币柒万元整", "人民币捌万元整"),
    ("15229399022", "15229399033"),
]
CJK_FONT = "china-s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / f"{args.source.stem}_revised.pdf"

    doc = fitz.open(args.source)
    applied = []
    for old, new in EDITS:
        rects = [rect for page in doc for rect in page.search_for(old)]
        for page in doc:
            for rect in page.search_for(old):
                page.add_redact_annot(rect, text=new, fontname=CJK_FONT, fontsize=9)
        applied.append({"old": old, "new": new, "rects": len(rects)})
    for page in doc:
        page.apply_redactions()
    doc.save(target)
    doc.close()

    with fitz.open(args.source) as before, fitz.open(target) as after:
        before_text = "".join(page.get_text() for page in before)
        after_text = "".join(page.get_text() for page in after)
        pages_before, pages_after = len(before), len(after)

    verification = [
        {
            **record,
            "old_left": after_text.count(record["old"]),
            "new_present": after_text.count(record["new"]),
            "old_before": before_text.count(record["old"]),
        }
        for record in applied
    ]
    report = {
        "source": str(args.source),
        "target": str(target),
        "pages_before": pages_before,
        "pages_after": pages_after,
        "edits": verification,
        "char_delta": len(after_text) - len(before_text),
        "expected_changes": [record["new"] for record in applied],
    }
    (args.output / "text_pair_fixture.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
