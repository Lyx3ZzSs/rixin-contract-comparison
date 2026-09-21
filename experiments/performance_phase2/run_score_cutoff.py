"""Isolated pre-model scoring differential on the frozen B extraction.

Providers are disabled identically on both sides ONLY to isolate this component.
This does not replace the required live pipeline/Golden gate.
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import settings
from app.models import Document
from app.services.document_understanding import DocumentUnderstandingService
from app.services.document_preparation import DocumentPreparer
from app.services.native_heading_repair import NativeHeadingRepairService
from app.services.repeated_overlay_filter import RepeatedOverlayFilter
from app.services.clause_splitter import ClauseSplitter
from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus
from app.services.matcher import ClauseMatcher
from score_cutoff import ExactCutoffScoring

source = ROOT / 'output/analysis/e2e-20260921/profile-B/runtime/tasks/profile-B/diagnostics'
out = ROOT / 'output/analysis/perf-phase2-20260921/scoring-configured'
out.mkdir(parents=True, exist_ok=True)
docs = []
for side in ['original', 'compare']:
    doc = Document.model_validate(json.loads((source / f'ocr/B-{side}_ppstructure_ocr_hybrid_raw.json').read_text())['document'])
    NativeHeadingRepairService().repair(doc)
    RepeatedOverlayFilter().apply(doc)
    docs.append(doc)
DocumentUnderstandingService(settings.document_understanding).understand_pair(*docs)
DocumentPreparer(image_analysis_max_inflight=settings.compare_image_analysis_max_inflight).prepare_pair(*docs)
clauses = [ClauseSplitter().split(doc, side) for doc, side in zip(docs, ['O', 'N'])]
mapping = json.loads((source / 'debug/page_alignment.json').read_text())
alignment = PageAlignmentResult(tuple(PageAlignmentEntry(
    status=PageAlignmentStatus(e['status']), original_pages=tuple(e['original_pages']),
    compare_pages=tuple(e['compare_pages']), alignment_score=e['alignment_score'],
    anchors=tuple(e['anchors']), reason=e['reason']) for e in mapping['entries']))

class CaptureMatcher(ClauseMatcher):
    def _apply_rerank(self, candidates):
        self.ordering = [(c.original.clause_id, c.compare.clause_id, c.score, c.method, c.details) for c in candidates]
        return super()._apply_rerank(candidates)

class CutoffMatcher(ExactCutoffScoring, CaptureMatcher):
    pass

rows = []
for repeat in range(1, 4):
    expected = None
    for variant, cls in [('baseline', CaptureMatcher), ('candidate', CutoffMatcher)]:
        matcher = cls(threshold=settings.match_threshold, assignment_strategy=settings.matching.assignment_strategy,
                      use_prefilter=settings.matching.use_prefilter,
                      performance_strategy=settings.match_performance_strategy,
                      feature_cache_enabled=settings.match_feature_cache_enabled)
        start, cpu = time.perf_counter(), time.process_time()
        pairs = matcher.match(*clauses, page_alignment=alignment)
        seconds, cpu_seconds = time.perf_counter() - start, time.process_time() - cpu
        actual = {'ordering': matcher.ordering, 'pairs': [p.model_dump(mode='json') for p in pairs]}
        if expected is None:
            expected = actual
        assert actual == expected, 'REVERT: candidate order, raw score or selected pairing changed'
        row = dict(variant=variant, repeat=repeat, seconds=seconds, cpu_seconds=cpu_seconds,
                   clause_counts=[len(c) for c in clauses], candidate_count=len(matcher.ordering),
                   exact_ordering_and_pairs=True, counters=dict(getattr(matcher, 'cutoff_counts', {})))
        rows.append(row)
        (out / 'B-stage.json').write_text(json.dumps(rows, indent=2))
        print(row, flush=True)
        if repeat == 1:
            (out / f'B-{variant}-result.json').write_text(json.dumps(actual, ensure_ascii=False))
