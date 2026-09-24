"""Replay frozen table provider outputs through Resolver and three business compares.

All model, OCR, and crop boundaries are supplied by saved artifacts. This script
does not open the source PDFs or call an extraction model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
# The frozen bundle has no embedding/rerank responses; prevent live requests
# before the process-wide settings object is constructed.
os.environ["MATCH_ENABLE_SEMANTIC_MATCH"] = "false"
os.environ["MATCH_ENABLE_RERANK"] = "false"

from app.config import Settings  # noqa: E402
from app.infrastructure.artifact_store import ArtifactStore  # noqa: E402
from app.infrastructure.task_repository import SQLiteTaskRepository  # noqa: E402
from app.models import Document  # noqa: E402
from app.services.audit_summary import build_audit_stats  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402
from app.services.diff_events import event_response  # noqa: E402
from app.services.extractors.base import ExtractionResult  # noqa: E402
from tests.golden_contract.golden_loader import load_corpus  # noqa: E402
from tests.test_table_structure_acceptance_frozen import (  # noqa: E402
    PAGE_ARTIFACTS, replay_graph, replay_vector,
)

KEYS = ("RETURN_CONFIRMATION", "KAILI_FOUR_PROJECTS", "NANJING_DINGYI_XUZHOU_BANGYU")
TASK_IDS = {"RETURN_CONFIRMATION": "ff24e3cb-92b7-427d-8be8-842d295f7af0",
            "KAILI_FOUR_PROJECTS": "abdb9c3b-6d44-4fa6-a61a-40d4b6cda578",
            "NANJING_DINGYI_XUZHOU_BANGYU": "593d2c7b-ffae-4822-bd6a-4c369f5c1909"}
FROZEN = ROOT / "output/analysis/scanned-table-structure-poc-20260924/after"
OUT = ROOT / "output/analysis/table-structure-safety-gate-20260924/acceptance-closure"


def _box(block):
    b = block.layout_bbox
    return (b.x0, b.y0, b.x1, b.y1) if b else None


def _same_box(left, right):
    return left is not None and all(abs(a - b) <= 1.0 for a, b in zip(left, right, strict=True))


def resolver_document(key: str, side: str):
    source = FROZEN / key / f"{side}-extraction.json"
    document = Document.model_validate_json(source.read_text())
    replacements = []
    for path in sorted(PAGE_ARTIFACTS.glob(f"{key}{'-compare' if side == 'compare' else ''}-p*.json")):
        artifact = json.loads(path.read_text())
        page_no = artifact["page_no"]
        for region in artifact["regions"]:
            if region["raw_layout_type"] != "table":
                continue
            suffix = int(region["region_id"].rsplit(":v3:", 1)[1])
            vector = next((t for t in artifact.get("tables", []) if t["region_id"] == region["region_id"]
                           and t["source"] == "PDF_vector_ruling"), None)
            if vector:
                table, resolver_page = replay_vector(key, page_no, suffix)
            else:
                table, resolver_page, _ = replay_graph(key, side, page_no, suffix)
            page = next(p for p in document.pages if p.page_no == page_no)
            positions = [i for i, b in enumerate(page.blocks) if b.block_type == "table"
                         and _same_box(_box(b), region["bbox"])]
            assert positions, f"No frozen document region for {region['region_id']}"
            assert all(_same_box(_box(page.blocks[i]), region["bbox"]) for i in positions)
            assert resolver_page.blocks, f"No Resolver block for {region['region_id']}"
            # Replace the entire table region. All published fields come from the
            # actual Resolver result; unrelated frozen document blocks stay intact.
            position = positions[0]
            for i in reversed(positions):
                del page.blocks[i]
            page.blocks[position:position] = resolver_page.blocks
            replacements.append({"region_id": region["region_id"], "source": table["source"],
                                 "structure_status": table["structure_status"],
                                 "validation": table["validation"], "removed_blocks": len(positions),
                                 "inserted_blocks": len(resolver_page.blocks)})
    assert len(replacements) == {"RETURN_CONFIRMATION": {"original": 6, "compare": 7},
                                 "KAILI_FOUR_PROJECTS": {"original": 3, "compare": 3},
                                 "NANJING_DINGYI_XUZHOU_BANGYU": {"original": 3, "compare": 3}}[key][side]
    # This is the exact serialization boundary consumed by the business service.
    return Document.model_validate_json(document.model_dump_json()), replacements


class ResolverFrozenExtractor:
    name = "contract_extraction_v3"

    def __init__(self, key):
        self.key = key
        self.trace = {}

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        side = Path(path).stem
        assert side in {"original", "compare"}
        document, trace = resolver_document(self.key, side)
        self.trace[side] = trace
        performance = json.loads((FROZEN / self.key / f"{side}-performance.json").read_text())
        return ExtractionResult(document=document, extractor_used=self.name, performance=performance)


def main():
    corpus = {c.contract_key: c for c in load_corpus(ROOT / "storage/golden-intake/contract-golden-v3")}
    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    assertions = {}
    for key in KEYS:
        contract = corpus[key]
        extractor = ResolverFrozenExtractor(key)
        # Frozen extraction has no saved embedding/rerank responses. Keep the
        # replay offline and record this coverage limit in the closure report.
        settings = Settings(storage_dir=OUT / "runtime" / key,
                            match_enable_semantic_match=False, match_enable_rerank=False)
        service = CompareService(extractor=extractor, repository=SQLiteTaskRepository(settings),
                                 artifact_store=ArtifactStore(settings))
        started = time.perf_counter()
        task = service.compare(contract.original_pdf, contract.compare_pdf, task_id=TASK_IDS[key],
                               original_filename=contract.original_filename, compare_filename=contract.compare_filename)
        elapsed = time.perf_counter() - started
        round_trip = type(task).model_validate_json(task.model_dump_json())
        (OUT / f"{key}.json").write_text(round_trip.model_dump_json(indent=2), encoding="utf-8")
        (OUT / f"{key}-resolver-trace.json").write_text(json.dumps(extractor.trace, ensure_ascii=False, indent=2))
        by_id = {d.diff_id: d for d in round_trip.diffs}
        check = {"json_round_trip": True, "errors": len(round_trip.errors),
                 "table_confirmed_add_delete": sum(d.diff_type in {"ADD", "DELETE"}
                                                   for d in round_trip.diffs if d.source_type == "table")}
        if key in {"RETURN_CONFIRMATION", "KAILI_FOUR_PROJECTS"}:
            candidates = ("D007", "D008", "D009") if key == "RETURN_CONFIRMATION" else ("D006", "D007")
            for diff_id in candidates:
                diff = by_id[diff_id]
                assert diff.diff_type != "DELETE" and diff.quality_status == "NEEDS_REVIEW"
                assert diff.match_score_details.get("decision_reason") == "TABLE_STRUCTURE_UNRESOLVED"
            candidate_date = "D007" if key == "RETURN_CONFIRMATION" else "D006"
            assert by_id[candidate_date].diff_type == "ADD"
            assert by_id[candidate_date].match_score_details.get("change_decision") == "NEEDS_REVIEW"
            changed_date = "D004" if key == "RETURN_CONFIRMATION" else "D002"
            assert by_id[changed_date].diff_type == "MODIFY"
            assert ("2026年4月2日" if key == "RETURN_CONFIRMATION" else "2026年4月21日") in by_id[changed_date].compare_text
            check.update({"false_delete_ids": list(candidates), "candidate_date_id": candidate_date,
                          "real_date_modify_id": changed_date})
        assert not round_trip.errors and check["table_confirmed_add_delete"] == 0
        stats = build_audit_stats(round_trip.diffs)
        events = event_response(round_trip)
        if key in {"RETURN_CONFIRMATION", "KAILI_FOUR_PROJECTS"}:
            projected = [event for event in events["events"] if candidate_date in event["raw_diff_ids"]]
            assert len(projected) == 1
            assert projected[0]["diff"]["quality_status"] == "NEEDS_REVIEW"
            assert projected[0]["diff"]["match_score_details"]["decision_reason"] == "TABLE_STRUCTURE_UNRESOLVED"
            check["event_candidate_direction"] = projected[0]["direction"]
            check["event_candidate_quality"] = projected[0]["diff"]["quality_status"]
        check.update({"audit_stats": vars(stats), "event_direction_counts": events["counts"],
                      "event_count": events["event_count"]})
        assertions[key] = check
        row = {"contract": key, "seconds": round(elapsed, 3), "diffs": len(round_trip.diffs),
               "errors": round_trip.errors, "table": [{"id": d.diff_id, "operation": d.diff_type,
               "reason": d.match_score_details.get("decision_reason"), "status": d.quality_status}
               for d in round_trip.diffs if d.source_type == "table"]}
        summary.append(row)
        print(key, row["seconds"], "diffs", row["diffs"], "errors", len(row["errors"]),
              "table", [(x["id"], x["operation"], x["reason"]) for x in row["table"]], flush=True)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (OUT / "assertions.json").write_text(json.dumps(assertions, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
