from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.config import settings
from app.infrastructure.task_repository import default_task_repository
from app.main import app
from app.models import CompareTask, DiffItem

from auth_helpers import ADMIN, NO_ROLE, USER_A, USER_B


def _configure_storage(tmp_path: Path) -> None:
    settings.storage_dir = tmp_path / "storage"
    settings.tasks_dir = settings.storage_dir / "tasks"
    settings.ensure_storage()


def _save_task(task_id: str, owner_sub: str) -> CompareTask:
    task_dir = settings.tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    original = task_dir / "original.pdf"
    compare = task_dir / "compare.pdf"
    original.write_bytes(b"%PDF-original")
    compare.write_bytes(b"%PDF-compare")
    task = CompareTask(
        task_id=task_id,
        owner_sub=owner_sub,
        status="COMPLETED",
        original_pdf_path=str(original),
        compare_pdf_path=str(compare),
        diffs=[DiffItem(diff_id="D1", diff_type="ADD")],
    )
    default_task_repository.save_compare_task(task)
    return task


def _as(user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def test_records_and_task_subresources_enforce_owner_visibility(tmp_path: Path) -> None:
    _configure_storage(tmp_path)
    _save_task("TA", USER_A.sub)
    _save_task("TB", USER_B.sub)
    _save_task("TLEGACY", "")
    client = TestClient(app)

    _as(USER_A)
    records = client.get("/api/compare/records")
    assert records.status_code == 200
    assert [item["task_id"] for item in records.json()["records"]] == ["TA"]
    assert client.get("/api/compare/TA").status_code == 200

    for suffix in ("", "/progress", "/diffs", "/quality", "/original", "/compare"):
        assert client.get(f"/api/compare/TB{suffix}").status_code == 404
        assert client.get(f"/api/compare/TLEGACY{suffix}").status_code == 404

    _as(ADMIN)
    admin_records = client.get("/api/compare/records")
    assert admin_records.status_code == 200
    assert {item["task_id"] for item in admin_records.json()["records"]} == {"TA", "TB"}
    assert client.get("/api/compare/TB").status_code == 200
    assert client.get("/api/compare/TLEGACY").status_code == 404


def test_mutations_require_an_application_role_and_ignore_forged_reviewer(tmp_path: Path) -> None:
    _configure_storage(tmp_path)
    _save_task("TA", USER_A.sub)
    _save_task("TB", USER_B.sub)
    client = TestClient(app)
    payload = {"review_status": "CONFIRMED", "review_comment": "ok", "reviewed_by": "forged"}

    _as(NO_ROLE)
    assert client.patch("/api/compare/TA/diffs/D1/review", json=payload).status_code == 403

    _as(USER_A)
    assert client.patch("/api/compare/TB/diffs/D1/review", json=payload).status_code == 404
    own = client.patch("/api/compare/TA/diffs/D1/review", json=payload)
    assert own.status_code == 200
    assert own.json()["diff"]["reviewed_by"] == USER_A.sub


def test_retired_quality_workbench_returns_not_found() -> None:
    client = TestClient(app)

    _as(USER_A)
    assert client.get("/api/quality/cases").status_code == 404

    _as(ADMIN)
    assert client.get("/api/quality/cases").status_code == 404
