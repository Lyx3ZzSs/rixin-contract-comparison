from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path

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
from app.models import CompareTask, DiffType, TextRange
from app.services.audit_summary import AuditItem, build_task_audit_items
from app.services.evidence_validity import is_located_evidence

_SOURCE_TYPE_ORDER = {
    "clause": 0,
    "page": 1,
    "signing_region": 2,
    "table": 3,
    "seal": 4,
    "header_footer": 5,
    "metadata": 6,
}
_SOURCE_TYPE_LABELS = {
    "clause": "条款",
    "page": "页面",
    "table": "表格",
    "metadata": "封面",
    "seal": "印章",
    "signing_region": "签章区",
    "header_footer": "页眉页脚",
}
_CHINESE_NUMBERS = "一二三四五六七八九十"


def build_report_title(task: CompareTask) -> str:
    name = Path(task.original_filename or task.compare_filename or "").stem
    if not name:
        name = _extract_pdf_heading(task.original_pdf_path) or _extract_pdf_heading(task.compare_pdf_path) or "合同"
    return f"{_clean_report_text(name, 60)}差异分析报告"


def build_report_filename(task: CompareTask) -> str:
    title = build_report_title(task)
    safe_title = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title).strip(" ._") or "合同差异分析报告"
    return f"{safe_title}.pdf"


def _visible_audit_items(task: CompareTask) -> list[AuditItem]:
    return [item for item in build_task_audit_items(task) if item.review_status != "IGNORED"]


