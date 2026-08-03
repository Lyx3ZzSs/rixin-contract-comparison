from __future__ import annotations

import base64
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from app.config import Settings
from app.infrastructure.artifact_store import LocalArtifactStore
from app.models import BBox, Clause, CompareTask, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor
from app.services.matcher import ClauseMatcher
from app.services.matching.providers import RerankMatcher, SemanticMatcher
from app.services.matching.types import MatchCandidate
from app.services.pipeline import PipelineContext
from app.services.pipeline_metrics import PerformanceRecorder
from app.services.pipeline_stages import ExtractionStage
from scripts.benchmark_ocr_concurrency import run_concurrency_level
from scripts.summarize_compare_performance import summarize_tasks


def _document(filename: str = "test.pdf") -> Document:
    return Document(
        filename=filename,
        path=filename,
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id=f"{filename}-b1",
                        page_no=1,
                        text="第一条 付款条款",
                        bbox=BBox(x0=10, y0=10, x1=180, y1=30),
                    )
                ],
            )
        ],
    )


def _clause(clause_id: str, clause_no: str, text: str) -> Clause:
    return Clause(
        clause_id=clause_id,
        clause_no=clause_no,
        title=text,
        text=text,
        normalized_text=text,
        match_text=text,
    )


def test_performance_recorder_aggregates_operation_timings_and_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamps = iter([10.0, 11.0, 13.0, 15.0])
    monkeypatch.setattr("app.services.pipeline_metrics.time.perf_counter", lambda: next(timestamps))
    recorder = PerformanceRecorder()

    with recorder.measure("remote_call"):
        recorder.increment("request_count")
    snapshot = recorder.snapshot()

    assert snapshot["duration_seconds"] == 5.0
    assert snapshot["operations"]["remote_call"] == {
        "count": 1,
        "duration_seconds": 2.0,
        "max_duration_seconds": 2.0,
    }
    assert snapshot["counters"]["request_count"] == 1


def test_hybrid_extractor_exposes_component_and_merge_metrics() -> None:
    class StaticExtractor:
        def __init__(self, result: ExtractionResult) -> None:
            self.result = result

        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            return self.result

    ocr = ExtractionResult(
        document=_document("ocr.pdf"),
        extractor_used="ppocrv5",
        performance={"counters": {"http_request_count": 1}},
    )
    structure = ExtractionResult(
        document=_document("structure.pdf"),
        extractor_used="ppstructure",
        performance={"counters": {"http_request_count": 1}},
    )

    result = PPStructureOCRHybridExtractor(
        structure_extractor=StaticExtractor(structure),
        ocr_extractor=StaticExtractor(ocr),
    ).extract("contract.pdf")

    assert result.performance["components"]["ppocrv5"] == ocr.performance
    assert result.performance["components"]["ppstructure"] == structure.performance
    assert result.performance["operations"]["ppocrv5_component"]["count"] == 1
    assert result.performance["operations"]["ppstructure_component"]["count"] == 1
    assert result.performance["operations"]["document_merge"]["count"] == 1


def test_hybrid_extractor_runs_independent_components_in_parallel() -> None:
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    class DelayedExtractor:
        def __init__(self, result: ExtractionResult) -> None:
            self.result = result

        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                time.sleep(0.02)
                return self.result
            finally:
                with lock:
                    state["active"] -= 1

    ocr = ExtractionResult(document=_document("ocr.pdf"), extractor_used="ppocrv5")
    structure = ExtractionResult(document=_document("structure.pdf"), extractor_used="ppstructure")

    result = PPStructureOCRHybridExtractor(
        structure_extractor=DelayedExtractor(structure),
        ocr_extractor=DelayedExtractor(ocr),
        component_parallel_enabled=True,
    ).extract("contract.pdf")

    assert state["max_active"] == 2
    assert result.performance["counters"]["component_parallel_requested"] == 1
    assert result.performance["counters"]["component_parallel_used"] == 1
    assert result.performance["operations"]["component_extraction"]["count"] == 1


