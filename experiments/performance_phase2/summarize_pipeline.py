"""Numeric, body-free summary using the original probe's intervals and RSS."""
import json
from pathlib import Path
import sys

def summarize(path):
    path=Path(path)
    rows=[json.loads(line) for line in (path/'spans.jsonl').read_text().splitlines()]
    http=[r for r in rows if r['kind']=='http']
    intervals=sorted((r['start'],r['end']) for r in http)
    union=0.;end=0.
    for start,finish in intervals:
        union+=max(0.,finish-max(start,end));end=max(end,finish)
    comparisons=[r for r in rows if r['kind']=='comparison']
    comparison=comparisons[-1] if comparisons else {}
    result=json.loads((path/'result.json').read_text()) if (path/'result.json').exists() else {}
    performance=result.get('metrics',{}).get('performance',{})
    return dict(path=str(path),completed=bool(result),pipeline_seconds=result.get('pipeline_seconds'),
        cpu_seconds=comparison.get('cpu_seconds'),rss_peak_mib=comparison.get('peak_rss_bytes',0)/1024**2 if comparison else None,
        remote_count=len(http),remote_union_seconds=union,
        ocr_count=sum(r['endpoint']=='/ocr' for r in http),layout_count=sum(r['endpoint']=='/layout-parsing' for r in http),
        stages={r['stage']:r['seconds'] for r in rows if r['kind']=='stage'},
        events_bytes=len(json.dumps(result['events'],ensure_ascii=False,separators=(',',':')).encode()) if result else None,
        diff_count=len(result.get('diffs',[])) if result else None,event_count=result.get('events',{}).get('event_count'),
        alignment_counters=performance.get('page_alignment',{}).get('counters',{}),
        matching_counters=performance.get('matching',{}).get('counters',{}),
        cache_hits=result.get('cache_hits'),cache_misses=result.get('cache_misses'))

if __name__=='__main__':
    root=Path(sys.argv[1]);paths=sorted(p.parent for p in root.rglob('spans.jsonl'))
    (root/'numeric-summary.json').write_text(json.dumps([summarize(p) for p in paths],indent=2,ensure_ascii=False))
