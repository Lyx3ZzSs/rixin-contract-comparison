from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api_quality import get_quality_workbench_service
from app.config import settings
from app.main import app
from app.services.quality_workbench import QualityWorkbenchService


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_case(case_root: Path, case_id: str = "case-001") -> Path:
    case_dir = case_root / case_id
    _write_json(
        case_dir / "expected.json",
        {
            "case_id": case_id,
            "source_task_id": "task-001",
            "source_files": {
                "original_filename": "original.pdf",
                "compare_filename": "compare.pdf",
            },
            "expected_diffs": [
                {"review_status": "APPROVED", "title_contains": "date"},
                {"review_status": "DRAFT", "title_contains": "amount"},
            ],
        },
    )
    _write_json(
        case_dir / "actual.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "diffs": [
                {
                    "diff_id": "D001",
                    "title": "date",
                    "diff_type": "MODIFY",
                    "source_type": "metadata",
                    "original_text": "date: 2026-06-01",
                    "compare_text": "date: 2026-06-30",
                }
            ],
        },
    )
    (case_dir / "README.md").write_text(
        "# OCR Compare Gold Case: case-001\n", encoding="utf-8"
    )
    return case_dir


@pytest.fixture
def quality_service(tmp_path: Path) -> Iterator[QualityWorkbenchService]:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    app.dependency_overrides[get_quality_workbench_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.clear()


def test_list_quality_cases(quality_service: QualityWorkbenchService) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)

    response = client.get("/api/quality/cases")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["cases"] == [
        {
            "case_id": "case-001",
            "schema_version": "1.0",
            "dataset_split": "legacy",
            "case_tags": [],
            "baseline_required": False,
            "source_task_id": "task-001",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "approved_expected_count": 1,
            "draft_expected_count": 1,
            "rejected_expected_count": 0,
            "actual_diff_count": 1,
            "has_actual_json": True,
            "has_source_pdfs": False,
        }
    ]


def test_update_quality_expected_diff(
    quality_service: QualityWorkbenchService,
) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)

    response = client.patch(
        "/api/quality/cases/case-001/expected-diffs/0",
        json={
            "review_status": "DRAFT",
            "severity": "major",
            "notes": "Needs reviewer confirmation",
            "expected_evidence": [
                {
                    "side": "compare",
                    "page": 1,
                    "text": "金额为200元",
                }
            ],
            "unexpected_field": "ignored",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    diff = payload["expected"]["expected_diffs"][0]
    assert diff["title_contains"] == "date"
    assert diff["review_status"] == "DRAFT"
    assert diff["severity"] == "major"
    assert diff["notes"] == "Needs reviewer confirmation"
    assert diff["expected_evidence"] == [
        {
            "side": "compare",
            "page": 1,
            "text": "金额为200元",
        }
    ]
    assert "unexpected_field" not in diff


def test_update_quality_expected_diff_allows_schema_11_review_fields(
    quality_service: QualityWorkbenchService,
) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)

    response = client.patch(
        "/api/quality/cases/case-001/expected-diffs/0",
        json={
            "review_status": "REJECTED",
            "reviewer": "quality@example.com",
            "reviewed_at": "2026-07-01T10:30:00+08:00",
            "false_positive_reason": "Actual diff is acceptable",
            "false_negative_reason": "System missed this metadata diff before",
            "should_not_match_again": True,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    diff = payload["expected"]["expected_diffs"][0]
    assert diff["review_status"] == "REJECTED"
    assert diff["reviewer"] == "quality@example.com"
    assert diff["reviewed_at"] == "2026-07-01T10:30:00+08:00"
    assert diff["false_positive_reason"] == "Actual diff is acceptable"
    assert diff["false_negative_reason"] == "System missed this metadata diff before"
    assert diff["should_not_match_again"] is True


def test_update_quality_expected_diff_rejects_non_list_expected_evidence(
    quality_service: QualityWorkbenchService,
) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)

    response = client.patch(
        "/api/quality/cases/case-001/expected-diffs/0",
        json={"expected_evidence": {"side": "compare", "page": 1}},
    )

    assert response.status_code == 422


def test_quality_case_path_traversal_returns_400(
    quality_service: QualityWorkbenchService,
) -> None:
    client = TestClient(app)

    response = client.get("/api/quality/cases/..%2Fsecret")

    assert response.status_code == 400


def test_quality_case_dot_segment_returns_400(
    quality_service: QualityWorkbenchService,
) -> None:
    client = TestClient(app)

    response = client.get("/api/quality/cases/%2E%2E")

    assert response.status_code == 400


def test_default_quality_service_uses_configured_task_root(tmp_path: Path) -> None:
    original_tasks_dir = settings.tasks_dir
    settings.tasks_dir = tmp_path / "configured-tasks"
    try:
        service = get_quality_workbench_service()
    finally:
        settings.tasks_dir = original_tasks_dir

    assert service.task_root == tmp_path / "configured-tasks"


def test_export_quality_case(quality_service: QualityWorkbenchService) -> None:
    _write_json(
        quality_service.task_root / "task-001" / "task.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "diffs": [
                {
                    "diff_id": "D001",
                    "title": "合同金额",
                    "diff_type": "MODIFY",
                    "source_type": "metadata",
                    "original_text": "金额为100元",
                    "compare_text": "金额为200元",
                }
            ],
        },
    )
    client = TestClient(app)

    response = client.post(
        "/api/quality/cases/export",
        json={"task_id": "task-001", "case_id": "case-001"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "case_id": "case-001",
        "task_id": "task-001",
        "expected_diff_count": 1,
        "actual_diff_count": 1,
    }
    exported = json.loads(
        (quality_service.case_root / "case-001" / "expected.json").read_text(
            encoding="utf-8"
        )
    )
    assert exported["expected_diffs"][0]["review_status"] == "DRAFT"


def test_review_quality_task_endpoint(
    quality_service: QualityWorkbenchService,
) -> None:
    _write_json(
        quality_service.task_root / "task-001" / "task.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "diffs": [
                {
                    "diff_id": "D001",
                    "title": "OCR punctuation",
                    "diff_type": "MODIFY",
                    "source_type": "clause",
                    "original_snippet": "/",
                    "compare_snippet": "∠",
                    "match_score": 99,
                    "review_flags": ["POSSIBLE_OCR_NOISE"],
                    "quality_status": "NEEDS_REVIEW",
                },
                {
                    "diff_id": "D002",
                    "title": "Contract amount",
                    "diff_type": "MODIFY",
                    "source_type": "clause",
                    "original_snippet": "1.5%",
                    "compare_snippet": "15%",
                    "match_score": 99,
                },
            ],
        },
    )
    client = TestClient(app)

    response = client.get("/api/quality/tasks/task-001/review")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["task_id"] == "task-001"
    assert payload["historical_diff_count"] == 2
    assert payload["retained_diff_count"] == 1
    assert payload["suppressed_diff_count"] == 1
    assert payload["ocr_quality_summary"] == {}
    assert [diff["diff_id"] for diff in payload["retained_diffs"]] == ["D002"]
    assert payload["suppressed_diffs"][0]["diff_id"] == "D001"
    assert payload["suppressed_diffs"][0]["suppression_reason"] == "clause_ocr_noise"


