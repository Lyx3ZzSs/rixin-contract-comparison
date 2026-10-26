import base64
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app import api_textin
from app.api_textin import router, serialize_task
from app.auth.dependencies import get_current_user
from app.auth.models import CurrentUser, AGENT_USER
from app.models import CompareTask, DiffItem, BBox, EvidenceBox


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    user = CurrentUser(sub="test", roles=frozenset({AGENT_USER}))
    app.dependency_overrides[api_textin.require_app_role] = lambda: user
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_create_routes_to_real_application_contract(client, monkeypatch):
    submitted = {}

    async def submit(**kwargs):
        submitted.update(kwargs)
        assert await kwargs["original_file"].read() == b"%PDF-1.7 test"

    monkeypatch.setattr(api_textin.default_compare_task_application, "submit_uploads", submit)
    doc = {"filename": "a.pdf", "filedata": base64.b64encode(b"%PDF-1.7 test").decode()}
    r = client.post("/api/contracts/v3/comparison/external/create", json={"standard_doc": [doc], "compare_doc": [doc]})
    assert r.status_code == 200
    assert r.json()["code"] == 200
    assert "/tasks/" in r.json()["data"]["preview_url"]
    assert submitted["external_compare_options"]["merge_diff"] is True
    assert submitted["owner"].sub == "test"


@pytest.mark.parametrize(
    "patch",
    [
        {"token_mode": 1},
        {"config": {"remove_watermark": 1}},
        {"convert_arg": {"remove_symbol": 1}},
        {"convert_arg": {"remove_stamp": 1}},
        {"convert_arg": {"remove_headerfooter": 1}},
        {"convert_arg": {"remove_stamp": 0}},
        {"convert_arg": {"remove_headerfooter": 0}},
        {"convert_arg": {"merge_diff": "1"}},
        {"unknown": 1},
        {"compare_doc": []},
        {"standard_doc": [{"filename": "x.docx", "filedata": "secret-base64"}]},
    ],
)
def test_unsupported_request_fails_without_echoing_input(client, patch):
    doc = {"filename": "a.pdf", "filedata": base64.b64encode(b"%PDF-1.7 test").decode()}
    r = client.post(
        "/api/contracts/v3/comparison/external/create", json={"standard_doc": [doc], "compare_doc": [doc], **patch}
    )
    assert r.status_code == 400
    assert r.json()["code"] == 400
    assert "secret-base64" not in r.text


def test_serialization_counts_coordinates_and_status():
    t = CompareTask(
        task_id="x",
        status="COMPLETED",
        diffs=[
            DiffItem(
                diff_id="a",
                diff_type="ADD",
                compare_evidence=[EvidenceBox(page_no=8, bbox=BBox(x0=1, y0=2, x1=3, y1=4), text="new")],
            )
        ],
    )
    p = serialize_task(t)["data"]["result"][0]
    assert p["diff_info"] == {"insert": 1, "delete": 0, "change": 0, "total": 1}
    assert p["diff_result"][0]["diff"][1][0]["page_index"] == 7
    assert p["diff_result"][0]["diff"][1][0]["polygon"] == [1, 2, 3, 2, 3, 4, 1, 4]
    assert "diff_result" not in serialize_task(t, False)["data"]["result"][0]
    t.status = "PROCESSING"
    assert serialize_task(t)["data"] == {"status": 2, "metrics": {}, "result": []}
    t.status = "FAILED"
    assert serialize_task(t)["data"]["status"] == -1


def test_character_positions_are_real_and_missing_geometry_is_empty():
    from app.models import CharBox

    box = BBox(x0=1, y0=2, x1=5, y1=8)
    evidence = EvidenceBox(page_no=8, bbox=box, text="字", char_boxes=[CharBox(char="字", page_no=8, bbox=box)])
    task = CompareTask(
        task_id="char", status="COMPLETED", diffs=[DiffItem(diff_id="a", diff_type="ADD", compare_evidence=[evidence])]
    )
    line = serialize_task(task)["data"]["result"][0]["diff_result"][0]["diff"][1][0]
    assert line["char_polygons"] == [[1, 2, 5, 2, 5, 8, 1, 8]]
    evidence.text = "不同"
    assert serialize_task(task)["data"]["result"][0]["diff_result"][0]["diff"][1][0]["char_polygons"] == []


def test_real_merged_table_cell_and_missing_structure_contract():
    cell = dict(
        row=1,
        col=26,
        rowspan=2,
        colspan=2,
        page_no=8,
        text="金额",
        bbox=dict(x0=1, y0=2, x1=5, y1=8),
        table_id="table-a",
    )
    task = CompareTask(
        task_id="cell",
        status="COMPLETED",
        diffs=[
            DiffItem(
                diff_id="a",
                diff_type="ADD",
                source_type="table",
                match_score_details={
                    "cell_observations": [{"diff_type": "ADD", "original_cell": None, "compare_cell": cell}]
                },
            )
        ],
    )
    entry = serialize_task(task)["data"]["result"][0]["diff_result"][0]
    assert entry["cell_cnt"] == 1
    assert entry["diff"][0]["diff"][0] == []
    line = entry["diff"][0]["diff"][1][0]
    assert line["cell_position"] == "AA2:AB3"
    assert line["merged"] is True
    assert line["page_index"] == 7
    task.diffs[0].match_score_details = {}
    assert serialize_task(task)["data"]["result"][0]["diff_result"][0]["cell_cnt"] == 0
