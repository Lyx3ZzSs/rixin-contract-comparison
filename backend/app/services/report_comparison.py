"""Readable comparison cards; presentation never changes stored diff identities or text."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from html import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from app.models import TextRange


def display_ranges(item) -> tuple[list[TextRange], list[TextRange]]:
    original, compare = item.original_change_ranges, item.compare_change_ranges
    if item.diff_type != "MODIFY" or original or compare:
        return original, compare
    # Old results may lack character ranges. Align literal text for display only.
    if max(len(item.original_text), len(item.compare_text)) > 20_000:
        return [], []
    original, compare = [], []
    for tag, a, b, c, d in SequenceMatcher(None, item.original_text, item.compare_text).get_opcodes():
        if tag != "equal":
            if a < b:
                original.append(TextRange(start=a, end=b, highlight_type="DELETE"))
            if c < d:
                compare.append(TextRange(start=c, end=d, highlight_type="ADD"))
    return _whole_numbers(item.original_text, original), _whole_numbers(item.compare_text, compare)


def _whole_numbers(text, ranges):
    # Highlight changed numeric values as units, e.g. 80% -> 90%, not just 8 -> 9.
    tokens = list(re.finditer(r"[0-9]+(?:[.,][0-9]+)*(?:[%％])?", text))
    return [r.model_copy(update={
        "start": min([r.start] + [m.start() for m in tokens if m.start() < r.end and m.end() > r.start]),
        "end": max([r.end] + [m.end() for m in tokens if m.start() < r.end and m.end() > r.start]),
    }) for r in ranges]


def text_chunks(text: str, limit: int = 240) -> list[tuple[int, str]]:
    """Bound row height without dropping characters; prefer existing line/sentence ends."""
    result = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            candidates = [text.rfind(mark, start + limit // 2, end) for mark in ("\n", "。", "；", ";", ". ")]
            boundary = max(candidates)
            if boundary >= 0:
                end = boundary + 1
        result.append((start, text[start:end]))
        start = end
    return result


def highlighted_text(text: str, offset: int, ranges: list[TextRange], side: str, whole: bool = False) -> str:
    end = offset + len(text)
    if whole:
        return escape(text).replace("\n", "<br/>") or " "
    intervals = sorted(
        (max(offset, r.start), min(end, r.end)) for r in ranges if r.end > offset and r.start < end
    )
    parts, pos = [], offset
    color, background = ("#9F2929", "#FDEDED") if side == "original" else ("#14613F", "#EAF6EE")
    for start, stop in intervals:
        start = max(pos, start)
        if start >= stop:
            continue
        parts.append(escape(text[pos - offset:start - offset]))
        parts.append(f'<font color="{color}" backColor="{background}"><b><u>{escape(text[start - offset:stop - offset])}</u></b></font>')
        pos = stop
    parts.append(escape(text[pos - offset:]))
    return "".join(parts).replace("\n", "<br/>") or " "


def comparison_cards(indexed_items, styles, width: float, page_label):
    body = ParagraphStyle("ComparisonBody", parent=styles["Normal"], wordWrap="CJK", leading=16)
    heading = ParagraphStyle("ComparisonHeading", parent=styles["Heading3"], fontSize=11, leading=17)
    column = ParagraphStyle("ComparisonColumn", parent=styles["Normal"], fontSize=9, leading=14)
    review_labels = {"UNREVIEWED": "未审核", "CONFIRMED": "确认差异", "FALSE_POSITIVE": "误报",
                     "NEEDS_REVIEW": "待复核", "IGNORED": "忽略"}
    labels = {"ADD": "新增", "DELETE": "删除", "MODIFY": "修改"}
    result = []
    for index, item in indexed_items:
        # A one-sided change uses full width; modifications always show both versions together.
        sides = ["compare"] if item.diff_type == "ADD" else ["original"] if item.diff_type == "DELETE" else ["original", "compare"]
        unresolved = item.quality_status == "NEEDS_REVIEW" or item.change_decision != "CONFIRMED"
        direction = f"待复核·疑似{labels[item.diff_type]}" if unresolved and len(sides) == 1 else labels[item.diff_type]
        title = f"{index:02d}  {direction} | {item.title}"
        review = f"审核：{review_labels.get(item.review_status, item.review_status)}"
        if item.quality_status == "NEEDS_REVIEW":
            review += "；内容识读或变化证据需复核"
        header = [Paragraph(escape(title), heading), Paragraph(escape(f"{page_label(item)} · {review}"), styles["Small"])]
        if item.source_type in {'seal', 'header_footer'} and item.summary and item.summary != item.title:
            header.append(Paragraph(escape(item.summary), styles['Small']))
        if len(sides) == 1:
            absence = ("原文对应内容未确认" if item.diff_type == "ADD" else "新版对应内容未确认") if unresolved else (
                "原文无对应新增内容" if item.diff_type == "ADD" else "新版无对应保留内容")
            header.append(Paragraph(absence, styles["Small"]))
        original_ranges, compare_ranges = display_ranges(item)
        if item.diff_type == "MODIFY" and not original_ranges and not compare_ranges:
            header.append(Paragraph("未提供精确文字变化片段，请结合两侧内容核对。", styles["Small"]))
        rows = [[header] + [""] * (len(sides) - 1)]
        rows.append([Paragraph("原文（删除或替换前）" if side == "original" else "新版（新增或替换后）", column) for side in sides])
        chunks = [text_chunks(getattr(item, f"{side}_text")) for side in sides]
        for n in range(max(1, *(len(parts) for parts in chunks))):
            row = []
            for side, parts in zip(sides, chunks):
                if n < len(parts):
                    offset, text = parts[n]
                    ranges = original_ranges if side == "original" else compare_ranges
                    row.append(Paragraph(highlighted_text(text, offset, ranges, side, len(sides) == 1), body))
                else:
                    row.append(Paragraph("未提取到可读文字，请对照原文。" if not parts and n == 0 else " ", styles["Small"]))
            rows.append(row)
        table = Table(rows, colWidths=[width / len(sides)] * len(sides), repeatRows=2, hAlign="LEFT")
        commands = [
            ("SPAN", (0, 0), (-1, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F4F8")),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#D8E0EA")),
            ("LINEBELOW", (0, 1), (-1, 1), .5, colors.HexColor("#D8E0EA")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
        if len(sides) == 1:
            commands.append(("BACKGROUND", (0, 2), (-1, -1), colors.HexColor("#F0F8F3" if sides[0] == "compare" else "#FFF4F3")))
        if len(sides) == 2:
            commands.append(("LINEAFTER", (0, 1), (0, -1), .5, colors.HexColor("#D8E0EA")))
        table.setStyle(TableStyle(commands))
        result.append(table)
        result.append(Paragraph(escape(f"差异编号：{item.item_id}"), styles["Small"]))
        result.append(Spacer(1, 12))
    return result or [Paragraph("未发现差异。", styles["Normal"])]