class ReportGenerator:
    def generate(self, task: CompareTask, output_path: str | Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        font_name = self._register_font()
        self._font_name = font_name
        styles = self._styles(font_name)

        report_title = build_report_title(task)
        audit_items = _visible_audit_items(task)
        indexed_items = self._indexed_items(audit_items)
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
            Paragraph("差异明细", styles["Heading2"]),
            *self._diff_grouped_tables(indexed_items, styles),
        ]

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=1.5 * cm,
            leftMargin=1.5 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.5 * cm,
        )
        doc.build(story, onFirstPage=self._page_footer, onLaterPages=self._page_footer)
        return output_path

    # ── Font & Styles ──────────────────────────────────────────────

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
            "Heading3": ParagraphStyle(
                "ContractHeading3",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=10.5,
                leading=16,
                spaceBefore=6,
                spaceAfter=4,
                textColor=colors.HexColor("#374151"),
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
            "DiffText": ParagraphStyle(
                "ContractDiffText",
                parent=base["Normal"],
                fontName=font_name,
                fontSize=8,
                leading=11,
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

    # ── Metadata & Summary ─────────────────────────────────────────

    def _metadata_table(self, task: CompareTask, styles: dict[str, ParagraphStyle]) -> Table:
        rows = [
            [Paragraph("任务编号", styles["MetaLabel"]), Paragraph(escape(task.task_id), styles["Normal"])],
            [Paragraph("原合同", styles["MetaLabel"]), Paragraph(escape(task.original_filename), styles["Normal"])],
            [Paragraph("对比合同", styles["MetaLabel"]), Paragraph(escape(task.compare_filename), styles["Normal"])],
            [
                Paragraph("生成时间", styles["MetaLabel"]),
                Paragraph(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), styles["Normal"]),
            ],
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

    # ── Grouped Diff Tables ────────────────────────────────────────

    def _diff_grouped_tables(
        self,
        indexed_items: list[tuple[int, AuditItem]],
        styles: dict[str, ParagraphStyle],
    ) -> list[Flowable]:
        if not indexed_items:
            return [Paragraph("未发现差异。", styles["Normal"])]

        groups: dict[str, list[tuple[int, AuditItem]]] = {}
        for entry in indexed_items:
            source_type = entry[1].source_type or "clause"
            groups.setdefault(source_type, []).append(entry)

        sorted_groups = sorted(groups.items(), key=lambda item: _SOURCE_TYPE_ORDER.get(item[0], 99))

        flowables: list[Flowable] = []
        for group_index, (source_type, items) in enumerate(sorted_groups):
            label = _SOURCE_TYPE_LABELS.get(source_type, source_type)
            cn_num = _CHINESE_NUMBERS[group_index] if group_index < len(_CHINESE_NUMBERS) else str(group_index + 1)
            heading = Paragraph(f"{cn_num}、{label}差异（{len(items)}项）", styles["Heading3"])
            flowables.append(heading)

            for index, item in items:
                flowables.append(self._build_item_summary_table(index, item, styles))
                flowables.extend(self._audit_item_detail_flowables(item, styles))
                flowables.append(Spacer(1, 0.22 * cm))
            flowables.append(Spacer(1, 0.3 * cm))

        return flowables

    def _build_item_summary_table(
        self,
        index: int,
        item: AuditItem,
        styles: dict[str, ParagraphStyle],
    ) -> Table:
        col_widths = [0.8 * cm, 1.0 * cm, 1.8 * cm, 2.6 * cm, 4.8 * cm, 4.8 * cm]
        header = [
            Paragraph("序号", styles["IndexHead"]),
            Paragraph("类型", styles["IndexHead"]),
            Paragraph("页码", styles["IndexHead"]),
            Paragraph("标题", styles["IndexHead"]),
            Paragraph("原文内容", styles["IndexHead"]),
            Paragraph("修改后内容", styles["IndexHead"]),
        ]
        type_label = self._diff_type_label(item.diff_type)
        type_color = {"ADD": "#15804F", "DELETE": "#C9362C", "MODIFY": "#A96300"}.get(item.diff_type, "#4B5563")
        page_label = self._page_label(item)
        orig_rich = self._highlighted_cell_text(
            item.original_text,
            item.original_change_ranges,
            item.diff_type,
            "original",
            200,
        )
        comp_rich = self._highlighted_cell_text(
            item.compare_text,
            item.compare_change_ranges,
            item.diff_type,
            "compare",
            200,
        )
        rows: list[list[Flowable]] = [
            header,
            [
                Paragraph(f"{index:02d}", styles["IndexCell"]),
                Paragraph(f'<font color="{type_color}"><b>{escape(type_label)}</b></font>', styles["IndexCell"]),
                Paragraph(escape(page_label), styles["IndexCell"]),
                Paragraph(escape(_clean_report_text(item.title, 40)), styles["IndexCell"]),
                Paragraph(orig_rich, styles["DiffText"]),
                Paragraph(comp_rich, styles["DiffText"]),
            ],
        ]

        table = Table(rows, colWidths=col_widths, repeatRows=1)
        style_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        style_commands.append(("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#FFFFFF")))
        table.setStyle(TableStyle(style_commands))
        return table

    def _audit_item_detail_flowables(
        self,
        item: AuditItem,
        styles: dict[str, ParagraphStyle],
    ) -> list[Flowable]:
        flowables: list[Flowable] = []
        for label, value in self._audit_item_detail_fields(item):
            text = f"{label}：{value}"
            flowables.append(Paragraph(escape(text), styles["Small"]))
        return flowables

    def _audit_item_detail_fields(self, item: AuditItem) -> list[tuple[str, str]]:
        source_label = _SOURCE_TYPE_LABELS.get(item.source_type, item.source_type or "未知")
        section_path = " / ".join(item.section_path) if item.section_path else "未提供"
        structural_flags = "、".join(item.structural_flags) if item.structural_flags else "无"
        review_flags = "、".join(item.review_flags) if item.review_flags else "无"
        text_confidence = self._format_optional_confidence(item.text_confidence)
        match_confidence = item.match_confidence or "未提供"
        ocr_statuses = "、".join(item.ocr_context.statuses) if item.ocr_context.statuses else "未受影响"
        ocr_reasons = "、".join(item.ocr_context.reasons) if item.ocr_context.reasons else "无"
        ocr_sides = "、".join(item.ocr_context.sides) if item.ocr_context.sides else "无"
        ocr_pages = "、".join(str(page_no) for page_no in item.ocr_context.page_numbers) or "无"
        remediation_ids = (
            "、".join(item.remediation_context.action_ids) if item.remediation_context.action_ids else "无"
        )
        remediation_actions = (
            "、".join(item.remediation_context.action_types) if item.remediation_context.action_types else "无"
        )
        remediation_statuses = (
            "、".join(item.remediation_context.statuses) if item.remediation_context.statuses else "无"
        )
        remediation_changes = []
        if item.remediation_context.changed_evidence:
            remediation_changes.append("证据已变更")
        if item.remediation_context.changed_diff_text:
            remediation_changes.append("差异文本已变更")
        if item.remediation_context.requires_manual_review:
            remediation_changes.append("需要人工复核")
        remediation_summary = "、".join(remediation_changes) if remediation_changes else "无"
        review_comment = item.review_comment or "无"
        reviewed_by = item.reviewed_by or "未提供"
        reviewed_at = item.reviewed_at or "未提供"
        evidence_location = self._evidence_location_text(item)
        scope_labels = {"ITEM": "审计项", "DIFF": "差异", "NONE": "无"}

        return [
            ("审计项", item.item_id),
            ("来源", f"{source_label} ({item.source_type or 'unknown'})"),
            ("章节类型", item.section_type or "未提供"),
            ("章节路径", section_path),
            ("原文全文", item.original_text or "—"),
            ("修改后全文", item.compare_text or "—"),
            ("证据位置", evidence_location),
            ("质量状态", item.quality_status),
            ("结构标记", structural_flags),
            ("审核标记", review_flags),
            ("文本置信度", text_confidence),
            ("匹配置信度", match_confidence),
            ("OCR归属", scope_labels[item.ocr_context.scope]),
            ("OCR受影响", "是" if item.ocr_context.affected else "否"),
            ("OCR状态", ocr_statuses),
            ("OCR原因", ocr_reasons),
            ("OCR侧", ocr_sides),
            ("OCR页码", ocr_pages),
            ("修复归属", scope_labels[item.remediation_context.scope]),
            ("修复动作ID", remediation_ids),
            ("修复动作", remediation_actions),
            ("修复状态", remediation_statuses),
            ("修复结果", remediation_summary),
            ("审核状态", item.review_status),
            ("审核意见", review_comment),
            ("审核人", reviewed_by),
            ("审核时间", reviewed_at),
        ]

    def _evidence_location_text(self, item: AuditItem) -> str:
        if item.evidence_state == "UNLOCATED":
            return "未定位"
        locations = [
            *self._side_evidence_locations(item.original_evidence, "原文"),
            *self._side_evidence_locations(item.compare_evidence, "新版"),
        ]
        return "；".join(locations) if locations else "未定位"

    @staticmethod
    def _side_evidence_locations(evidence_boxes, side_label: str) -> list[str]:
        locations = []
        for box in evidence_boxes:
            if not is_located_evidence(box):
                continue
            bbox = box.bbox
            coordinates = ", ".join(
                ReportGenerator._format_number(value) for value in (bbox.x0, bbox.y0, bbox.x1, bbox.y1)
            )
            locations.append(
                f"{side_label}第{box.page_no}页 bbox=({coordinates}), "
                f"method={box.method}, confidence={box.confidence:.2f}, quality={box.evidence_quality}"
            )
        return locations

    @staticmethod
    def _format_number(value: float) -> str:
        return f"{value:g}"

    @staticmethod
    def _format_optional_confidence(value: float | None) -> str:
        return "未提供" if value is None else f"{value:.2f}"

    # ── Text Highlighting ──────────────────────────────────────────

    @staticmethod
    def _highlighted_cell_text(
        text: str,
        change_ranges: list[TextRange],
        diff_type: DiffType,
        side: str,
        limit: int,
    ) -> str:
        if not text or text.startswith("（"):
            return escape(text) if text else "—"
        color_map = {"ADD": "#15804F", "DELETE": "#C9362C", "MODIFY": "#A96300"}
        highlight_color = color_map.get(diff_type, "#A96300")
        if not change_ranges:
            return escape(_clean_report_text(text, limit))
        text_limit = min(len(text), limit)
        sorted_ranges = sorted(change_ranges, key=lambda r: r.start)
        parts: list[str] = []
        pos = 0
        for cr in sorted_ranges:
            if cr.start >= text_limit:
                break
            start = max(pos, cr.start)
            end = min(cr.end, text_limit)
            if start > pos:
                parts.append(escape(text[pos:start]))
            if start < end:
                parts.append(f'<font color="{highlight_color}">{escape(text[start:end])}</font>')
            pos = max(pos, end)
        if pos < text_limit:
            parts.append(escape(text[pos:text_limit]))
        if len(text) > limit:
            parts.append("...")
        result = "".join(parts)
        return " ".join(result.split())

    # ── Labels & Helpers ───────────────────────────────────────────

    def _indexed_items(self, audit_items: list[AuditItem]) -> list[tuple[int, AuditItem]]:
        return [(index, item) for index, item in enumerate(audit_items, start=1)]

    def _page_label(self, item: AuditItem) -> str:
        original_pages = self._pages(item, "original")
        compare_pages = self._pages(item, "compare")
        if item.diff_type == "ADD":
            return f"新版第{compare_pages}页" if compare_pages else "—"
        if item.diff_type == "DELETE":
            return f"原文第{original_pages}页" if original_pages else "—"
        parts = []
        if original_pages:
            parts.append(f"原文第{original_pages}页")
        if compare_pages:
            parts.append(f"新版第{compare_pages}页")
        return " / ".join(parts) if parts else "—"

    @staticmethod
    def _pages(item: AuditItem, side: str) -> str:
        evidence = item.original_evidence if side == "original" else item.compare_evidence
        pages = sorted({box.page_no for box in evidence if is_located_evidence(box)})
        return "、".join(str(p) for p in pages)

    @staticmethod
    def _diff_type_label(diff_type: str) -> str:
        return {"ADD": "新增", "DELETE": "删除", "MODIFY": "修改"}.get(diff_type, diff_type)


def _extract_pdf_heading(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    try:
        import fitz

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
