from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.infrastructure.artifact_store import ArtifactStore, LocalArtifactStore
from app.models import Clause
from app.services.compare_debug import CompareDebugWriter


def test_clause_split_quality_reports_diagnostic_distributions(tmp_path: Path) -> None:
    _configure_storage(tmp_path)
    artifact_store: ArtifactStore = LocalArtifactStore(settings)
    writer = CompareDebugWriter(artifact_store)
    clauses = [
        Clause(
            clause_id="O001",
            clause_no="1",
            title="付款",
            text="1. 付款\n甲方应支付1000元。",
            normalized_text="1. 付款\n甲方应支付1000元。",
            section_type="main_contract",
            section_path=["第一章 总则"],
            clause_key="main_contract/n1",
            page_numbers=[1],
            source_block_ids=["b1"],
            bboxes=[],
            segmentation_reason="marker:1|heading_score:0.76|signals:marker,title_length|risks:PUNCTUATED_HEADING|order:reading_order_geometry_repair",
            segmentation_confidence=0.76,
            split_flags=["READING_ORDER_REPAIRED"],
        ),
        Clause(
            clause_id="O002",
            clause_no="2",
            title="交付",
            text="2. 交付\n乙方应跨页交付服务。",
            normalized_text="2. 交付\n乙方应跨页交付服务。",
            section_type="main_contract",
            section_path=["第一章 总则"],
            clause_key="main_contract/n1",
            page_numbers=[1, 2],
            source_block_ids=["b2", "b3"],
            bboxes=[],
            segmentation_reason="marker:2|heading_score:0.52|signals:marker|risks:WEAK_HEADING|order:continuation_boundary_repair",
            segmentation_confidence=0.52,
            split_flags=["PARAGRAPH_MERGED", "CROSS_PAGE_CONTINUATION_MERGED", "WEAK_HEADING"],
        ),
        Clause(
            clause_id="O003",
            clause_no="3",
            title="验收",
            text="3. 验收",
            normalized_text="3. 验收",
            section_type="main_contract",
            section_path=["第二章 验收"],
            clause_key="main_contract/n3",
            page_numbers=[2],
            source_block_ids=["b4"],
            bboxes=[],
            segmentation_reason="fallback_single_unit",
            segmentation_confidence=0.45,
        ),
    ]

    path = Path(writer.write_clause_split_quality("task-1", clauses, []))
    payload = json.loads(path.read_text(encoding="utf-8"))["original"]

    assert payload["split_flag_counts"]["CROSS_PAGE_CONTINUATION_MERGED"] == 1
    assert payload["duplicate_clause_key_count"] == 1
    assert payload["duplicate_clause_key_groups"][0]["count"] == 2
    assert payload["segmentation_diagnostics"]["base_counts"]["marker"] == 2
    assert payload["segmentation_diagnostics"]["base_counts"]["fallback"] == 1
    assert payload["segmentation_diagnostics"]["heading_score_buckets"] == {
        "normal_0_68_to_0_85": 1,
        "very_low_lt_0_55": 1,
    }
    assert payload["segmentation_diagnostics"]["heading_risk_counts"] == {
        "PUNCTUATED_HEADING": 1,
        "WEAK_HEADING": 1,
    }
    assert payload["section_path_diagnostics"]["section_path_change_count"] == 1
    assert payload["source_evidence_diagnostics"]["page_span_counts"]["multi_page"] == 1
    assert payload["result_stage_summary"]["clauses_with_cross_page_merge"] == 1


def _configure_storage(tmp_path: Path) -> None:
    settings.storage_dir = tmp_path / "storage"
    settings.uploads_dir = settings.storage_dir / "uploads"
    settings.tasks_dir = settings.storage_dir / "tasks"
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.debug_dir = settings.storage_dir / "debug"
    settings.ensure_storage()
