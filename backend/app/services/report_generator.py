from __future__ import annotations

import re
from datetime import datetime
from html import escape
from pathlib import Path

import fitz
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import settings
from app.models import CompareTask, DiffItem


def build_report_title(task: CompareTask) -> str:
    heading = _extract_pdf_heading(task.original_pdf_path) or _extract_pdf_heading(task.compare_pdf_path)
    if not heading:
        heading = Path(task.original_filename or task.compare_filename or "合同").stem
    return f"{_clean_report_text(heading, 60)}差异分析报告"


def build_report_filename(task: CompareTask) -> str:
    title = build_report_title(task)
    safe_title = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title).strip(" ._") or "合同差异分析报告"
    return f"{safe_title}.pdf"


class ReportGenerator:
    def generate(self, task: CompareTask, output_path: str | Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        font_name = self._register_font()
        self._font_name = font_name
        styles = self._styles(font_name)
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=1.5 * cm,
            leftMargin=1.5 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.5 * cm,
        )

        report_title = build_report_title(task)
        story = [
            Paragraph(escape(report_title), styles["Title"]),
            Spacer(1, 0.7 * cm),
            Paragraph(f"任务编号：{escape(task.task_id)}", styles["Normal"]),
            Paragraph(f"原合同：{escape(task.original_filename)}", styles["Normal"]),
            Paragraph(f"对比合同：{escape(task.compare_filename)}", styles["Normal"]),
            Paragraph(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]),
            Spacer(1, 0.5 * cm),
            Paragraph("审计统计", styles["Heading2"]),
            self._audit_items_table(task, styles),
        ]

        doc.build(story)
        return output_path

    def _register_font(self) -> str:
        candidates = [settings.report_font_path] if settings.report_font_path else []
        candidates.extend(
            [
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            ]
        )
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                try:
                    pdfmetrics.registerFont(TTFont("ContractCJK", candidate))
                    return "ContractCJK"
                except Exception:
                    continue
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            return "STSong-Light"
        except Exception:
            pass
        return "Helvetica"

    def _styles(self, font_name: str) -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        styles = {
            "Title": ParagraphStyle(
                "ContractTitle",
                parent=base["Title"],
                fontName=font_name,
                fontSize=24,
                leading=32,
                alignment=TA_CENTER,
            ),
            "Heading2": ParagraphStyle(
                "ContractHeading2",
                parent=base["Heading2"],
                fontName=font_name,
                fontSize=14,
                leading=20,
                spaceBefore=8,
                spaceAfter=6,
            ),
            "Normal": ParagraphStyle(
                "ContractNormal",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=9.5,
                leading=15,
            ),
            "Small": ParagraphStyle(
                "ContractSmall",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=8,
                leading=11,
            ),
        }
        return styles

    def _audit_items_table(self, task: CompareTask, styles: dict[str, ParagraphStyle]) -> Table:
        data = [["序号", "类型", "来源段落", "原文", "修改后"]]
        if not task.diffs:
            data.append(["-", "-", "-", "未发现改动点。", "未发现改动点。"])
        for index, diff in enumerate(task.diffs, start=1):
            data.append(
                [
                    str(index),
                    self._diff_type_label(diff.diff_type),
                    Paragraph(escape(_clean_report_text(self._source_label(diff), 120)), styles["Small"]),
                    Paragraph(escape(_clean_report_text(self._side_text(diff, "original"), 260)), styles["Small"]),
                    Paragraph(escape(_clean_report_text(self._side_text(diff, "compare"), 260)), styles["Small"]),
                ]
            )
        table = Table(data, colWidths=[1.0 * cm, 1.4 * cm, 4.1 * cm, 5.5 * cm, 5.5 * cm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                    ("FONTNAME", (0, 0), (-1, -1), getattr(self, "_font_name", "Helvetica")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D5DB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    def _source_label(self, diff: DiffItem) -> str:
        paragraph = self._paragraph_label(diff)
        page_label = self._page_label(diff)
        return f"{paragraph} · {page_label}"

    def _paragraph_label(self, diff: DiffItem) -> str:
        if diff.clause_no and diff.title:
            return f"{diff.clause_no} {diff.title}"
        return diff.title or diff.clause_no or diff.diff_id

    def _page_label(self, diff: DiffItem) -> str:
        original_pages = self._pages(diff, "original")
        compare_pages = self._pages(diff, "compare")
        if diff.diff_type == "ADD":
            return f"新版第 {compare_pages} 页" if compare_pages else "新版页码未定位"
        if diff.diff_type == "DELETE":
            return f"原文第 {original_pages} 页" if original_pages else "原文页码未定位"
        if original_pages and compare_pages:
            return f"原文第 {original_pages} 页 / 新版第 {compare_pages} 页"
        if original_pages:
            return f"原文第 {original_pages} 页 / 新版页码未定位"
        if compare_pages:
            return f"原文页码未定位 / 新版第 {compare_pages} 页"
        return "页码未定位"

    def _pages(self, diff: DiffItem, side: str) -> str:
        evidence = diff.original_evidence if side == "original" else diff.compare_evidence
        pages = sorted({box.page_no for box in evidence if box.page_no})
        return "、".join(str(page) for page in pages)

    def _side_text(self, diff: DiffItem, side: str) -> str:
        if diff.diff_type == "ADD" and side == "original":
            return "（原文无对应内容）"
        if diff.diff_type == "DELETE" and side == "compare":
            return "（新版已删除）"

        evidence_text = self._evidence_text(diff, side)
        if evidence_text:
            return evidence_text
        if side == "original":
            return diff.original_snippet or diff.original_text or "（未定位到原文片段）"
        return diff.compare_snippet or diff.compare_text or "（未定位到新版片段）"

    def _evidence_text(self, diff: DiffItem, side: str) -> str:
        evidence = diff.original_evidence if side == "original" else diff.compare_evidence
        typed = [box.text for box in evidence if box.highlight_type == diff.diff_type and box.text.strip()]
        if typed:
            return " ".join(text.strip() for text in typed)
        fallback = [box.text for box in evidence if box.highlight_type is None and box.text.strip()]
        if fallback:
            return " ".join(text.strip() for text in fallback)
        if diff.diff_type == "MODIFY":
            any_text = [box.text for box in evidence if box.text.strip()]
            return " ".join(text.strip() for text in any_text)
        return ""

    def _diff_type_label(self, diff_type: str) -> str:
        return {"ADD": "新增", "DELETE": "删除", "MODIFY": "修改"}.get(diff_type, diff_type)


def _extract_pdf_heading(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    try:
        pdf = fitz.open(path)
    except Exception:
        return ""
    try:
        if len(pdf) == 0:
            return ""
        page = pdf[0]
        blocks = sorted(page.get_text("blocks"), key=lambda block: (float(block[1]), float(block[0])))
        for block in blocks:
            text = str(block[4] if len(block) > 4 else "")
            for line in text.splitlines():
                heading = _clean_report_text(line, 60)
                if heading:
                    return heading
    finally:
        pdf.close()
    return ""


def _clean_report_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text
