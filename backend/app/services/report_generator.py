from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
from io import BytesIO
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
from reportlab.platypus import Flowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import settings
from app.models import CompareTask, DiffItem, EvidenceBox
from app.services.audit_summary import AuditItem, build_audit_items


@dataclass(frozen=True)
class ReportEvidenceImage:
    data: bytes
    width: float
    height: float


def build_report_title(task: CompareTask) -> str:
    heading = _extract_pdf_heading(task.original_pdf_path) or _extract_pdf_heading(task.compare_pdf_path)
    if not heading:
        heading = Path(task.original_filename or task.compare_filename or "合同").stem
    return f"{_clean_report_text(heading, 60)}差异分析报告"


def build_report_filename(task: CompareTask) -> str:
    title = build_report_title(task)
    safe_title = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title).strip(" ._") or "合同差异分析报告"
    return f"{safe_title}.pdf"


def _visible_audit_items(task: CompareTask) -> list[AuditItem]:
    visible_items: list[AuditItem] = []
    for item in build_audit_items(task.diffs):
        review = task.audit_item_reviews.get(item.item_id)
        if review is None or review.review_status != "IGNORED":
            visible_items.append(item)
    return visible_items


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
        audit_items = _visible_audit_items(task)
        indexed_items = self._indexed_items(audit_items)
        self._current_original_pdf_path = task.original_pdf_path
        self._current_compare_pdf_path = task.compare_pdf_path
        story = [
            Paragraph(escape(report_title), styles["Title"]),
            Spacer(1, 0.45 * cm),
            self._metadata_table(task, styles),
            Spacer(1, 0.5 * cm),
            Paragraph("审计统计与差异概览", styles["Heading2"]),
            self._summary_table(audit_items, styles),
            Spacer(1, 0.35 * cm),
            Paragraph("差异类型与证据说明", styles["Heading2"]),
            self._legend_table(styles),
            Spacer(1, 0.35 * cm),
            Paragraph("差异索引", styles["Heading2"]),
            self._index_table(indexed_items, styles),
            Spacer(1, 0.35 * cm),
            Paragraph("差异明细", styles["Heading2"]),
            *self._diff_cards(indexed_items, styles),
        ]

        doc.build(story, onFirstPage=self._page_footer, onLaterPages=self._page_footer)
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
            "Tiny": ParagraphStyle(
                "ContractTiny",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=6.8,
                leading=9,
                textColor=colors.HexColor("#6B7280"),
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
            "IndexHead": ParagraphStyle(
                "ContractIndexHead",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=7.5,
                leading=10,
                textColor=colors.HexColor("#FFFFFF"),
            ),
            "IndexCell": ParagraphStyle(
                "ContractIndexCell",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=7.5,
                leading=10.5,
                textColor=colors.HexColor("#1F2937"),
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

    def _page_footer(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(self._font_name, 7)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(doc.leftMargin, 0.8 * cm, "合同差异审计报告")
        canvas.drawRightString(A4[0] - doc.rightMargin, 0.8 * cm, f"第 {doc.page} 页")
        canvas.restoreState()

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

    def _summary_table(self, audit_items: list[AuditItem], styles: dict[str, ParagraphStyle]) -> Table:
        counts = Counter(item.diff_type for item in audit_items)
        items = [
            ("全部差异", len(audit_items), "#111827"),
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

    def _legend_table(self, styles: dict[str, ParagraphStyle]) -> Table:
        rows = [
            [
                Paragraph('<font color="#15804F"><b>新增</b></font>', styles["IndexCell"]),
                Paragraph("新版存在、原文无对应内容。", styles["IndexCell"]),
            ],
            [
                Paragraph('<font color="#C9362C"><b>删除</b></font>', styles["IndexCell"]),
                Paragraph("原文存在、新版已删除。", styles["IndexCell"]),
            ],
            [
                Paragraph('<font color="#A96300"><b>修改</b></font>', styles["IndexCell"]),
                Paragraph("同一位置或语义对应内容发生变化。", styles["IndexCell"]),
            ],
        ]
        table = Table(rows, colWidths=[2.4 * cm, 13.8 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table

    def _index_table(self, indexed_items: list[tuple[int, AuditItem]], styles: dict[str, ParagraphStyle]) -> Table:
        if not indexed_items:
            return Table([[Paragraph("未发现可定位审计点。", styles["Normal"])]], colWidths=[16.2 * cm])
        rows = [
            [
                Paragraph("序号", styles["IndexHead"]),
                Paragraph("差异编号", styles["IndexHead"]),
                Paragraph("类型", styles["IndexHead"]),
                Paragraph("来源", styles["IndexHead"]),
                Paragraph("页码", styles["IndexHead"]),
                Paragraph("摘要", styles["IndexHead"]),
            ]
        ]
        for index, item in indexed_items:
            rows.append(
                [
                    Paragraph(f"{index:02d}", styles["IndexCell"]),
                    Paragraph(escape(item.item_id), styles["IndexCell"]),
                    Paragraph(escape(self._diff_type_label(item.diff_type)), styles["IndexCell"]),
                    Paragraph(escape(self._source_type_label(item.diff.source_type)), styles["IndexCell"]),
                    Paragraph(escape(self._page_label(item)), styles["IndexCell"]),
                    Paragraph(escape(_clean_report_text(item.summary, 58)), styles["IndexCell"]),
                ]
            )
        table = Table(rows, colWidths=[1.0 * cm, 2.4 * cm, 1.4 * cm, 1.8 * cm, 3.6 * cm, 6.0 * cm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#FFFFFF")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    def _indexed_items(self, audit_items: list[AuditItem]) -> list[tuple[int, AuditItem]]:
        return [(index, item) for index, item in enumerate(audit_items, start=1)]

    def _diff_cards(self, indexed_items: list[tuple[int, AuditItem]], styles: dict[str, ParagraphStyle]) -> list[Flowable]:
        if not indexed_items:
            return [Paragraph("未发现可定位审计点。", styles["Normal"])]
        flowables: list[Flowable] = []
        for index, item in indexed_items:
            flowables.append(KeepTogether([self._diff_card(index, item, styles)]))
            flowables.append(Spacer(1, 0.22 * cm))
        return flowables

    def _diff_card(self, index: int, item: AuditItem, styles: dict[str, ParagraphStyle]) -> Table:
        type_label = self._diff_type_label(item.diff_type)
        palette = self._diff_palette(item.diff_type)
        header = Paragraph(
            escape(f"{index:02d} · {item.item_id} · {type_label}"),
            styles["CardTitle"],
        )
        source = Paragraph(
            f"<b>来源段落：</b>{escape(self._source_label(item))}　"
            f"<b>来源类型：</b>{escape(self._source_type_label(item.diff.source_type))}",
            styles["CardMeta"],
        )
        original = self._text_panel("原文", self._side_text(item, "original"), styles, "#F8FAFC")
        compare = self._text_panel("修改后", self._side_text(item, "compare"), styles, "#F8FAFC")
        original_evidence = self._evidence_panel("原文截图", item, "original", styles)
        compare_evidence = self._evidence_panel("新版截图", item, "compare", styles)
        body = Table(
            [
                [original, compare],
                [original_evidence, compare_evidence],
            ],
            colWidths=[7.82 * cm, 7.82 * cm],
        )
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

    def _evidence_panel(self, label: str, item: AuditItem, side: str, styles: dict[str, ParagraphStyle]) -> Table:
        evidence = item.original_evidence if side == "original" else item.compare_evidence
        if not evidence:
            if item.diff_type == "ADD" and side == "original":
                message = "原文无对应截图。"
            elif item.diff_type == "DELETE" and side == "compare":
                message = "新版无对应截图。"
            else:
                message = "未定位到可截图证据。"
            content: Flowable = Paragraph(message, styles["Small"])
        else:
            image = self._render_evidence_image(item, side)
            if image:
                max_width = 7.1 * cm
                max_height = 4.6 * cm
                ratio = min(max_width / image.width, max_height / image.height, 1)
                content = Image(BytesIO(image.data), width=image.width * ratio, height=image.height * ratio)
            else:
                content = Paragraph("截图生成失败，请以页码和文本证据复核。", styles["Small"])
        table = Table(
            [
                [Paragraph(escape(label), styles["FieldLabel"])],
                [content],
            ],
            colWidths=[7.55 * cm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
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

    def _render_evidence_image(self, item: AuditItem, side: str) -> ReportEvidenceImage | None:
        task_path = self._side_pdf_path(item, side)
        evidence_list = item.original_evidence if side == "original" else item.compare_evidence
        evidence = self._first_valid_evidence(evidence_list)
        if not task_path or not evidence:
            return None
        path = Path(task_path)
        if not path.exists():
            return None
        try:
            with fitz.open(path) as pdf:
                if evidence.page_no < 1 or evidence.page_no > len(pdf):
                    return None
                page = pdf[evidence.page_no - 1]
                crop_rect = self._crop_rect(page, evidence_list)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=crop_rect, alpha=False)
                image_bytes = self._draw_highlight(pix.tobytes("png"), crop_rect, evidence_list, item.diff_type)
                return ReportEvidenceImage(data=image_bytes, width=float(pix.width) * 0.45, height=float(pix.height) * 0.45)
        except Exception:
            return None

    def _side_pdf_path(self, item: AuditItem, side: str) -> str:
        # AuditItem keeps only the DiffItem; the PDF paths are attached temporarily while building cards.
        return getattr(self, "_current_original_pdf_path" if side == "original" else "_current_compare_pdf_path", "")

    def _first_valid_evidence(self, evidence_list: list[EvidenceBox]) -> EvidenceBox | None:
        for evidence in evidence_list:
            if evidence.page_no and evidence.bbox and evidence.bbox.x1 > evidence.bbox.x0 and evidence.bbox.y1 > evidence.bbox.y0:
                return evidence
        return None

    def _crop_rect(self, page, evidence_list: list[EvidenceBox]) -> fitz.Rect:
        boxes = [evidence.bbox for evidence in evidence_list if evidence.bbox and evidence.page_no]
        first_page_no = next((evidence.page_no for evidence in evidence_list if evidence.page_no), None)
        boxes = [evidence.bbox for evidence in evidence_list if evidence.page_no == first_page_no and evidence.bbox]
        x0 = min(box.x0 for box in boxes)
        y0 = min(box.y0 for box in boxes)
        x1 = max(box.x1 for box in boxes)
        y1 = max(box.y1 for box in boxes)
        page_rect = page.rect
        width = max(180, x1 - x0)
        height = max(80, y1 - y0)
        center_x = (x0 + x1) / 2
        center_y = (y0 + y1) / 2
        crop = fitz.Rect(
            center_x - width / 2 - 42,
            center_y - height / 2 - 36,
            center_x + width / 2 + 42,
            center_y + height / 2 + 46,
        )
        return crop & page_rect

    def _draw_highlight(self, image_bytes: bytes, crop_rect: fitz.Rect, evidence_list: list[EvidenceBox], diff_type: str) -> bytes:
        try:
            from PIL import Image as PILImage
            from PIL import ImageDraw
        except Exception:
            return image_bytes
        with PILImage.open(BytesIO(image_bytes)) as image:
            draw = ImageDraw.Draw(image, "RGBA")
            scale_x = image.width / max(1, crop_rect.width)
            scale_y = image.height / max(1, crop_rect.height)
            outline, fill = self._highlight_rgba(diff_type)
            first_page_no = next((evidence.page_no for evidence in evidence_list if evidence.page_no), None)
            for evidence in evidence_list:
                if evidence.page_no != first_page_no:
                    continue
                box = evidence.bbox
                rect = [
                    (box.x0 - crop_rect.x0) * scale_x,
                    (box.y0 - crop_rect.y0) * scale_y,
                    (box.x1 - crop_rect.x0) * scale_x,
                    (box.y1 - crop_rect.y0) * scale_y,
                ]
                draw.rectangle(rect, fill=fill, outline=outline, width=4)
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()

    def _highlight_rgba(self, diff_type: str) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        return {
            "ADD": ((21, 128, 79, 230), (21, 128, 79, 58)),
            "DELETE": ((201, 54, 44, 230), (201, 54, 44, 58)),
            "MODIFY": ((169, 99, 0, 230), (246, 196, 64, 70)),
        }.get(diff_type, ((75, 85, 99, 220), (75, 85, 99, 50)))

    def _diff_palette(self, diff_type: str) -> dict[str, str]:
        return {
            "ADD": {"accent": "#15804F", "background": "#EEF8F2"},
            "DELETE": {"accent": "#C9362C", "background": "#FFF1EF"},
            "MODIFY": {"accent": "#A96300", "background": "#FFF7E6"},
        }.get(diff_type, {"accent": "#4B5563", "background": "#F8FAFC"})

    def _source_label(self, item: AuditItem) -> str:
        diff = item.diff
        paragraph = self._paragraph_label(diff)
        page_label = self._page_label(item)
        return f"{paragraph} · {page_label}"

    def _paragraph_label(self, diff: DiffItem) -> str:
        if diff.clause_no and diff.title:
            return f"{diff.clause_no} {diff.title}"
        return diff.title or diff.clause_no or diff.diff_id

    def _page_label(self, item: AuditItem) -> str:
        original_pages = self._pages(item, "original")
        compare_pages = self._pages(item, "compare")
        if item.diff_type == "ADD":
            return f"新版第 {compare_pages} 页" if compare_pages else "新版页码未定位"
        if item.diff_type == "DELETE":
            return f"原文第 {original_pages} 页" if original_pages else "原文页码未定位"
        if original_pages and compare_pages:
            return f"原文第 {original_pages} 页 / 新版第 {compare_pages} 页"
        if original_pages:
            return f"原文第 {original_pages} 页 / 新版页码未定位"
        if compare_pages:
            return f"原文页码未定位 / 新版第 {compare_pages} 页"
        return "页码未定位"

    def _pages(self, item: AuditItem, side: str) -> str:
        evidence = item.original_evidence if side == "original" else item.compare_evidence
        pages = sorted({box.page_no for box in evidence if box.page_no})
        return "、".join(str(page) for page in pages)

    def _side_text(self, item: AuditItem, side: str) -> str:
        diff = item.diff
        if item.diff_type == "ADD" and side == "original":
            return "（原文无对应内容）"
        if item.diff_type == "DELETE" and side == "compare":
            return "（新版已删除）"

        evidence_text = self._audit_evidence_text(item, side)
        if evidence_text:
            return evidence_text
        snippet = diff.original_snippet if side == "original" else diff.compare_snippet
        if diff.diff_type == "MODIFY" and (not diff.original_snippet or not diff.compare_snippet):
            side_text = diff.original_text if side == "original" else diff.compare_text
            if side_text:
                return side_text
        if snippet:
            return snippet
        if side == "original":
            return diff.original_snippet or diff.original_text or "（未定位到原文片段）"
        return diff.compare_snippet or diff.compare_text or "（未定位到新版片段）"

    def _audit_evidence_text(self, item: AuditItem, side: str) -> str:
        evidence = item.original_evidence if side == "original" else item.compare_evidence
        return " ".join(box.text.strip() for box in evidence if box.text.strip())

    def _diff_type_label(self, diff_type: str) -> str:
        return {"ADD": "新增", "DELETE": "删除", "MODIFY": "修改"}.get(diff_type, diff_type)

    def _source_type_label(self, source_type: str) -> str:
        return {
            "clause": "条款",
            "table": "表格",
            "metadata": "封面",
            "seal": "印章",
        }.get(source_type or "clause", source_type or "条款")


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