def test_hybrid_component_parallelism_preserves_serial_document_result() -> None:
    class StaticExtractor:
        def __init__(self, result: ExtractionResult) -> None:
            self.result = result

        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            return self.result

    def build(component_parallel_enabled: bool) -> ExtractionResult:
        return PPStructureOCRHybridExtractor(
            structure_extractor=StaticExtractor(
                ExtractionResult(
                    document=_document("structure.pdf"),
                    extractor_used="ppstructure",
                    warnings=["structure-warning"],
                )
            ),
            ocr_extractor=StaticExtractor(
                ExtractionResult(
                    document=_document("ocr.pdf"),
                    extractor_used="ppocrv5",
                    warnings=["ocr-warning"],
                )
            ),
            component_parallel_enabled=component_parallel_enabled,
        ).extract("contract.pdf")

    serial = build(False)
    parallel = build(True)

    assert parallel.document == serial.document
    assert parallel.extractor_used == serial.extractor_used
    assert parallel.warnings == serial.warnings


@pytest.mark.parametrize("component_parallel_enabled", [False, True])
def test_hybrid_components_reuse_one_encoded_pdf_payload(
    tmp_path: Path,
    component_parallel_enabled: bool,
) -> None:
    pdf_path = tmp_path / "contract.pdf"
    pdf_bytes = b"%PDF-shared-request-payload"
    pdf_path.write_bytes(pdf_bytes)
    received_payloads: list[str] = []
    lock = threading.Lock()

    class SharedPayloadExtractor:
        def __init__(self, result: ExtractionResult) -> None:
            self.result = result

        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            raise AssertionError("shared payload path should be used")

        def extract_with_encoded_file(
            self,
            _path: str | Path,
            *,
            encoded_file: str,
            task_id: str | None = None,
        ) -> ExtractionResult:
            with lock:
                received_payloads.append(encoded_file)
            return self.result

    result = PPStructureOCRHybridExtractor(
        structure_extractor=SharedPayloadExtractor(
            ExtractionResult(document=_document("structure.pdf"), extractor_used="ppstructure")
        ),
        ocr_extractor=SharedPayloadExtractor(ExtractionResult(document=_document("ocr.pdf"), extractor_used="ppocrv5")),
        component_parallel_enabled=component_parallel_enabled,
    ).extract(pdf_path)

    assert len(received_payloads) == 2
    assert received_payloads[0] is received_payloads[1]
    assert received_payloads[0] == base64.b64encode(pdf_bytes).decode("ascii")
    assert result.performance["counters"]["shared_request_payload_supported"] == 1
    assert result.performance["counters"]["shared_request_payload_used"] == 1
    assert result.performance["operations"]["shared_request_file_encode"]["count"] == 1


def test_hybrid_parallel_structure_failure_preserves_ocr_only_fallback() -> None:
    class StaticOCR:
        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            return ExtractionResult(document=_document("ocr.pdf"), extractor_used="ppocrv5")

    class FailingStructure:
        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            raise DocumentExtractionError("structure unavailable")

    result = PPStructureOCRHybridExtractor(
        structure_extractor=FailingStructure(),
        ocr_extractor=StaticOCR(),
        require_structure=False,
        component_parallel_enabled=True,
    ).extract("contract.pdf")

    assert result.extractor_used == "ppstructure_ocr_hybrid_ocr_only"
    assert "structure unavailable" in result.warnings[0]
    assert result.performance["counters"]["ppstructure_failure_count"] == 1


def test_hybrid_parallel_raises_ocr_error_before_structure_error() -> None:
    class FailingOCR:
        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            raise DocumentExtractionError("ocr unavailable")

    class FailingStructure:
        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            raise DocumentExtractionError("structure unavailable")

    extractor = PPStructureOCRHybridExtractor(
        structure_extractor=FailingStructure(),
        ocr_extractor=FailingOCR(),
        require_structure=True,
        component_parallel_enabled=True,
    )

    with pytest.raises(DocumentExtractionError, match="ocr unavailable"):
        extractor.extract("contract.pdf")


