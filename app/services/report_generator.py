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
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import settings
from app.models import CompareTask
from app.services.audit_summary import AuditItem, build_audit_items


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

        story.append(PageBreak())
        story.append(Paragraph("合同差异", styles["Heading2"]))
        story.extend(self._page_screenshot_detail(task, styles))

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
        items = build_audit_items(task.diffs)
        data = [["序号", "类型", "页码", "条款/标题", "改动内容"]]
        if not items:
            data.append(["-", "-", "-", "-", "未发现改动点。"])
        for index, item in enumerate(items, start=1):
            data.append(
                [
                    str(index),
                    self._diff_type_label(item.diff_type),
                    self._audit_item_page(item),
                    Paragraph(escape(_clean_report_text(item.title, 50)), styles["Small"]),
                    Paragraph(escape(_clean_report_text(item.summary, 220)), styles["Small"]),
                ]
            )
        table = Table(data, colWidths=[1.0 * cm, 1.4 * cm, 1.2 * cm, 3.5 * cm, 9.4 * cm], repeatRows=1)
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

    def _audit_item_page(self, item: AuditItem) -> str:
        if item.diff_type == "ADD":
            evidence = item.diff.compare_evidence
        elif item.diff_type == "DELETE":
            evidence = item.diff.original_evidence
        else:
            evidence = [*item.diff.original_evidence, *item.diff.compare_evidence]
        pages = sorted({box.page_no for box in evidence if box.highlight_type in {item.diff_type, None}})
        return "、".join(str(page) for page in pages) if pages else "-"

    def _diff_type_label(self, diff_type: str) -> str:
        return {"ADD": "新增", "DELETE": "删除", "MODIFY": "修改"}.get(diff_type, diff_type)

    def _page_screenshot_detail(self, task: CompareTask, styles: dict[str, ParagraphStyle]) -> list:
        story: list = []
        max_pages = max(len(task.original_page_screenshots), len(task.compare_page_screenshots))
        if max_pages == 0:
            return [Paragraph("暂无页面截图。", styles["Normal"])]
        for index in range(max_pages):
            story.append(Paragraph(f"第 {index + 1} 页", styles["Heading2"]))
            if index < len(task.original_page_screenshots) and Path(task.original_page_screenshots[index]).exists():
                story.append(Paragraph("原版合同高亮截图", styles["Normal"]))
                story.append(Image(task.original_page_screenshots[index], width=15 * cm, height=20 * cm, kind="proportional"))
                story.append(Spacer(1, 0.2 * cm))
            if index < len(task.compare_page_screenshots) and Path(task.compare_page_screenshots[index]).exists():
                story.append(Paragraph("新版合同高亮截图", styles["Normal"]))
                story.append(Image(task.compare_page_screenshots[index], width=15 * cm, height=20 * cm, kind="proportional"))
                story.append(Spacer(1, 0.3 * cm))
        return story


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
