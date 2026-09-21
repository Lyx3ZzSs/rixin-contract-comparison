"""One bounded subprocess-run pipeline observation; not a browser E2E number."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("case")
parser.add_argument("output", type=Path)
args = parser.parse_args()
if os.environ.get("DIAG_OPENCV4_SHAPE"):
    raise RuntimeError("Phase 2 requires production OpenCV compatibility, not the diagnostic adapter")
args.output.mkdir(parents=True, exist_ok=True)
os.environ.update(DIAG_OUTPUT=str(args.output), STORAGE_DIR=str(args.output / "runtime"), AUTH_MODE="disabled")
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/scripts"), str(ROOT / "experiments/e2e_diagnosis")]
cases = json.loads((ROOT / "output/analysis/e2e-20260921/cases.json").read_text())
if args.case == "C-pathological":
    historical = ROOT / "output/analysis/e2e-20260921/runtime-C/tasks/42e90acf-021e-429d-a03f-e29ad79fa2ff/input"
    paths = [historical / side / f"C-{side}.pdf" for side in ("original", "compare")]
    for path in paths:
        assert path.read_bytes() == (ROOT / "output/analysis/e2e-20260921/cases" / path.name).read_bytes()
    case = {"paths": [str(p) for p in paths], "files": [{"sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
else:
    case = cases[args.case]
paths = [Path(p) for p in case["paths"]]
for path, info in zip(paths, case["files"]):
    assert hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"]
from probe import install
install()
from app.services.matching.candidate_scoring import CandidateScoringMixin
original_score_details = CandidateScoringMixin._score_details
original_partial = CandidateScoringMixin._partial_score
def count_score_details(self, *args, **kwargs):
    self._performance.increment("score_details_count_actual")
    return original_score_details(self, *args, **kwargs)
def count_partial(self, left, right):
    self._performance.increment("partial_score_baseline_impl_called")
    self._performance.increment("partial_chars_baseline_impl", len(left) + len(right))
    return original_partial(self, left, right)
CandidateScoringMixin._score_details = count_score_details
CandidateScoringMixin._partial_score = count_partial
assert sum(bool(os.getenv(key)) for key in ("PHASE2_PAGE_BOUND", "PHASE2_EXACT_CUTOFF", "PHASE2_CACHE_DIR")) <= 1, "One performance variable per experiment"
if os.getenv("PHASE2_EXACT_CUTOFF") == "1":
    from score_cutoff import ExactCutoffScoring
    import app.services.pipeline_stages as stages
    from app.services.matcher import ClauseMatcher
    class CutoffMatcher(ExactCutoffScoring, ClauseMatcher):
        def match(self, *args, **kwargs):
            result = super().match(*args, **kwargs)
            self.last_performance_metrics.setdefault("counters", {}).update(getattr(self, "cutoff_counts", {}))
            return result
    stages.ClauseMatcher = CutoffMatcher
if os.getenv("PHASE2_PAGE_BOUND") == "1":
    from anchor_bound import AnchorBoundAligner
    import app.services.pipeline_stages as stages
    stages.PageSequenceAligner = AnchorBoundAligner
cache_instances = []
if os.getenv("PHASE2_CACHE_DIR"):
    from extraction_cache import ExtractionCache
    import app.services.pipeline_stages as stages
    from app.config import settings
    factory = stages.build_compare_document_extractor
    config = settings.model_dump(mode="json")
    # Conservative superset: missing a selection/config key could create a false
    # hit. Runtime storage roots are excluded so independent tasks can reuse it.
    # Only the digest is stored; no settings or credentials are written to cache.
    relevant = {k:v for k,v in config.items() if k not in {"storage_dir", "base_dir"}}
    code_version = hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT/"backend/app").rglob("*.py")))).hexdigest()
    def cached_factory(*args, **kwargs):
        cached = ExtractionCache(factory(*args,**kwargs), Path(os.environ["PHASE2_CACHE_DIR"]),
            scope=os.environ["PHASE2_CACHE_SCOPE"],extractor_version=code_version,pipeline_version=code_version,
            ocr_model_version=os.environ["PHASE2_OCR_MODEL_VERSION"],layout_model_version=os.environ["PHASE2_LAYOUT_MODEL_VERSION"],relevant_config=relevant)
        cache_instances.append(cached)
        return cached
    stages.build_compare_document_extractor = cached_factory
from app.services.models.setup import register_default_models
from app.services.compare_service import CompareService
from app.services.pipeline import ComparePipeline
from app.services.diff_events import event_response
from app.clients import close_clients
register_default_models()
contexts = []
extraction_summary = {}
old_run = ComparePipeline.run
def capture(self, ctx):
    if os.getenv("PHASE2_CAPTURE_EXTRACTION") == "1":
        original_execute = self.stages[0].execute
        def extract(ctx):
            original_execute(ctx)
            for side in ("original", "compare"):
                result = getattr(ctx, side + "_extraction")
                doc = result.document
                payload = doc.model_dump(mode="json", exclude={"path", "filename"})
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                extraction_summary[side] = {"page_count": doc.page_count, "blocks": sum(len(p.blocks) for p in doc.pages), "char_boxes": sum(len(b.char_boxes) for p in doc.pages for b in p.blocks), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
                (args.output / (side + "-extraction.json")).write_text(encoded)
        self.stages[0].execute = extract
    result = old_run(self, ctx)
    contexts.append(ctx)
    return result
ComparePipeline.run = capture
started = time.perf_counter()
try:
    task = CompareService().compare(*paths, task_id="phase2-" + args.case)
    seconds = time.perf_counter() - started
    ctx = contexts[0]
    def dump(value):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [dump(v) for v in value]
        if hasattr(value, "__dataclass_fields__"):
            import dataclasses
            return dataclasses.asdict(value)
        return value
    payload = event_response(task)
    result = {"mode": "pipeline-only", "pipeline_seconds": seconds,
              "metrics": task.metrics, "events": dump(payload), "diffs": dump(task.diffs), "extraction": extraction_summary,
              "page_alignment": dump(ctx.page_alignment), "pairs": dump(ctx.pairs),
              "original_sections": dump(ctx.original_clauses), "compare_sections": dump(ctx.compare_clauses),
              "cache_hits": sum(c.hits for c in cache_instances), "cache_misses": sum(c.misses for c in cache_instances)}
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=dump))
    (args.output / "code-hashes.json").write_text(json.dumps({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for base in ["backend/app", "frontend/src", "experiments/performance_phase2"] for p in sorted((ROOT/base).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}))
    print(json.dumps({"case": args.case, "pipeline_seconds": seconds, "diff_count": len(task.diffs), "event_count": payload["event_count"]}))
finally:
    close_clients()