def test_extraction_stage_records_each_side_in_task_metrics(tmp_path: Path) -> None:
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.write_bytes(b"%PDF-original")
    compare_path.write_bytes(b"%PDF-compare")

    class SequentialExtractor:
        def __init__(self) -> None:
            self.results = iter(
                [
                    ExtractionResult(
                        document=_document("original.pdf"),
                        extractor_used="ppstructure_ocr_hybrid",
                        performance={"counters": {"http_request_count": 2}},
                    ),
                    ExtractionResult(
                        document=_document("compare.pdf"),
                        extractor_used="ppstructure_ocr_hybrid",
                        performance={"counters": {"http_request_count": 2}},
                    ),
                ]
            )

        def extract(self, _path: str | Path, task_id: str | None = None) -> ExtractionResult:
            return next(self.results)

    app_settings = Settings(storage_dir=tmp_path / "storage")
    task = CompareTask(
        task_id="TPERF",
        original_pdf_path=str(original_path),
        compare_pdf_path=str(compare_path),
    )
    ctx = PipelineContext(task=task, original_pdf=original_path, compare_pdf=compare_path)

    ExtractionStage(
        extractor=SequentialExtractor(),
        artifact_store=LocalArtifactStore(app_settings),
    ).execute(ctx)

    metrics = task.metrics["performance"]["extraction"]
    assert metrics["sides"]["original"]["status"] == "SUCCEEDED"
    assert metrics["sides"]["original"]["page_count"] == 1
    assert metrics["sides"]["original"]["details"]["counters"]["http_request_count"] == 2
    assert metrics["sides"]["compare"]["status"] == "SUCCEEDED"
    assert metrics["operations"]["native_heading_repair"]["count"] == 1


def test_extraction_stage_uses_independent_extractors_for_parallel_pair(tmp_path: Path) -> None:
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.write_bytes(b"%PDF-original")
    compare_path.write_bytes(b"%PDF-compare")
    state = {
        "active": 0,
        "max_active": 0,
        "created": 0,
        "used_instances": set(),
    }
    lock = threading.Lock()

    class ParallelExtractor:
        name = "ppstructure_ocr_hybrid"

        def __init__(self) -> None:
            with lock:
                state["created"] += 1
                self.instance_id = state["created"]

        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                state["used_instances"].add(self.instance_id)
            try:
                time.sleep(0.02)
                return ExtractionResult(
                    document=_document(Path(path).name),
                    extractor_used=self.name,
                )
            finally:
                with lock:
                    state["active"] -= 1

    app_settings = Settings(storage_dir=tmp_path / "storage")
    task = CompareTask(
        task_id="TPARALLEL",
        original_pdf_path=str(original_path),
        compare_pdf_path=str(compare_path),
    )
    ctx = PipelineContext(task=task, original_pdf=original_path, compare_pdf=compare_path)

    ExtractionStage(
        extractor_factory=ParallelExtractor,
        parallel_extraction_enabled=True,
        artifact_store=LocalArtifactStore(app_settings),
    ).execute(ctx)

    metrics = task.metrics["performance"]["extraction"]
    assert state["created"] == 2
    assert state["used_instances"] == {1, 2}
    assert state["max_active"] == 2
    assert metrics["mode"] == "parallel"
    assert metrics["counters"]["parallel_extraction_requested"] == 1
    assert metrics["counters"]["parallel_extraction_used"] == 1
    assert ctx.original_extraction is not None
    assert ctx.original_extraction.document.filename == "original.pdf"
    assert ctx.compare_extraction is not None
    assert ctx.compare_extraction.document.filename == "compare.pdf"


