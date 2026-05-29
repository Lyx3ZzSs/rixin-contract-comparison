from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path

import fitz
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
            Spacer(1, 0.45 * cm),
            self._metadata_table(task, styles),
            Spacer(1, 0.5 * cm),
            Paragraph("审计统计与差异概览", styles["Heading2"]),
            self._summary_table(task, styles),
            Spacer(1, 0.35 * cm),
            Paragraph("差异明细", styles["Heading2"]),
            *self._diff_cards(task, styles),
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
                fontSize=21,
                leading=29,
                alignment=TA_CENTER,
                textColor=colors.HexColor("#111827"),
            ),
            "Heading2": ParagraphStyle(
                "ContractHeading2",
                parent=base["Heading2"],
                fontName=font_name,
                fontSize=13,
                leading=20,
                spaceBefore=8,
                spaceAfter=8,
                textColor=colors.HexColor("#111827"),
            ),
            "Normal": ParagraphStyle(
                "ContractNormal",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=9.5,
                leading=15,
                textColor=colors.HexColor("#263238"),
            ),
            "Small": ParagraphStyle(
                "ContractSmall",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=8,
                leading=11,
                textColor=colors.HexColor("#374151"),
            ),
            "MetaLabel": ParagraphStyle(
                "ContractMetaLabel",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=8,
                leading=12,
                textColor=colors.HexColor("#6B7280"),
            ),
            "CardTitle": ParagraphStyle(
                "ContractCardTitle",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=10,
                leading=14,
                textColor=colors.HexColor("#111827"),
                spaceAfter=2,
            ),
            "CardMeta": ParagraphStyle(
                "ContractCardMeta",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=8,
                leading=12,
                textColor=colors.HexColor("#4B5563"),
            ),
            "FieldLabel": ParagraphStyle(
                "ContractFieldLabel",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=8,
                leading=11,
                textColor=colors.HexColor("#6B7280"),
            ),
            "DiffText": ParagraphStyle(
                "ContractDiffText",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=8.5,
                leading=13,
                textColor=colors.HexColor("#111827"),
                alignment=TA_LEFT,
            ),
        }
        return styles

    def _metadata_table(self, task: CompareTask, styles: dict[str, ParagraphStyle]) -> Table:
        rows = [
            [Paragraph("任务编号", styles["MetaLabel"]), Paragraph(escape(task.task_id), styles["Normal"])],
            [Paragraph("原合同", styles["MetaLabel"]), Paragraph(escape(task.original_filename), styles["Normal"])],
            [Paragraph("对比合同", styles["MetaLabel"]), Paragraph(escape(task.compare_filename), styles["Normal"])],
            [Paragraph("生成时间", styles["MetaLabel"]), Paragraph(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), styles["Normal"])],
        ]
        table = Table(rows, colWidths=[2.0 * cm, 15.0 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E5E7EB")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#EEF2F7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    def _summary_table(self, task: CompareTask, styles: dict[str, ParagraphStyle]) -> Table:
        counts = Counter(diff.diff_type for diff in task.diffs)
        items = [
            ("全部差异", len(task.diffs), "#111827"),
            ("新增", counts["ADD"], "#15804F"),
            ("删除", counts["DELETE"], "#C9362C"),
            ("修改", counts["MODIFY"], "#A96300"),
        ]
        row = []
        for label, value, color in items:
            cell = [
                Paragraph(escape(label), styles["MetaLabel"]),
                Paragraph(f'<font color="{color}" size="16"><b>{value}</b></font>', styles["Normal"]),
            ]
            row.append(cell)
        table = Table([row], colWidths=[4.05 * cm, 4.05 * cm, 4.05 * cm, 4.05 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E5E7EB")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E5E7EB")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        return table

    def _diff_cards(self, task: CompareTask, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
        if not task.diffs:
            return [Paragraph("未发现改动点。", styles["Normal"])]
        flowables: list[Flowable] = []
        for index, diff in enumerate(task.diffs, start=1):
            flowables.append(self._diff_card(index, diff, styles))
            flowables.append(Spacer(1, 0.22 * cm))
        return flowables

    def _diff_card(self, index: int, diff: DiffItem, styles: dict[str, ParagraphStyle]) -> Table:
        type_label = self._diff_type_label(diff.diff_type)
        palette = self._diff_palette(diff.diff_type)
        header = Paragraph(
            escape(f"{index:02d} · {diff.diff_id} · {type_label}"),
            styles["CardTitle"],
        )
        source = Paragraph(f"<b>来源段落：</b>{escape(self._source_label(diff))}", styles["CardMeta"])
        original = self._text_panel("原文", self._side_text(diff, "original"), styles, "#F8FAFC")
        compare = self._text_panel("修改后", self._side_text(diff, "compare"), styles, "#F8FAFC")
        body = Table([[original, compare]], colWidths=[7.82 * cm, 7.82 * cm])
        body.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        card = Table(
            [
                [[header, source]],
                [body],
            ],
            colWidths=[16.2 * cm],
        )
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(palette["background"])),
                    ("LINEABOVE", (0, 0), (-1, 0), 1.8, colors.HexColor(palette["accent"])),
                    ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#D9E1E7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, 0), 9),
                    ("RIGHTPADDING", (0, 0), (-1, 0), 9),
                    ("TOPPADDING", (0, 0), (-1, 0), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                    ("LEFTPADDING", (0, 1), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 1), (-1, -1), 9),
                    ("TOPPADDING", (0, 1), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 9),
                ]
            )
        )
        return card

    def _text_panel(self, label: str, value: str, styles: dict[str, ParagraphStyle], background: str) -> Table:
        table = Table(
            [
                [Paragraph(escape(label), styles["FieldLabel"])],
                [Paragraph(escape(_clean_report_text(value, 700)), styles["DiffText"])],
            ],
            colWidths=[7.55 * cm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(background)),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    def _diff_palette(self, diff_type: str) -> dict[str, str]:
        return {
            "ADD": {"accent": "#15804F", "background": "#EEF8F2"},
            "DELETE": {"accent": "#C9362C", "background": "#FFF1EF"},
            "MODIFY": {"accent": "#A96300", "background": "#FFF7E6"},
        }.get(diff_type, {"accent": "#4B5563", "background": "#F8FAFC"})

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

        snippet = diff.original_snippet if side == "original" else diff.compare_snippet
        if diff.diff_type == "MODIFY" and (not diff.original_snippet or not diff.compare_snippet):
            side_text = diff.original_text if side == "original" else diff.compare_text
            if side_text:
                return side_text
        if snippet:
            return snippet
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
