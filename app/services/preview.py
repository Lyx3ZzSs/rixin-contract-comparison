from __future__ import annotations

import json
from html import escape
from pathlib import Path

from app.models import CompareTask


def build_preview_html(task: CompareTask) -> str:
    diffs = []
    for diff in task.diffs:
        analysis = diff.ai_analysis
        diffs.append(
            {
                "diff_id": diff.diff_id,
                "type": diff.diff_type,
                "risk": analysis.risk_level if analysis else "LOW",
                "element": analysis.contract_element if analysis else "一般条款",
                "summary": analysis.change_summary if analysis else diff.readable_change,
                "suggestion": analysis.review_suggestion if analysis else "请人工复核。",
                "explanation": analysis.risk_explanation if analysis else "",
                "original_screenshot": _screenshot_url(task.task_id, diff.original_screenshot),
                "compare_screenshot": _screenshot_url(task.task_id, diff.compare_screenshot),
                "original_text": diff.original_snippet or diff.original_text[:500],
                "compare_text": diff.compare_snippet or diff.compare_text[:500],
            }
        )
    diffs_json = json.dumps(diffs, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>合同差异预览 {escape(task.task_id)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f9; color: #172033; }}
    header {{ display: flex; gap: 16px; align-items: center; justify-content: space-between; padding: 14px 18px; background: #ffffff; border-bottom: 1px solid #d9dee7; position: sticky; top: 0; z-index: 10; }}
    h1 {{ margin: 0; font-size: 18px; font-weight: 700; }}
    .stats {{ display: flex; gap: 10px; flex-wrap: wrap; font-size: 13px; }}
    .stat {{ padding: 4px 8px; border: 1px solid #d9dee7; border-radius: 6px; background: #fff; }}
    a.button {{ display: inline-flex; align-items: center; min-height: 32px; padding: 6px 10px; background: #1f6feb; color: white; text-decoration: none; border-radius: 6px; font-size: 13px; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 12px; padding: 12px; height: calc(100vh - 62px); }}
    .pdfs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; min-height: 0; }}
    .pane {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; overflow: hidden; min-height: 0; display: flex; flex-direction: column; }}
    .pane-title {{ padding: 8px 10px; font-size: 13px; font-weight: 700; border-bottom: 1px solid #d9dee7; background: #fbfcfe; }}
    iframe {{ width: 100%; height: 100%; border: 0; flex: 1; }}
    aside {{ display: flex; flex-direction: column; gap: 10px; min-height: 0; }}
    .list {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; overflow: auto; flex: 1; }}
    .diff-item {{ width: 100%; text-align: left; border: 0; border-bottom: 1px solid #eef1f5; background: white; padding: 10px; cursor: pointer; }}
    .diff-item:hover, .diff-item.active {{ background: #eef6ff; }}
    .meta {{ display: flex; gap: 6px; align-items: center; margin-bottom: 6px; font-size: 12px; }}
    .badge {{ border-radius: 999px; padding: 2px 7px; background: #edf2f7; }}
    .HIGH {{ background: #fee2e2; color: #991b1b; }}
    .MEDIUM {{ background: #fef3c7; color: #92400e; }}
    .LOW {{ background: #dcfce7; color: #166534; }}
    .summary {{ font-size: 13px; line-height: 1.45; color: #2b3445; }}
    .detail {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; padding: 10px; max-height: 46%; overflow: auto; }}
    .detail h2 {{ font-size: 15px; margin: 0 0 8px; }}
    .detail p {{ margin: 7px 0; font-size: 13px; line-height: 1.45; }}
    .shots {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .shot img {{ width: 100%; max-height: 180px; object-fit: contain; border: 1px solid #d9dee7; background: #fff; }}
    .empty {{ color: #6b7280; font-size: 13px; padding: 12px; }}
    @media (max-width: 1100px) {{
      main {{ grid-template-columns: 1fr; height: auto; }}
      .pdfs {{ height: 70vh; }}
      aside {{ min-height: 500px; }}
    }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .pdfs {{ grid-template-columns: 1fr; height: 100vh; }}
      .shots {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>任务 {escape(task.task_id)}</h1>
      <div class="stats">
        <span class="stat">差异 {task.diff_count}</span>
        <span class="stat">高 {task.high_risk_count}</span>
        <span class="stat">中 {task.medium_risk_count}</span>
        <span class="stat">低 {task.low_risk_count}</span>
      </div>
    </div>
    <a class="button" href="/api/compare/{escape(task.task_id)}/report">下载报告</a>
  </header>
  <main>
    <section class="pdfs">
      <div class="pane"><div class="pane-title">原合同高亮 PDF</div><iframe src="/api/compare/{escape(task.task_id)}/highlight/original"></iframe></div>
      <div class="pane"><div class="pane-title">对比合同高亮 PDF</div><iframe src="/api/compare/{escape(task.task_id)}/highlight/compare"></iframe></div>
    </section>
    <aside>
      <div class="list" id="diffList"></div>
      <div class="detail" id="detail"><div class="empty">请选择一条差异查看截图和 AI 分析。</div></div>
    </aside>
  </main>
  <script>
    const diffs = {diffs_json};
    const list = document.getElementById('diffList');
    const detail = document.getElementById('detail');
    const esc = (value) => String(value || '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    function renderList() {{
      if (!diffs.length) {{
        list.innerHTML = '<div class="empty">未发现差异。</div>';
        return;
      }}
      list.innerHTML = diffs.map((d, i) => `
        <button class="diff-item" data-index="${{i}}">
          <div class="meta">
            <strong>${{esc(d.diff_id)}}</strong>
            <span class="badge">${{esc(d.type)}}</span>
            <span class="badge ${{esc(d.risk)}}">${{esc(d.risk)}}</span>
            <span class="badge">${{esc(d.element)}}</span>
          </div>
          <div class="summary">${{esc(d.summary)}}</div>
        </button>
      `).join('');
      list.querySelectorAll('.diff-item').forEach(btn => btn.addEventListener('click', () => selectDiff(Number(btn.dataset.index))));
    }}
    function shot(title, url) {{
      return `<div class="shot"><p>${{esc(title)}}</p>${{url ? `<img src="${{esc(url)}}" alt="${{esc(title)}}">` : '<div class="empty">无截图</div>'}}</div>`;
    }}
    function selectDiff(index) {{
      const d = diffs[index];
      list.querySelectorAll('.diff-item').forEach((btn, i) => btn.classList.toggle('active', i === index));
      detail.innerHTML = `
        <h2>${{esc(d.diff_id)}} AI 分析</h2>
        <div class="shots">${{shot('原合同', d.original_screenshot)}}${{shot('对比合同', d.compare_screenshot)}}</div>
        <p><strong>摘要：</strong>${{esc(d.summary)}}</p>
        <p><strong>风险说明：</strong>${{esc(d.explanation)}}</p>
        <p><strong>复核建议：</strong>${{esc(d.suggestion)}}</p>
        <p><strong>原文片段：</strong>${{esc(d.original_text)}}</p>
        <p><strong>对比片段：</strong>${{esc(d.compare_text)}}</p>
      `;
    }}
    renderList();
    if (diffs.length) selectDiff(0);
  </script>
</body>
</html>"""


def _screenshot_url(task_id: str, screenshot_path: str) -> str:
    if not screenshot_path:
        return ""
    return f"/api/compare/{task_id}/screenshot/{Path(screenshot_path).name}"