def test_extraction_stage_falls_back_to_serial_for_injected_extractor_without_factory(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.write_bytes(b"%PDF-original")
    compare_path.write_bytes(b"%PDF-compare")
    calls: list[str] = []

    class PathExtractor:
        name = "ppstructure_ocr_hybrid"

        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            calls.append(Path(path).name)
            return ExtractionResult(
                document=_document(Path(path).name),
                extractor_used=self.name,
            )

    app_settings = Settings(storage_dir=tmp_path / "storage")
    task = CompareTask(
        task_id="TSERIALFALLBACK",
        original_pdf_path=str(original_path),
        compare_pdf_path=str(compare_path),
    )
    ctx = PipelineContext(task=task, original_pdf=original_path, compare_pdf=compare_path)

    ExtractionStage(
        extractor=PathExtractor(),
        parallel_extraction_enabled=True,
        artifact_store=LocalArtifactStore(app_settings),
    ).execute(ctx)

    metrics = task.metrics["performance"]["extraction"]
    assert calls == ["original.pdf", "compare.pdf"]
    assert metrics["mode"] == "serial"
    assert metrics["parallel_fallback_reason"] == "extractor_factory_unavailable"
    assert metrics["counters"]["parallel_extraction_requested"] == 1
    assert metrics["counters"]["parallel_extraction_used"] == 0


def test_parallel_extraction_preserves_serial_document_results(tmp_path: Path) -> None:
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.write_bytes(b"%PDF-original")
    compare_path.write_bytes(b"%PDF-compare")

    class DeterministicExtractor:
        name = "ppstructure_ocr_hybrid"

        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            return ExtractionResult(
                document=_document(Path(path).name),
                extractor_used=self.name,
                warnings=["stable-warning"],
            )

    app_settings = Settings(storage_dir=tmp_path / "storage")

    def context(task_id: str) -> PipelineContext:
        return PipelineContext(
            task=CompareTask(
                task_id=task_id,
                original_pdf_path=str(original_path),
                compare_pdf_path=str(compare_path),
            ),
            original_pdf=original_path,
            compare_pdf=compare_path,
        )

    serial_ctx = context("TSERIALRESULT")
    ExtractionStage(
        extractor=DeterministicExtractor(),
        parallel_extraction_enabled=False,
        artifact_store=LocalArtifactStore(app_settings),
    ).execute(serial_ctx)
    parallel_ctx = context("TPARALLELRESULT")
    ExtractionStage(
        extractor_factory=DeterministicExtractor,
        parallel_extraction_enabled=True,
        artifact_store=LocalArtifactStore(app_settings),
    ).execute(parallel_ctx)

    assert serial_ctx.require_extractions().original.document == parallel_ctx.require_extractions().original.document
    assert serial_ctx.require_extractions().compare.document == parallel_ctx.require_extractions().compare.document
    assert serial_ctx.task.extractor_used == parallel_ctx.task.extractor_used
    assert serial_ctx.task.parse_warnings == parallel_ctx.task.parse_warnings


def test_clause_matcher_records_candidate_and_pair_metrics() -> None:
    matcher = ClauseMatcher(enable_semantic_match=False, enable_rerank=False)

    pairs = matcher.match(
        [_clause("O1", "1", "第一条 付款"), _clause("O2", "2", "第二条 交付")],
        [_clause("N1", "1", "第一条 付款"), _clause("N2", "2", "第二条 交付")],
    )

    counters = matcher.last_performance_metrics["counters"]
    operations = matcher.last_performance_metrics["operations"]
    assert counters["original_clause_count"] == 2
    assert counters["compare_clause_count"] == 2
    assert counters["candidate_count"] == 4
    assert counters["pair_count"] == len(pairs)
    assert operations["candidate_generation"]["count"] == 1
    assert operations["global_assignment"]["count"] == 1


def test_remote_semantic_and_rerank_providers_record_request_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        content = b"{}"

        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self.payload

    class FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            if url.endswith("/embeddings"):
                return FakeResponse(
                    {"data": [{"embedding": [float(index + 1), 1.0]} for index, _text in enumerate(json["input"])]}
                )
            return FakeResponse({"data": [{"score": 0.9} for _document_text in json["documents"]]})

    monkeypatch.setattr("app.services.matching.providers.httpx.Client", FakeClient)
    recorder = PerformanceRecorder()
    semantic = SemanticMatcher(
        enabled=True,
        provider="openai",
        base_url="http://model.test",
        model="embedding-model",
        batch_size=2,
    )
    semantic.set_performance_recorder(recorder)
    clauses = [
        _clause("O1", "1", "第一条"),
        _clause("O2", "2", "第二条"),
        _clause("O3", "3", "第三条"),
    ]
    semantic.prepare(clauses)

    reranker = RerankMatcher(enabled=True, base_url="http://model.test")
    reranker.set_performance_recorder(recorder)
    reranker.score_candidates(
        [
            MatchCandidate(
                clauses[0],
                clauses[1],
                80.0,
                "test",
                {},
            )
        ]
    )
    counters = recorder.snapshot()["counters"]

    assert counters["embedding_batch_count"] == 2
    assert counters["embedding_input_count"] == 3
    assert counters["embedding_http_request_count"] == 2
    assert counters["rerank_group_count"] == 1
    assert counters["rerank_document_count"] == 1
    assert counters["rerank_http_request_count"] == 1


def test_semantic_pair_preparation_batches_both_sides_and_reuses_one_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"created": 0, "posted": 0, "closed": 0}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self.payload

        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

    class FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            state["created"] += 1

        def post(self, _url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            state["posted"] += 1
            return FakeResponse(
                {"data": [{"embedding": [float(index + 1), 1.0]} for index, _text in enumerate(json["input"])]}
            )

        def close(self) -> None:
            state["closed"] += 1

    monkeypatch.setattr("app.services.matching.providers.httpx.Client", FakeClient)
    recorder = PerformanceRecorder()
    matcher = SemanticMatcher(
        enabled=True,
        provider="openai",
        base_url="http://model.test",
        model="embedding-model",
        batch_size=2,
    )
    matcher.set_performance_recorder(recorder)
    original = [_clause("O1", "1", "原版一"), _clause("O2", "2", "原版二")]
    compare = [_clause("N1", "1", "新版一"), _clause("N2", "2", "新版二")]

    choices = matcher.prepare_pair(original, compare)
    matcher.top_k(original[0], compare, choices, limit=1, score_cutoff=0)
    matcher.score(original[1], compare[1], choices[1])
    matcher.close()

    counters = recorder.snapshot()["counters"]
    assert state == {"created": 1, "posted": 2, "closed": 1}
    assert counters["embedding_input_count"] == 4
    assert counters["embedding_http_request_count"] == 2
    assert counters["embedding_http_client_create_count"] == 1


def test_rerank_reuses_one_client_across_candidate_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"created": 0, "posted": 0, "closed": 0}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"data": [{"score": 0.9}]}

    class FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            state["created"] += 1

        def post(self, _url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            state["posted"] += 1
            return FakeResponse()

        def close(self) -> None:
            state["closed"] += 1

    monkeypatch.setattr("app.services.matching.providers.httpx.Client", FakeClient)
    recorder = PerformanceRecorder()
    matcher = RerankMatcher(enabled=True, base_url="http://model.test")
    matcher.set_performance_recorder(recorder)
    clauses = [_clause("O1", "1", "第一条"), _clause("N1", "1", "第一条")]
    candidates = [MatchCandidate(clauses[0], clauses[1], 80.0, "test", {})]

    matcher.score_candidates(candidates)
    matcher.score_candidates(candidates)
    matcher.close()

    counters = recorder.snapshot()["counters"]
    assert state == {"created": 1, "posted": 2, "closed": 1}
    assert counters["rerank_http_request_count"] == 2
    assert counters["rerank_http_client_create_count"] == 1


def test_rerank_scores_candidate_groups_concurrently_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"active": 0, "max_active": 0, "created": 0}
    lock = threading.Lock()

    class FakeResponse:
        def __init__(self, score: float) -> None:
            self.score = score

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"data": [{"score": self.score}]}

    class FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            state["created"] += 1

        def post(self, _url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                time.sleep(0.01)
                score = float(str(json["query"]).split("原文")[-1]) / 10
                return FakeResponse(score)
            finally:
                with lock:
                    state["active"] -= 1

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.services.matching.providers.httpx.Client", FakeClient)
    recorder = PerformanceRecorder()
    matcher = RerankMatcher(enabled=True, base_url="http://model.test", max_inflight=3)
    matcher.set_performance_recorder(recorder)
    groups = [
        [
            MatchCandidate(
                _clause(f"O{index}", str(index), f"原文{index}"),
                _clause(f"N{index}", str(index), f"新版{index}"),
                80.0,
                "test",
                {},
            )
        ]
        for index in range(1, 5)
    ]

    scores = matcher.score_candidate_groups(groups)
    matcher.close()

    assert [group_scores[0][0] for group_scores in scores] == [10.0, 20.0, 30.0, 40.0]
    assert state["created"] == 1
    assert state["max_active"] >= 2
    counters = recorder.snapshot()["counters"]
    assert counters["rerank_parallel_worker_count"] == 3
    assert counters["rerank_http_request_count"] == 4


def test_ocr_concurrency_benchmark_reports_wall_time_and_invocations() -> None:
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    class FakeExtractor:
        name = "fake"

        def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                time.sleep(0.01)
                return ExtractionResult(document=_document(Path(path).name), extractor_used=self.name)
            finally:
                with lock:
                    state["active"] -= 1

    result = run_concurrency_level(
        FakeExtractor,
        [Path("original.pdf"), Path("compare.pdf")],
        concurrency=2,
        repetitions=2,
    )

    assert result["job_count"] == 4
    assert result["success_count"] == 4
    assert result["failure_count"] == 0
    assert result["p95_duration_seconds"] > 0
    assert len(result["invocations"]) == 4
    assert state["max_active"] == 2


def test_performance_summary_skips_non_compare_and_malformed_tasks(tmp_path: Path) -> None:
    compare_dir = tmp_path / "compare"
    compare_dir.mkdir()
    (compare_dir / "task.json").write_text(
        """
        {
          "task_id": "TCOMPARE",
          "status": "COMPLETED",
          "original_filename": "original.pdf",
          "compare_filename": "compare.pdf",
          "document_profiles": {
            "original": {"page_count": 14},
            "compare": {"page_count": 15}
          },
          "metrics": {
            "total_duration_seconds": 70,
            "peak_memory_mb": 600,
            "stages": [
              {"name": "文档解析中", "duration_seconds": 55},
              {"name": "条款匹配中", "duration_seconds": 10}
            ],
            "performance": {
              "extraction": {
                "mode": "parallel",
                "native_fast_path": {"decision": "shadow_eligible"},
                "sides": {
                  "original": {"queue_wait_duration_seconds": 0.1},
                  "compare": {"queue_wait_duration_seconds": 0.2}
                }
              },
              "matching": {
                "counters": {
                  "embedding_http_request_count": 4,
                  "rerank_http_request_count": 20
                }
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )
    extraction_dir = tmp_path / "legacy-extraction"
    extraction_dir.mkdir()
    (extraction_dir / "task.json").write_text(
        '{"task_id": "TEXTRACT", "status": "COMPLETED", "fields": []}',
        encoding="utf-8",
    )
    malformed_dir = tmp_path / "malformed"
    malformed_dir.mkdir()
    (malformed_dir / "task.json").write_text("{", encoding="utf-8")

    report = summarize_tasks(tmp_path)

    assert report["sample_count"] == 1
    assert report["skipped_count"] == 2
    assert report["page_buckets"]["0-30"]["p95_duration_seconds"] == 70.0
    assert report["samples"][0]["hotspot_ratio"] == pytest.approx(65 / 70)
    assert report["samples"][0]["extraction_mode"] == "parallel"
    assert report["samples"][0]["native_fast_path_decision"] == "shadow_eligible"
    assert report["samples"][0]["extraction_queue_wait_seconds"] == 0.3
    assert report["overall"]["parallel_sample_count"] == 1
    assert report["overall"]["native_fast_path_shadow_eligible_count"] == 1
    assert report["samples"][0]["rerank_http_request_count"] == 20
