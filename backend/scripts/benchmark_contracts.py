"""Optional deterministic snapshots for performance experiments; no production hooks."""
from __future__ import annotations

from dataclasses import fields
import copy
import hashlib
import json
from pathlib import Path


def digest_payload(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def install_capture(pipeline, target: Path, deep: bool = False):
    """Capture actual candidate/table outputs, preserving order and all score fields."""
    records = {"match_tables": [], "align_rows": [], "clause_candidates": []} if deep else {}
    for stage in pipeline.stages:
        name = type(stage).__name__
        if name == "MatchStage":
            original = stage.matcher._build_candidates

            def candidates(*args, original=original, **kwargs):
                result = original(*args, **kwargs)
                records["clause_candidates"] = [
                    {f.name: (getattr(c, f.name).clause_id if f.name in {"original", "compare"}
                              else getattr(c, f.name)) for f in fields(c)} for c in result]
                return result

            stage.matcher._build_candidates = candidates
        if name == "PreClauseDiffStage":
            matcher = stage.table_comparator._matcher
            for method_name in ("match_tables", "align_rows"):
                original = getattr(matcher, method_name)

                def tables(*args, original=original, method_name=method_name, **kwargs):
                    result = original(*args, **kwargs)
                    records.setdefault(method_name, []).append(result)
                    return result

                setattr(matcher, method_name, tables)
        original = stage.execute

        def execute(ctx, original=original, name=name):
            original(ctx)
            if name == "PageAlignmentStage":
                records["page_alignment"] = ctx.page_alignment.to_debug_payload()
            if name == "MatchStage":
                records["clause_pairs"] = [p.model_dump(mode="json") for p in ctx.pairs]
            if deep:
                if name == "ClauseDiffStage":
                    records["after_clause_diff"] = copy.deepcopy([d.model_dump(mode="json") for d in ctx.diffs])
                elif name == "EvidenceStage":
                    records["after_evidence"] = copy.deepcopy([d.model_dump(mode="json") for d in ctx.diffs])
                elif name == "DiffQualityStage":
                    records["after_diff_quality"] = {
                        "diffs": copy.deepcopy([d.model_dump(mode="json") for d in ctx.diffs]),
                        "ocr_quality_summary": ctx.task.ocr_quality_summary.model_dump(mode="json") if ctx.task.ocr_quality_summary is not None else None,
                        "ocr_remediation_summary": ctx.task.ocr_remediation_summary.model_dump(mode="json") if ctx.task.ocr_remediation_summary is not None else None,
                    }
                elif name == "SigningRegionStage":
                    records["after_signing_region"] = {
                        "debug": copy.deepcopy(ctx.signing_region_debug),
                        "original_regions": [r.model_dump(mode="json") for r in ctx.signing_regions_original],
                        "compare_regions": [r.model_dump(mode="json") for r in ctx.signing_regions_compare],
                        "outlines": ctx.task.signing_region_outlines.model_dump(mode="json"),
                        "diffs": [d.model_dump(mode="json") for d in ctx.signing_region_diffs],
                    }
            if name in {"SummaryStage", "VisualizationStage"}:
                # Existing offline Golden validator requires completed stage artifacts.
                (target / f"{name}.json").write_text(json.dumps({"complete": True}))

        stage.execute = execute
    return records


def save_capture(target: Path, records):
    (target / "computation-snapshot.json").write_text(json.dumps(records, ensure_ascii=False))
    (target / "computation-hashes.json").write_text(json.dumps(
        {key: digest_payload(value) for key, value in records.items()}, indent=2))


class LocalCallCounter:
    """Benchmark-only counts of actual local PyMuPDF calls, across worker threads."""

    def __init__(self):
        from collections import Counter
        import threading
        self.counts = Counter()
        self.lock = threading.Lock()
        self.originals = {}

    def __enter__(self):
        import fitz
        for name in ("get_pixmap", "get_text"):
            original = getattr(fitz.Page, name)
            self.originals[name] = original
            def call(page, *args, original=original, name=name, **kwargs):
                with self.lock:
                    self.counts[name] += 1
                return original(page, *args, **kwargs)
            setattr(fitz.Page, name, call)
        return self

    def __exit__(self, *_):
        import fitz
        for name, original in self.originals.items():
            setattr(fitz.Page, name, original)
