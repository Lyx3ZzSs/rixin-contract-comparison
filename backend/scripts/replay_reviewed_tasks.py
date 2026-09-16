"""Replay explicit local task IDs with cached OCR in an isolated output directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import app  # noqa: E402
from app.config import Settings, settings  # noqa: E402
from app.infrastructure.artifact_store import ArtifactStore  # noqa: E402
from app.infrastructure.task_repository import SQLiteTaskRepository  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402
from scripts.replay_candidate_golden_v2 import _CachedCandidateExtractor  # noqa: E402


class _ComponentReplayExtractor(_CachedCandidateExtractor):
    """Rebuild hybrid extraction from immutable component responses, without HTTP."""

    def extract(self, path, task_id=None):
        from app.services.extractors.ppocrv5 import PPOCRV5Extractor
        from app.services.extractors.ppstructure import PPStructureExtractor
        from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor

        result = super().extract(path, task_id)
        raw = self.raw_by_filename[Path(path).name]
        stem = raw.name.removesuffix('_ppstructure_ocr_hybrid_raw.json')
        ocr = PPOCRV5Extractor().payload_to_document(json.loads(raw.with_name(stem + '_ppocrv5_raw.json').read_text()), path)
        layout_extractor = PPStructureExtractor()
        layout = layout_extractor.payload_to_document(json.loads(raw.with_name(stem + '_ppstructure_raw.json').read_text()), path)
        hybrid = PPStructureOCRHybridExtractor()
        result.document = hybrid._merge_documents(ocr, layout)
        # The live extractor returns layout quality with the fused document.
        # Omitting it changes remediation flags and downstream noise filtering.
        structure_quality = layout_extractor.last_layout.quality if layout_extractor.last_layout is not None else None
        result.layout_quality = hybrid._build_layout_quality(result.document, structure_quality)
        return result


def replay(task_ids: list[str], output: Path, *, rebuild_hybrid: bool = False, field_ocr_cache: Path | None = None, capture_field_ocr: bool = False) -> None:
    if capture_field_ocr and field_ocr_cache is None:
        raise ValueError("--capture-field-ocr requires --field-ocr-cache")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = Settings(storage_dir=output / "runtime")
    settings.match_enable_semantic_match = False
    settings.match_enable_rerank = False
    # MatchingSettings is materialized at Settings construction; changing the
    # legacy flat properties alone does not disable the actual matchers.
    settings.matching.enable_semantic_match = False
    settings.matching.enable_rerank = False
    settings.signing_visual_backend = "opencv"
    code_root = Path(app.__file__).resolve().parent
    fingerprint = {
        "app/" + str(path.relative_to(code_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(code_root.rglob("*.py"))
    }
    (output / "code-hashes.json").write_text(json.dumps(fingerprint, indent=2))
    (output / "replay-settings.json").write_text(
        json.dumps(
            {
                "ocr": "cached_components_rebuilt_hybrid" if rebuild_hybrid else "cached_ppstructure_ocr_hybrid",
                "executed_code_root": str(code_root),
                "semantic_match": settings.matching.enable_semantic_match,
                "rerank": settings.matching.enable_rerank,
                "signing_visual_backend": "opencv",
                "field_ocr": ("cache_first_capture" if capture_field_ocr else "cached_by_input_sha256") if field_ocr_cache else "disabled_offline",
                "field_ocr_cache": str(field_ocr_cache) if field_ocr_cache else None,
                "settings_sha256": hashlib.sha256(settings.model_dump_json().encode()).hexdigest(),
            },
            indent=2,
        )
    )
    for task_id in task_ids:
        source = BACKEND.parent / "storage" / "tasks" / task_id
        raw = {}
        hashes = {}
        for path in (source / "diagnostics" / "ocr").glob("*_ppstructure_ocr_hybrid_raw.json"):
            payload = json.loads(path.read_text())
            raw[payload["document"]["filename"]] = path
            hashes[str(path.relative_to(BACKEND.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
        inputs = [next((source / "input" / side).glob("*.pdf")) for side in ("original", "compare")]
        for path in inputs:
            hashes[str(path.relative_to(BACKEND.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
        if rebuild_hybrid:
            for path in (source / "diagnostics" / "ocr").glob("*_raw.json"):
                hashes[str(path.relative_to(BACKEND.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
        extractor = (_ComponentReplayExtractor if rebuild_hybrid else _CachedCandidateExtractor)(raw)
        service = CompareService(
            extractor=extractor,
            structured_extractor=extractor,
            repository=SQLiteTaskRepository(runtime),
            artifact_store=ArtifactStore(runtime),
        )
        pipeline = service._build_pipeline()
        ocr_calls = []
        for stage in pipeline.stages:
            if hasattr(stage, "field_value_ocr"):
                if field_ocr_cache is None:
                    stage.field_value_ocr.enrich = lambda *_args, **_kwargs: []
                    stage.field_value_ocr.enrich_occluded_fields = lambda *_args, **_kwargs: []
                    stage.field_value_ocr.recover_missing_fields = lambda *_args, **_kwargs: []
                    stage.field_value_ocr.enrich_disputed_dates = lambda *_args, **_kwargs: []
            for attribute in ("field_value_ocr", "cover_party_ocr", "cover_date_ocr"):
                local_ocr = getattr(stage, attribute, None)
                if local_ocr is not None and field_ocr_cache is not None:
                    predict = local_ocr.extractor.predict
                    def cached_predict(path, file_type=0, predict=predict, **_kwargs):
                        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                        cache_path = field_ocr_cache / f"{digest}.json"
                        ocr_calls.append({"input_sha256": digest, "cache_hit": cache_path.exists()})
                        if not cache_path.exists():
                            if not capture_field_ocr:
                                raise RuntimeError(f"MissingCachedFieldOCR:{digest}")
                            response = predict(path, file_type=file_type, **_kwargs)
                            field_ocr_cache.mkdir(parents=True, exist_ok=True)
                            cache_path.write_text(json.dumps({"input_sha256": digest, "file_type": file_type, "response": response}, ensure_ascii=False))
                        payload = json.loads(cache_path.read_text())
                        if payload.get("input_sha256") != digest:
                            raise ValueError("FieldOCRCacheHashMismatch")
                        return payload["response"]
                    local_ocr.extractor.predict = cached_predict
            execute = stage.execute

            def capture(ctx, execute=execute, name=type(stage).__name__):
                execute(ctx)
                target = output / task_id
                target.mkdir(exist_ok=True)
                (target / f"{name}.json").write_text(
                    json.dumps([item.model_dump(mode="json") for item in ctx.diffs], ensure_ascii=False, indent=2)
                )

            stage.execute = capture
        service._build_pipeline = lambda: pipeline
        print(f"START {task_id}", flush=True)
        # Use the production queue lifecycle as well as the comparison stages.
        # CompareService alone intentionally returns a PROCESSING task.
        from app.models import CompareTask
        from app.infrastructure.task_runner import QueuedTaskRunner
        replay_id = f"replay-{task_id}"
        service.repository.save_compare_task(CompareTask(task_id=replay_id))
        runner = QueuedTaskRunner(repository=service.repository, app_settings=runtime, max_workers=1)
        runner.register_handler("compare", lambda context, payload: service.compare(
            *inputs, task_id=replay_id, execution_context=context))
        runner.submit(task_type="compare", task_id=replay_id, payload={})
        try:
            while True:
                task = service.repository.load_compare_task(replay_id)
                if task.status != "PROCESSING":
                    break
                time.sleep(.2)
        finally:
            runner.stop()
        (output / task_id / "result.json").write_text(task.model_dump_json(indent=2))
        (output / task_id / "input-hashes.json").write_text(json.dumps(hashes, indent=2))
        (output / task_id / "local-ocr-calls.json").write_text(json.dumps(ocr_calls, indent=2))
        print(f"DONE {task_id}: {task.status}, {len(task.diffs)} diffs", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rebuild-hybrid", action="store_true")
    parser.add_argument("--field-ocr-cache", type=Path, help="Offline local OCR responses keyed by PNG SHA-256")
    parser.add_argument("--capture-field-ocr", action="store_true", help="Call the configured local OCR service only for missing crop hashes")
    parser.add_argument("task_ids", nargs="+")
    args = parser.parse_args()
    replay(args.task_ids, args.output, rebuild_hybrid=args.rebuild_hybrid, field_ocr_cache=args.field_ocr_cache, capture_field_ocr=args.capture_field_ocr)
