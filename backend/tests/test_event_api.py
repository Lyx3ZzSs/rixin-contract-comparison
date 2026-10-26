from types import SimpleNamespace
import pytest
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


@pytest.mark.parametrize('side,direction', [('compare','ADD'),('original','DELETE')])
def test_seal_decision_changes_counts_reversibly_without_regrouping(tmp_path, monkeypatch, side, direction):
    from app.models import BBox, EvidenceBox, ComparisonObservation
    repo = SQLiteTaskRepository(Settings(storage_dir=tmp_path))
    task = CompareTask(task_id='seal-review', owner_sub='owner', status='COMPLETED', diffs=[
        DiffItem(diff_id=x, diff_type='ADD', compare_clause_id=x) for x in ['a', 'b']],
        comparison_observations=[ComparisonObservation(observation_id='impression-seal-0', source_type='seal',
            reason='MIRRORED_ADJACENT_PAGE_IMPRESSION', **{side+'_evidence':[EvidenceBox(
                page_no=12, bbox=BBox(x0=100,y0=100,x1=200,y1=200), method='comparison_observation')]})])
    task.event_snapshot = build_snapshot(task, clauses={'compare':[
        {'clause_id':x,'order_index':i} for i,x in enumerate(['a','b'])]}, matches=[],
        page_alignment=[{'original_pages':[12],'compare_pages':[12],'status':'MATCHED'}])
    repo.save_compare_task(task)
    application=SimpleNamespace(repository=repo,load_compare_task=repo.load_compare_task)
    monkeypatch.setattr(api,'default_compare_task_application',application)
    monkeypatch.setattr(api_events,'default_compare_task_application',application)
    app=FastAPI();app.include_router(api_events.router)
    app.dependency_overrides[get_current_user]=lambda:CurrentUser(sub='owner',roles=frozenset({AGENT_USER}))
    client=TestClient(app);url='/api/compare/seal-review/events'
    before=client.get(url).json();group=before['events'][0]
    def decide(target, decision):
        current=client.get(url).json()
        response=client.put(f'{url}/{target}/review',json={'revision':current['revision'],
            'review_status':'CONFIRMED','seal_decision':decision})
        assert response.status_code==200,response.text
        return response.json()
    added=decide('impression-seal-0','CONFIRMED_CHANGE')
    assert added['event_count']==2 and added['comparison_observation_count']==0
    assert added['events'][0]==group and added['page_alignment']==before['page_alignment']
    seal=next(e for e in added['events'] if e['diff']['source_type']=='seal')
    assert seal['direction']==direction and seal['diff']['review_status']=='CONFIRMED'
    assert seal['diff'][side+'_evidence'][0]['method']=='seal_region'
    assert client.get(url+'/export').json()['counts']['total']==2
    repeated=decide(seal['event_id'],'CONFIRMED_CHANGE')
    assert repeated['event_count']==2
    removed=decide(seal['event_id'],'IMPRESSION')
    assert removed['event_count']==2 and removed['comparison_observation_count']==0
    assert removed['events'][-1]['diff']['match_score_details']['seal_origin_decision']=='CONFIRMED_IMPRESSION'
    assert removed['events'][0]==group
    reset=decide('impression-seal-0','RESET')
    assert reset['event_count']==1 and reset['comparison_observations'][0]['review_status']=='UNREVIEWED'
    assert reset['comparison_observations'][0]['change_resolution'] is None
    rejected=client.put(f"{url}/{group['event_id']}/review",json={'revision':reset['revision'],
        'review_status':'CONFIRMED','seal_decision':'IMPRESSION'})
    assert rejected.status_code==422
    assert client.get(url).json()['revision']==reset['revision']
    repo.close()


def test_counted_seal_can_be_explained_as_impression_then_restore_machine_candidate():
    from app.models import BBox, EvidenceBox
    from app.services.diff_events import event_response
    from app.services.review_service import CompareReviewService
    from app.services.seal_review import apply_seal_decision
    raw=DiffItem(diff_id='seal',diff_type='ADD',source_type='seal',quality_status='NEEDS_REVIEW',
        match_score_details={'seal_change_decision':'CANDIDATE','impression_evidence':[{'mirror_score':.294}]},
        compare_evidence=[EvidenceBox(page_no=28,bbox=BBox(x0=100,y0=100,x1=200,y1=200),method='seal_region')])
    task=CompareTask(task_id='candidate',diffs=[raw]);service=CompareReviewService()
    before=event_response(task)
    apply_seal_decision(task,before['events'][0]['event_id'],'IMPRESSION',before,service,'tester','透印')
    explained=event_response(task)
    assert explained['event_count']==1 and explained['comparison_observation_count']==0
    oid=explained['events'][0]['event_id']
    apply_seal_decision(task,oid,'RESET',explained,service,'tester',None)
    restored=event_response(task)
    assert restored['event_count']==1 and restored['comparison_observation_count']==0
    assert restored['events'][0]['event_id']==before['events'][0]['event_id']
    assert task.diffs[0].match_score_details==raw.match_score_details
    assert task.diffs[0].compare_evidence==raw.compare_evidence


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


def test_unresolved_observation_review_export_and_revision_guard(tmp_path, monkeypatch):
    from tests.test_d005_recovery import unresolved
    repo = SQLiteTaskRepository(Settings(storage_dir=tmp_path))
    task = CompareTask(task_id='unresolved', owner_sub='owner', status='COMPLETED', diffs=[unresolved()])
    repo.save_compare_task(task)
    application = SimpleNamespace(repository=repo, load_compare_task=repo.load_compare_task)
    monkeypatch.setattr(api, 'default_compare_task_application', application)
    monkeypatch.setattr(api_events, 'default_compare_task_application', application)
    app = FastAPI()
    app.include_router(api_events.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub='owner', roles=frozenset({AGENT_USER}))
    client = TestClient(app)
    before = client.get('/api/compare/unresolved/events').json()
    assert before['event_count'] == 0 and before['comparison_observation_count'] == 1
    url = '/api/compare/unresolved/events/observation-D005/review'
    review = {'revision':before['revision'], 'review_status':'CONFIRMED', 'review_comment':'已人工核验'}
    response = client.put(url, json=review)
    assert response.status_code == 200
    assert response.json()['comparison_observations'][0]['review_comment'] == '已人工核验'
    assert client.put(url, json=review).status_code == 409
    exported = client.get('/api/compare/unresolved/events/export').json()
    assert exported['counts']['total'] == 0 and exported['comparison_observations'][0]['review_status'] == 'CONFIRMED'
    # Reading/reviewing an old task does not delete its historical raw failure record.
    assert len(repo.load_compare_task('unresolved').diffs) == 1
    repo.close()
