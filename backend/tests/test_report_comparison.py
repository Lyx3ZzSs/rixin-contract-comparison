from types import SimpleNamespace

import fitz

from app.models import CompareTask, DiffItem, TextRange
from app.services.report_comparison import display_ranges, highlighted_text, text_chunks
from app.services.report_generator import ReportGenerator


def test_highlights_exact_replacement_and_escapes_contract_markup():
    item = SimpleNamespace(diff_type="MODIFY", original_text="应在30日内支付<款项>", compare_text="应在45日内支付<款项>",
                           original_change_ranges=[], compare_change_ranges=[])
    before, after = display_ranges(item)
    assert [(r.start, r.end) for r in before] == [(2, 4)]
    assert [(r.start, r.end) for r in after] == [(2, 4)]
    rendered = highlighted_text(item.original_text, 0, before, "original")
    assert '<b><u>30</u></b>' in rendered
    assert '&lt;款项&gt;' in rendered
    assert '<b><u>应在' not in rendered


def test_explicit_ranges_are_preserved_and_span_chunk_boundaries():
    text = "A" * 600
    chunks = text_chunks(text)
    assert ''.join(part for _, part in chunks) == text
    ranges = [TextRange(start=230, end=250)]
    assert '<b><u>' in highlighted_text(chunks[0][1], chunks[0][0], ranges, "original")
    assert '<b><u>' in highlighted_text(chunks[1][1], chunks[1][0], ranges, "original")
    item = SimpleNamespace(diff_type="MODIFY", original_change_ranges=ranges, compare_change_ranges=[])
    assert display_ranges(item) == (ranges, [])


def test_event_report_keeps_long_content_and_repeats_comparison_headers(tmp_path):
    original = "开始原文\n" + "甲方应在约定期限内提供设备并完成安装。\n" * 160 + "原文终点"
    compare = "开始新版\n" + "乙方应在约定期限内提供设备并完成调试。\n" * 160 + "新版终点"
    task = CompareTask(task_id="report-example", status="COMPLETED", diffs=[
        DiffItem(diff_id="long", diff_type="MODIFY", title="交付要求", original_text=original, compare_text=compare),
        DiffItem(diff_id="add", diff_type="ADD", title="新增义务", compare_text="应提供培训。"),
    ])
    before = task.model_dump()
    path = ReportGenerator().generate(task, tmp_path / "report.pdf", event_view=True)
    assert task.model_dump() == before
    with fitz.open(path) as pdf:
        text = '\n'.join(page.get_text() for page in pdf)
        compact = ''.join(text.split())
        assert len(pdf) > 2
        assert text.count("原文（删除或替换前）") > 1
        for marker in ("开始原文", "原文终点", "开始新版", "新版终点", "应提供培训。"):
            assert marker in compact
        assert "原文无对应新增内容" in compact
        assert "bbox" not in text
        for page in pdf:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        x0, y0, x1, y1 = span["bbox"]
                        assert x0 >= 0 and y0 >= 0 and x1 <= page.rect.width + 1 and y1 <= page.rect.height + 1


def test_fallback_highlights_changed_numeric_values_as_complete_units():
    item = SimpleNamespace(diff_type="MODIFY", original_text="支付80%", compare_text="支付90%",
                           original_change_ranges=[], compare_change_ranges=[])
    before, after = display_ranges(item)
    assert [(r.start, r.end) for r in before] == [(2, 5)]
    assert [(r.start, r.end) for r in after] == [(2, 5)]


def test_report_links_use_event_export_for_records_and_detail():
    from app.api_presenters import compare_task_artifact_urls, compare_record_summary, compare_record_summary_from_index

    task = CompareTask(task_id="x", status="COMPLETED")
    expected = "/api/compare/x/events/report"
    assert compare_task_artifact_urls(task)["report_url"] == expected
    assert compare_record_summary(task).report_url == expected
    assert compare_record_summary_from_index({"task_id": "x", "status": "COMPLETED"}).report_url == expected
