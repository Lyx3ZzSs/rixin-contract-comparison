from app.models import BBox,CompareTask,DiffItem,EvidenceBox
from app.services.diff_engine import DiffEngine
from app.services.diff_events import build_snapshot,event_response


def task(before,after):
    _,_,a,b=DiffEngine()._changed_snippets(before,after)
    def boxes(text,ranges):
        return [EvidenceBox(page_no=2,bbox=BBox(x0=10,y0=20+i*20,x1=40,y1=30+i*20),text=text[r.start:r.end],highlight_type=r.highlight_type) for i,r in enumerate(ranges)]
    d=DiffItem(diff_id='d',diff_type='MODIFY',title='签约主体',original_text=before,compare_text=after,
        original_change_ranges=a,compare_change_ranges=b,original_evidence=boxes(before,a),compare_evidence=boxes(after,b))
    t=CompareTask(task_id='t',diffs=[d]);t.event_snapshot=build_snapshot(t);return t


def test_name_change_is_owned_by_party_and_keeps_context():
    before='签约主体\n委托人:甲公司\n法定代表人:许峰\n承包人:乙公司\n法定代表人:雍正\n双方同意签约。'
    t=task(before,before.replace('许峰','吴峻'))
    e=event_response(t)['events'][0]
    assert e['diff']['title']=='委托人 · 法定代表人'
    assert e['diff']['original_text']=='许峰'
    assert e['diff']['compare_text']=='吴峻'
    assert e['context']['original_text']==before
    assert t.diffs[0].original_text==before


def test_two_parties_same_label_are_separate_changes():
    a='委托人:甲公司\n法定代表人:许峰\n承包人:乙公司\n法定代表人:雍正'
    t=task(a,a.replace('许峰','吴峻').replace('雍正','李明'))
    events=event_response(t)['events']
    assert {e['diff']['title'] for e in events}=={'委托人 · 法定代表人','承包人 · 法定代表人'}
    assert len(events)==2


def test_real_placeholder_deletion_and_valid_value_are_not_suppressed():
    a='委托人:甲公司\n传真号码:/\n邮箱地址:/'
    for b,direction in [('委托人:甲公司\n传真号码:\n邮箱地址:/','DELETE'),
                         ('委托人:甲公司\n传真号码:123456\n邮箱地址:/','MODIFY')]:
        e=event_response(task(a,b))['events'][0]
        assert e['diff']['title']=='委托人 · 传真号码'
        assert e['direction']==direction
        assert e['diff']['original_text']=='/'


def test_empty_field_filled_and_preamble_do_not_share_review_unit():
    a='委托人:甲公司\n传真号码:\n邮箱地址:/\n双方同意签约。'
    e=event_response(task(a,a.replace('传真号码:', '传真号码:123456')))['events'][0]
    assert e['direction']=='ADD'
    assert e['diff']['compare_text']=='123456'
    assert e['diff']['title']=='委托人 · 传真号码'
    assert '双方同意' not in e['diff']['compare_text']
    assert '双方同意' in e['context']['compare_text']


def test_ambiguous_owner_or_changed_prose_keeps_full_context():
    a='委托人:甲公司\n法定代表人:许峰\n法定代表人:陈雄\n双方同意签约。'
    assert 'field_selection' not in event_response(task(a,a.replace('许峰','吴峻')))['events'][0]
    a='委托人:甲公司\n法定代表人:许峰\n双方同意签约。'
    assert 'field_selection' not in event_response(task(a,a.replace('许峰','吴峻').replace('同意','拒绝')))['events'][0]


def test_field_reviews_are_independent_and_export_preserves_context(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import api,api_events
    from app.auth.dependencies import get_current_user
    from app.auth.models import CurrentUser,AGENT_USER
    from app.config import Settings
    from app.infrastructure.task_repository import SQLiteTaskRepository
    a='委托人:甲公司\n法定代表人:许峰\n承包人:乙公司\n法定代表人:雍正'
    t=task(a,a.replace('许峰','吴峻').replace('雍正','李明'))
    t=CompareTask.model_validate({**t.model_dump(),'owner_sub':'owner','status':'COMPLETED'})
    repo=SQLiteTaskRepository(Settings(storage_dir=tmp_path));repo.save_compare_task(t)
    application=SimpleNamespace(repository=repo,load_compare_task=repo.load_compare_task)
    monkeypatch.setattr(api,'default_compare_task_application',application)
    monkeypatch.setattr(api_events,'default_compare_task_application',application)
    app=FastAPI();app.include_router(api_events.router)
    app.dependency_overrides[get_current_user]=lambda:CurrentUser(sub='owner',roles=frozenset({AGENT_USER}))
    client=TestClient(app);before=client.get('/api/compare/t/events').json()
    event=before['events'][0]
    r=client.put('/api/compare/t/events/'+event['event_id']+'/review',json={'revision':before['revision'],'review_status':'CONFIRMED'})
    assert r.status_code==200
    assert [e['diff']['review_status'] for e in r.json()['events']]==['CONFIRMED','UNREVIEWED']
    assert repo.load_compare_task('t').diffs[0].review_status=='UNREVIEWED'
    exported=client.get('/api/compare/t/events/export').json()
    assert exported['events'][0]['context']['original_text']==a
    repo.close()
