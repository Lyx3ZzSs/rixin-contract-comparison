"""Alternating local result-delivery experiment on all frozen A-E tasks.

Includes actual event_response, FastAPI dict encoding, and Starlette JSON render.
Does not label this component measurement as click-to-render E2E.
"""
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse
from app.infrastructure.task_repository import SQLiteTaskRepository
from app.services.diff_events import event_response
from event_wire import pack,unpack

SOURCE=ROOT/'output/analysis/e2e-20260921'
OUT=ROOT/'output/analysis/perf-phase2-20260921/event-wire'
OUT.mkdir(parents=True,mode=0o700,exist_ok=True)
rows=[]
for label in 'ABCDE':
    task_id=json.loads((SOURCE/f'{label}-r2-browser.json').read_text())['taskId']
    with sqlite3.connect(f'file:{SOURCE}/runtime-{label}/tasks.sqlite3?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        task=SQLiteTaskRepository()._deserialize(connection.execute('select * from tasks where task_id=?',(task_id,)).fetchone())
    expected=event_response(task)
    for repeat in range(1,4):
        for variant in ['baseline','candidate']:
            start=time.perf_counter();cpu=time.process_time()
            payload=event_response(task);built=time.perf_counter()
            if variant=='candidate':payload=pack(payload)
            packed=time.perf_counter()
            encoded=jsonable_encoder(payload);model_encoded=time.perf_counter()
            body=JSONResponse(encoded).body;done=time.perf_counter()
            row={'case':label,'repeat':repeat,'variant':variant,'component_seconds':done-start,'cpu_seconds':time.process_time()-cpu,'result_build_ms':(built-start)*1000,'dedupe_ms':(packed-built)*1000,'model_encode_ms':(model_encoded-packed)*1000,'json_encode_ms':(done-model_encoded)*1000,'events_bytes':len(body),'diff_count':len(task.diffs),'event_count':payload['event_count']}
            restored=unpack(payload) if variant=='candidate' else payload
            assert restored==expected,'Wire format changed fields, order, identifiers, or coordinates'
            row['serialization_semantics_equal']=True
            rows.append(row)
            if repeat==1:
                target=OUT/f'{label}-{variant}.json';target.write_bytes(body);target.chmod(0o600)
            (OUT/'report.json').write_text(json.dumps(rows,indent=2))
            print(label,variant,repeat,round(row['component_seconds'],3),len(body),flush=True)
            del payload,encoded,body,restored
