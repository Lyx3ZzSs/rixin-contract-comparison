"""Exercise real create/queue/pipeline/query/review/export using cached OCR only.

Creates an isolated repository, never overwrites source tasks. --serve keeps the
local, auth-disabled verification UI on loopback; do not use as a deployment app.
"""

from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import time

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-task", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient
    from app.config import Settings, settings
    from app.infrastructure.task_repository import SQLiteTaskRepository
    from app.infrastructure.task_runner import QueuedTaskRunner
    from app.infrastructure.artifact_store import ArtifactStore
    from app.application import compare_tasks
    from app.services.compare_service import CompareService
    from scripts.replay_candidate_golden_v2 import _CachedCandidateExtractor
    from app.auth.models import CurrentUser, AGENT_ADMIN
    from app.auth.dependencies import get_current_user
    from app import api, api_events, api_textin

    settings.matching.enable_semantic_match = False
    settings.matching.enable_rerank = False
    settings.signing_visual_backend = "opencv"
    runtime = Settings(storage_dir=output / "runtime")
    repository = SQLiteTaskRepository(runtime)
    store = ArtifactStore(runtime)
    runner = QueuedTaskRunner(repository=repository, app_settings=runtime, max_workers=1)
    application = compare_tasks.CompareTaskApplication(repository=repository, runner=runner, artifact_store=store)
    source = BACKEND.parent / "storage" / "tasks" / args.source_task
    raw = {
        json.loads(p.read_text())["document"]["filename"]: p
        for p in (source / "diagnostics" / "ocr").glob("*_ppstructure_ocr_hybrid_raw.json")
    }
    extractor = _CachedCandidateExtractor(raw)

    def cached_service(**kwargs):
        service = CompareService(extractor=extractor, structured_extractor=extractor, **kwargs)
        pipeline = service._build_pipeline()
        for stage in pipeline.stages:
            if hasattr(stage, "field_value_ocr"):
                for method in ("enrich", "enrich_occluded_fields", "recover_missing_fields", "enrich_disputed_dates"):
                    setattr(stage.field_value_ocr, method, lambda *a, **k: [])
        service._build_pipeline = lambda: pipeline
        return service

    compare_tasks.CompareService = cached_service
    for module in (api, api_events, api_textin):
        module.default_compare_task_application = application
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(api_events.router)
    app.include_router(api_textin.router)
    user = CurrentUser(sub="alignment-verification", roles=frozenset({AGENT_ADMIN}))
    app.dependency_overrides[get_current_user] = lambda: user
    # API PDF access still validates paths against the isolated root.
    api.assert_path_inside_storage = lambda p: Path(p).resolve().relative_to(runtime.storage_dir.resolve())
    client = TestClient(app, base_url="http://127.0.0.1:8776")
    docs = [next((source / "input" / side).glob("*.pdf")) for side in ("original", "compare")]
    body = {
        "standard_doc": [{"filename": docs[0].name, "filedata": base64.b64encode(docs[0].read_bytes()).decode()}],
        "compare_doc": [{"filename": docs[1].name, "filedata": base64.b64encode(docs[1].read_bytes()).decode()}],
        "token_mode": 0,
        "convert_arg": {"merge_diff": 1},
        "config": {"use_pdf_parser": "true", "remove_watermark": "false"},
    }
    started = time.monotonic()
    created = client.post("/api/contracts/v3/comparison/external/create", json=body)
    assert created.status_code == 200, created.text
    task_id = created.json()["data"]["task_id"]
    print("CREATED", task_id, flush=True)
    (output / "create-response.json").write_text(json.dumps(created.json(), ensure_ascii=False, indent=2))
    del body
    prefix = "/api/contracts/v3/comparison/external/diff_info"
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        response = client.get(prefix, params={"task_id": task_id, "with_diff_result": "true"})
        status = response.json()["data"]["status"]
        if status != 2:
            break
        time.sleep(0.2)
    assert status == 1, response.text
    events = client.get(f"/api/compare/{task_id}/events").json()
    assert events["snapshot_persisted"] and events["event_count"] == 10 and events["raw_diff_count"] == 36
    group = next(e for e in events["events"] if len(e["children"]) == 31)
    assert sorted({b["page_no"] for b in group["diff"]["compare_evidence"]}) == [15, 16, 17, 18]
    assert len([e for e in events["events"] if e["diff"]["source_type"] == "seal"]) == 2
    assert len(set(i for e in events["events"] for i in e["raw_diff_ids"])) == 36
    assert response.json()["data"]["result"][0]["diff_info"]["total"] == 10
    # Real PDF endpoints and report rendering, not only serializer snapshots.
    for side in ("original", "compare"):
        pdf = client.get(f"/api/compare/{task_id}/{side}")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    report = client.get(f"/api/compare/{task_id}/events/report")
    assert report.status_code == 200 and report.content.startswith(b"%PDF-")
    (output / "events-report.pdf").write_bytes(report.content)
    review = client.put(
        f"/api/compare/{task_id}/events/{group['event_id']}/review",
        json={"revision": events["revision"], "review_status": "CONFIRMED"},
    )
    assert review.status_code == 200, review.text
    assert (
        next(e for e in review.json()["events"] if e["event_id"] == group["event_id"])["diff"]["review_status"]
        == "CONFIRMED"
    )
    stale = client.put(
        f"/api/compare/{task_id}/events/{group['event_id']}/review",
        json={"revision": events["revision"], "review_status": "FALSE_POSITIVE"},
    )
    assert stale.status_code == 409
    # Restore the verification-only review so the UI presents original conclusions.
    restored = client.put(
        f"/api/compare/{task_id}/events/{group['event_id']}/review",
        json={"revision": review.json()["revision"], "review_status": "UNREVIEWED"},
    )
    assert restored.status_code == 200
    events = restored.json()
    watermark_events = [e for e in events["events"] if e.get("evidence_selection")]
    assert len(watermark_events) == 5
    chosen = watermark_events[0]["event_id"]
    selected = client.put(
        f"/api/compare/{task_id}/events/{chosen}/review",
        json={"revision": events["revision"], "review_status": "CONFIRMED"},
    )
    assert selected.status_code == 200
    assert sum(e["diff"]["review_status"] == "CONFIRMED" for e in selected.json()["events"]) == 1
    reset = client.put(
        f"/api/compare/{task_id}/events/{chosen}/review",
        json={"revision": selected.json()["revision"], "review_status": "UNREVIEWED"},
    )
    assert reset.status_code == 200
    events = reset.json()
    detail = client.get(f"/api/compare/{task_id}").json()
    records = client.get("/api/compare/records").json()
    assert detail["event_count"] == 10, detail
    assert next(r for r in records["records"] if r["task_id"] == task_id)["event_count"] == 10, records
    assert client.get(f"/api/compare/{task_id}/events/export").json()["event_count"] == 10
    (output / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2))
    (output / "textin-compatible-result.json").write_text(json.dumps(response.json(), ensure_ascii=False, indent=2))
    task = repository.load_compare_task(task_id)
    (output / "task.json").write_text(task.model_dump_json(indent=2))
    (output / "verification.json").write_text(
        json.dumps(
            {
                "source_task_id": args.source_task,
                "task_id": task_id,
                "execution_status": task.execution_status,
                "duration_seconds": round(time.monotonic() - started, 2),
                "checks": [
                    "real_create",
                    "queued_runner_completed",
                    "cached_ocr_pipeline",
                    "persisted_snapshot",
                    "36_raw_10_events",
                    "31_children_pages_15_to_18",
                    "independent_stamps",
                    "raw_id_conservation",
                    "compatible_count",
                    "pdf_endpoints",
                    "event_pdf_report",
                    "atomic_group_review",
                    "stale_review_rejected",
                    "isolated_watermark_review",
                    "task_record_export_counts",
                ],
                "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in docs},
            },
            indent=2,
        )
    )
    print("VERIFIED", task_id, "36 raw / 10 events", flush=True)
    runner.stop()
    if args.serve:
        dist = BACKEND.parent / "frontend" / "dist"
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def index(path: str):
            return FileResponse(dist / "index.html")

        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8776, log_level="warning")
    repository.close()


if __name__ == "__main__":
    main()