def test_review_quality_task_rejects_unsafe_task_id(
    quality_service: QualityWorkbenchService,
) -> None:
    client = TestClient(app)

    response = client.get("/api/quality/tasks/..%2Fsecret/review")

    assert response.status_code == 400


def test_review_quality_task_missing_task_returns_404(
    quality_service: QualityWorkbenchService,
) -> None:
    client = TestClient(app)

    response = client.get("/api/quality/tasks/missing-task/review")

    assert response.status_code == 404


def test_evaluate_quality_endpoint(quality_service: QualityWorkbenchService) -> None:
    case_dir = _make_case(quality_service.case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["dataset_split"] = "regression"
    expected["expected_diffs"] = [
        {
            "review_status": "APPROVED",
            "title_contains": "date",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "original_contains": "2026-06-01",
            "compare_contains": "2026-06-30",
        }
    ]
    _write_json(case_dir / "expected.json", expected)
    client = TestClient(app)

    response = client.post(
        "/api/quality/evaluate",
        json={"dataset_splits": ["regression"], "run_id": "eval-api-001"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == "eval-api-001"
    assert payload["status"] == "COMPLETED"
    assert payload["report"]["dataset_splits"] == ["regression"]
    assert payload["report"]["aggregate"]["recall"] == 1.0
    assert payload["comparison"] is None


def test_evaluate_quality_rejects_unsafe_run_id(
    quality_service: QualityWorkbenchService,
) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/quality/evaluate",
        json={"run_id": "../bad"},
    )

    assert response.status_code == 400


def test_quality_regression_endpoint(quality_service: QualityWorkbenchService) -> None:
    case_dir = _make_case(quality_service.case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["dataset_split"] = "regression"
    expected["expected_diffs"] = [
        {
            "review_status": "APPROVED",
            "title_contains": "date",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "original_contains": "2026-06-01",
            "compare_contains": "2026-06-30",
        }
    ]
    _write_json(case_dir / "expected.json", expected)
    client = TestClient(app)

    response = client.post(
        "/api/quality/regression",
        json={
            "dataset_splits": ["regression"],
            "baseline_name": "missing-baseline",
            "run_id": "regression-api-001",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == "regression-api-001"
    assert payload["status"] == "PASSED"
    assert payload["report"]["regression"]["run_id"] == "regression-api-001"
    assert payload["comparison"]["baseline_available"] is False


def test_quality_regression_rejects_unsafe_run_id(
    quality_service: QualityWorkbenchService,
) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/quality/regression",
        json={"run_id": "../bad"},
    )

    assert response.status_code == 400


def test_quality_regression_rejects_unsafe_baseline_name(
    quality_service: QualityWorkbenchService,
) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/quality/regression",
        json={"baseline_name": "../bad"},
    )

    assert response.status_code == 400


def test_create_and_delete_quality_expected_diff(
    quality_service: QualityWorkbenchService,
) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)

    create_response = client.post(
        "/api/quality/cases/case-001/expected-diffs",
        json={
            "diff_type": "ADD",
            "source_type": "clause",
            "title_contains": "warranty",
            "review_status": "DRAFT",
            "unexpected_field": "ignored",
        },
    )

    assert create_response.status_code == 200, create_response.text
    created = create_response.json()
    assert len(created["expected"]["expected_diffs"]) == 3
    assert created["expected"]["expected_diffs"][-1] == {
        "diff_type": "ADD",
        "source_type": "clause",
        "title_contains": "warranty",
        "review_status": "DRAFT",
    }

    delete_response = client.delete("/api/quality/cases/case-001/expected-diffs/2")

    assert delete_response.status_code == 200, delete_response.text
    deleted = delete_response.json()
    assert len(deleted["expected"]["expected_diffs"]) == 2
    assert all(
        diff.get("title_contains") != "warranty"
        for diff in deleted["expected"]["expected_diffs"]
    )
