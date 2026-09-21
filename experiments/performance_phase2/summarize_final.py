"""Final A-E comparison of the only active performance patch (animation).

Algorithm/cache/wire experiments stay disabled and are reported separately.
This uses the original browser readiness boundary and all three alternating runs.
"""
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/analysis/perf-phase2-20260921'
data = json.loads((OUT / 'animation/comparison.json').read_text())
rows = []
for case, group in data.items():
    metrics = group['metrics_p50']
    for key in ('E2E', 'ExtractionStage', 'PageAlignmentStage', 'MatchStage', 'events_bytes', 'CPU', 'RSS_MiB', 'remote_calls', 'remote_union'):
        rows.append(dict(case=case, metric=key, **metrics[key]))
    for mark in ('renderEnd', 'firstResultDOM', 'visibleCanvasReady'):
        values = []
        for variant in ('baseline', 'candidate'):
            runs = [json.loads(p.read_text()) for p in (OUT / 'animation' / variant).glob(f'{case}-r*-browser.json')]
            assert len(runs) == 3
            values.append(statistics.median((r['marks'][mark]-r['marks']['completedReceived'])/1000 for r in runs))
        a, b = values
        rows.append(dict(case=case, metric='terminal_to_'+mark, baseline=a, candidate=b,
                         absolute_delta=b-a, percentage_delta=(b-a)/a*100 if a>0 else None))
(OUT / 'final-benchmark.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
lines = ['# 最终 A–E 对照：生产代码中保留的动画解耦', '',
         '前提：双方均含OpenCV兼容修复，未启用native、页对齐剪枝、评分cutoff、跨任务cache或events v2。算法实验收益不能与下表相加。每组3次交替，数值为P50；E2E终点沿用第一阶段。', '',
         '`terminal_to_renderEnd`为终态收到至全部事件DOM与两个ready canvas加两帧；`terminal_to_firstResultDOM`为首个结果节点；`terminal_to_visibleCanvasReady`可为负数，因为PDF预览在终态前已显示，该项不计算收益百分比。', '',
         '|Case|指标|Before|After|Delta|Delta %|', '|---|---|---:|---:|---:|---:|']
for row in rows:
    pct = '—' if row['percentage_delta'] is None else f"{row['percentage_delta']:+.2f}%"
    lines.append(f"|{row['case']}|{row['metric']}|{row['baseline']:.4f}|{row['candidate']:.4f}|{row['absolute_delta']:+.4f}|{pct}|")
lines += ['', '单位：时间为秒，RSS为MiB，events为bytes。C整体E2E增加已保留；其它阶段的波动不能归因于前端动画。', '',
          '语义：13/15组完整持久化结果在task-scoped事件ID双射归一化后相等；E-r2/r3有11处远端rerank及派生分数差异，定位/分类/内容/事件分组/审核标识未变。原始差异在animation/remote-score-differences.json。完整Golden仍须独立查看，不以此表宣称全绿。', '']
(ROOT / 'experiments/performance_phase2/final-benchmark.md').write_text('\n'.join(lines))
