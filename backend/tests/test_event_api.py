from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import api, api_events
from app.auth.dependencies import get_current_user
from app.auth.models import CurrentUser, AGENT_USER
from app.config import Settings
from app.infrastructure.task_repository import SQLiteTaskRepository
from app.models import CompareTask, DiffItem
from app.services.diff_events import build_snapshot


def test_event_access_atomic_review_and_export(tmp_path, monkeypatch):
    repo = SQLiteTaskRepository(Settings(storage_dir=tmp_path))
    task = CompareTask(
        task_id="x",
        owner_sub="owner",
        status="COMPLETED",
        diffs=[
            DiffItem(diff_id="a", diff_type="ADD", compare_clause_id="a"),
            DiffItem(diff_id="b", diff_type="ADD", compare_clause_id="b"),
        ],
    )
    task.event_snapshot = build_snapshot(
        task,
        clauses={"compare": [{"clause_id": "a", "order_index": 1}, {"clause_id": "b", "order_index": 2}]},
        matches=[],
    )
    repo.save_compare_task(task)
    application = SimpleNamespace(repository=repo, load_compare_task=repo.load_compare_task)
    monkeypatch.setattr(api, "default_compare_task_application", application)
    monkeypatch.setattr(api_events, "default_compare_task_application", application)
    app = FastAPI()
    app.include_router(api_events.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub="other", roles=frozenset({AGENT_USER}))
    client = TestClient(app)
    assert client.get("/api/compare/x/events").status_code == 404
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub="owner", roles=frozenset({AGENT_USER}))
    before = client.get("/api/compare/x/events").json()
    id = before["events"][0]["event_id"]
    result = client.put(
        f"/api/compare/x/events/{id}/review", json={"revision": before["revision"], "review_status": "CONFIRMED"}
    )
    assert result.status_code == 200
    assert result.json()["revision"] == before["revision"] + 1
    assert result.json()["events"][0]["diff"]["review_status"] == "CONFIRMED"
    assert (
        client.put(
            f"/api/compare/x/events/{id}/review",
            json={"revision": before["revision"], "review_status": "FALSE_POSITIVE"},
        ).status_code
        == 409
    )
    assert client.get("/api/compare/x/events/export").json()["event_count"] == before["event_count"]
    assert all(d.review_status == "CONFIRMED" for d in repo.load_compare_task("x").diffs)
    repo.close()


def test_table_units_have_independent_api_review_and_matching_export(tmp_path, monkeypatch):
    from tests.test_difference_counting import task as make_task
    from app.api_textin import serialize_task

    repo = SQLiteTaskRepository(Settings(storage_dir=tmp_path))
    task = make_task().model_copy(update={"status": "COMPLETED", "owner_sub": "owner"})
    repo.save_compare_task(task)
    application = SimpleNamespace(repository=repo, load_compare_task=repo.load_compare_task)
    monkeypatch.setattr(api, "default_compare_task_application", application)
    monkeypatch.setattr(api_events, "default_compare_task_application", application)
    app = FastAPI()
    app.include_router(api_events.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub="owner", roles=frozenset({AGENT_USER}))
    client = TestClient(app)
    before = client.get("/api/compare/table/events").json()
    response = client.put(
        f"/api/compare/table/events/{before['events'][0]['event_id']}/review",
        json={"revision": before["revision"], "review_status": "CONFIRMED"},
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert [e["diff"]["review_status"] for e in after["events"]] == ["CONFIRMED", "UNREVIEWED"]
    assert client.get("/api/compare/table/events/export").json()["counts"] == before["counts"]
    result = serialize_task(repo.load_compare_task("table"))["data"]["result"][0]
    assert result["diff_info"]["total"] == 2
    assert [x["type"] for x in result["diff_result"]] == ["cell", "cell"]
    assert all(x["diff"][0]["type"] == "text" for x in result["diff_result"])
    repo.close()
