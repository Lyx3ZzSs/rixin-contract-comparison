import fitz
import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.config import settings
from app.infrastructure.task_repository import default_task_repository
from app.main import app
from app.models import CompareTask
from auth_helpers import USER_A, USER_B


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    settings.ensure_storage()
    paths = []
    for side, count in (("original", 55), ("compare", 54)):
        path = settings.storage_dir / "tasks" / "preview-test" / "input" / side / f"{side}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open() as pdf:
            for i in range(count):
                page = pdf.new_page(width=600 if i % 2 else 840, height=900)
                if i == 1:
                    page.set_cropbox(fitz.Rect(20, 30, 580, 870))
                    page.set_rotation(90)
                if i == 2:
                    page.set_rotation(270)
            pdf.save(path)
        paths.append(path)
    task = CompareTask(task_id="preview-test", owner_sub=USER_A.sub, status="COMPLETED",
                       original_filename="original.pdf", compare_filename="compare.pdf",
                       original_pdf_path=str(paths[0]), compare_pdf_path=str(paths[1]))
    default_task_repository.save_compare_task(task)
    app.dependency_overrides[get_current_user] = lambda: USER_A
    return TestClient(app), task, paths


def test_geometry_cache_and_business_result_unchanged(sample):
    client, task, paths = sample
    before = default_task_repository.load_compare_task(task.task_id).model_dump(mode="json")
    response = client.get("/api/compare/preview-test/preview-metadata")
    assert response.status_code == 200
    data = response.json()
    assert data["coordinate_space"] == "display_pdf_points"
    for side, count, path in zip(("original", "compare"), (55, 54), paths):
        doc = data["documents"][side]
        assert doc["page_count"] == len(doc["pages"]) == count
        with fitz.open(path) as pdf:
            for i, item in enumerate(doc["pages"]):
                assert item == dict(page_no=i+1, width_points=pdf[i].rect.width,
                                    height_points=pdf[i].rect.height, rotation=pdf[i].rotation)
        assert str(path) not in response.text
    cache = settings.storage_dir / "tasks/preview-test/preview/metadata-v1.json"
    stamp = cache.stat().st_mtime_ns
    assert client.get("/api/compare/preview-test/preview-metadata").json() == data
    assert cache.stat().st_mtime_ns == stamp
    assert default_task_repository.load_compare_task(task.task_id).model_dump(mode="json") == before


def test_processing_preview_requires_both_published_files_and_preserves_access(sample):
    from app.api_presenters import compare_task_response

    client, task, paths = sample
    task.status = "PROCESSING"
    default_task_repository.save_compare_task(task)
    response = compare_task_response(task)
    assert response.preview_available is True
    assert response.report_url == ""
    assert client.get("/api/compare/preview-test/preview-metadata").status_code == 200
    app.dependency_overrides[get_current_user] = lambda: USER_B
    assert client.get("/api/compare/preview-test/preview-metadata").status_code == 404
    paths[1].unlink()
    assert compare_task_response(task).preview_available is False


def test_file_replacement_and_corrupt_cache(sample):
    client, _, paths = sample
    url = "/api/compare/preview-test/preview-metadata"
    old = client.get(url).json()
    with fitz.open() as doc:
        doc.new_page(width=300, height=400)
        doc.save(paths[1])
    new = client.get(url).json()
    assert new["documents"]["compare"]["source_revision"] != old["documents"]["compare"]["source_revision"]
    assert new["documents"]["compare"]["page_count"] == 1
    cache = settings.storage_dir / "tasks/preview-test/preview/metadata-v1.json"
    cache.write_text('{"schema_version":1}')
    assert client.get(url).json() == new


def test_access_and_file_errors(sample):
    client, _, paths = sample
    app.dependency_overrides[get_current_user] = lambda: USER_B
    assert client.get("/api/compare/preview-test/preview-metadata").status_code == 404
    app.dependency_overrides[get_current_user] = lambda: USER_A
    assert client.get("/api/compare/..%2Fbad/preview-metadata").status_code in (404, 422)
    paths[0].write_bytes(b"not a pdf")
    response = client.get("/api/compare/preview-test/preview-metadata")
    assert response.status_code == 422
    assert str(paths[0]) not in response.text
    paths[0].unlink()
    assert client.get("/api/compare/preview-test/preview-metadata").status_code == 404
