from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path

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
from app.models import CompareTask, DiffItem


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

        story = [
            Paragraph("AI 合同差异分析报告", styles["Title"]),
            Spacer(1, 0.7 * cm),
            Paragraph(f"任务编号：{escape(task.task_id)}", styles["Normal"]),
            Paragraph(f"原合同：{escape(task.original_filename)}", styles["Normal"]),
            Paragraph(f"对比合同：{escape(task.compare_filename)}", styles["Normal"]),
            Spacer(1, 0.5 * cm),
            Paragraph("AI 总体摘要", styles["Heading2"]),
            Paragraph(escape(task.ai_summary or "暂无摘要。"), styles["Normal"]),
            Spacer(1, 0.4 * cm),
            Paragraph("风险统计", styles["Heading2"]),
            self._risk_table(task, styles),
            Spacer(1, 0.4 * cm),
            Paragraph("合同要素分类统计", styles["Heading2"]),
            self._element_table(task.diffs, styles),
            PageBreak(),
            Paragraph("差异明细表", styles["Heading2"]),
            self._diff_table(task.diffs, styles),
            PageBreak(),
            Paragraph("高风险差异专题", styles["Heading2"]),
        ]

        high_risk = [diff for diff in task.diffs if diff.ai_analysis and diff.ai_analysis.risk_level == "HIGH"]
        if not high_risk:
            story.append(Paragraph("未识别到高风险差异。", styles["Normal"]))
        else:
            for diff in high_risk:
                story.extend(self._diff_detail(diff, styles))

        story.append(PageBreak())
        story.append(Paragraph("全部差异截图明细", styles["Heading2"]))
        for diff in task.diffs:
            story.extend(self._diff_detail(diff, styles, include_images=True))

        story.extend(
            [
                Spacer(1, 0.5 * cm),
                Paragraph("AI 免责声明", styles["Heading2"]),
                Paragraph(
                    "本报告由 AI 辅助生成，仅用于合同差异审查参考，不构成正式法律意见。最终结论应由具备授权的业务、财务和法务人员复核确认。",
                    styles["Normal"],
                ),
            ]
        )
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

    def _risk_table(self, task: CompareTask, styles: dict[str, ParagraphStyle]) -> Table:
        data = [
            ["差异总数", "高风险", "中风险", "低风险"],
            [str(task.diff_count), str(task.high_risk_count), str(task.medium_risk_count), str(task.low_risk_count)],
        ]
        return self._styled_table(data)

    def _element_table(self, diffs: list[DiffItem], styles: dict[str, ParagraphStyle]) -> Table:
        counter = Counter(diff.ai_analysis.contract_element if diff.ai_analysis else "一般条款" for diff in diffs)
        data = [["合同要素", "差异数量"]]
        data.extend([[element, str(count)] for element, count in counter.most_common()] or [["无", "0"]])
        return self._styled_table(data)

    def _diff_table(self, diffs: list[DiffItem], styles: dict[str, ParagraphStyle]) -> Table:
        data = [["编号", "类型", "风险", "合同要素", "AI 摘要"]]
        for diff in diffs:
            analysis = diff.ai_analysis
            data.append(
                [
                    diff.diff_id,
                    diff.diff_type,
                    analysis.risk_level if analysis else "LOW",
                    analysis.contract_element if analysis else "一般条款",
                    Paragraph(escape((analysis.change_summary if analysis else diff.readable_change)[:120]), styles["Small"]),
                ]
            )
        return self._styled_table(data, col_widths=[1.4 * cm, 1.5 * cm, 1.5 * cm, 3 * cm, 10 * cm])

    def _diff_detail(
        self,
        diff: DiffItem,
        styles: dict[str, ParagraphStyle],
        include_images: bool = False,
    ) -> list:
        analysis = diff.ai_analysis
        story = [
            Paragraph(f"{escape(diff.diff_id)} {escape(diff.diff_type)} {escape(diff.title or diff.clause_no)}", styles["Heading2"]),
            Paragraph(f"风险等级：{escape(analysis.risk_level if analysis else 'LOW')}", styles["Normal"]),
            Paragraph(f"摘要：{escape(analysis.change_summary if analysis else diff.readable_change)}", styles["Normal"]),
            Paragraph(f"复核建议：{escape(analysis.review_suggestion if analysis else '请人工复核。')}", styles["Normal"]),
        ]
        if include_images:
            for label, screenshot in [("原合同截图", diff.original_screenshot), ("对比合同截图", diff.compare_screenshot)]:
                if screenshot and Path(screenshot).exists():
                    story.append(Paragraph(label, styles["Normal"]))
                    story.append(Image(screenshot, width=15 * cm, height=5 * cm, kind="proportional"))
                    story.append(Spacer(1, 0.2 * cm))
        return story

    def _styled_table(self, data: list, col_widths: list | None = None) -> Table:
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
                    ("FONTNAME", (0, 0), (-1, -1), getattr(self, "_font_name", "Helvetica")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table
