from __future__ import annotations

import re

from app.models import AIAnalysis, DiffItem, RiskLevel


class RuleBasedRiskAnalyzer:
    """Deterministic risk scoring for contract diffs.

    The diff facts remain programmatic; this layer only adds a review priority
    and a short explanation so reports and records are not all LOW risk.
    """

    high_risk_patterns = {
        "付款条款": re.compile(r"付款|支付|回款|账期|逾期|发票|结算|预付款"),
        "金额税率": re.compile(r"金额|价款|总价|单价|税率|增值税|关税|违约金|赔偿"),
        "违约责任": re.compile(r"违约|赔偿|责任|免责|损失|罚款|索赔"),
        "合同解除": re.compile(r"解除|终止|撤销|暂停|退货"),
    }
    medium_risk_patterns = {
        "交付验收": re.compile(r"交付|交货|验收|里程碑|上线|工期|期限|日期|天|月"),
        "质量质保": re.compile(r"质量|质保|保修|维护|售后|缺陷"),
        "主体信息": re.compile(r"甲方|乙方|买方|卖方|合同编号|签订日期|签订地点|项目名称"),
        "知识产权与保密": re.compile(r"知识产权|著作权|专利|保密|数据|源代码"),
    }
    amount_pattern = re.compile(r"(?:\d[\d,]*(?:\.\d+)?\s*(?:元|万元|%|‰))|(?:人民币|价款|金额|税率)")
    date_pattern = re.compile(r"\d{4}年\d{1,2}月|\d+\s*(?:日|天|个月|年)")

    def analyze(self, diffs: list[DiffItem]) -> list[DiffItem]:
        return [self._analyze_diff(diff) for diff in diffs]

    def _analyze_diff(self, diff: DiffItem) -> DiffItem:
        text = self._diff_text(diff)
        element = "一般条款"
        score = {"ADD": 42, "DELETE": 48, "MODIFY": 34}.get(diff.diff_type, 30)

        high_element = self._first_matching_element(text, self.high_risk_patterns)
        medium_element = self._first_matching_element(text, self.medium_risk_patterns)
        if high_element:
            element = high_element
            score += 32
        elif medium_element:
            element = medium_element
            score += 18

        if self.amount_pattern.search(text):
            element = "金额税率" if element == "一般条款" else element
            score += 18
        if self.date_pattern.search(text):
            element = "交付验收" if element == "一般条款" else element
            score += 10
        if self._has_low_quality_evidence(diff):
            score += 5

        score = max(0, min(score, 100))
        risk_level = self._risk_level(score)
        analysis = AIAnalysis(
            risk_level=risk_level,
            risk_score=score,
            contract_element=element,
            change_summary=self._summary(diff),
            risk_explanation=self._explanation(risk_level, element, diff),
            review_suggestion=self._suggestion(risk_level, element),
            raw_response={"source": "rule_based"},
        )
        return diff.model_copy(update={"ai_analysis": analysis})

    def _diff_text(self, diff: DiffItem) -> str:
        return " ".join(
            part
            for part in [
                diff.title,
                diff.clause_no,
                diff.original_text,
                diff.compare_text,
                diff.readable_change,
            ]
            if part
        )

    def _first_matching_element(self, text: str, patterns: dict[str, re.Pattern[str]]) -> str:
        for element, pattern in patterns.items():
            if pattern.search(text):
                return element
        return ""

    def _has_low_quality_evidence(self, diff: DiffItem) -> bool:
        evidence = [*diff.original_evidence, *diff.compare_evidence]
        return bool(evidence) and any(item.evidence_quality == "LOW" or item.confidence < 0.6 for item in evidence)

    def _risk_level(self, score: int) -> RiskLevel:
        if score >= 75:
            return "HIGH"
        if score >= 50:
            return "MEDIUM"
        return "LOW"

    def _summary(self, diff: DiffItem) -> str:
        return diff.readable_change or diff.compare_snippet or diff.original_snippet or "识别到合同文本差异。"

    def _explanation(self, risk_level: RiskLevel, element: str, diff: DiffItem) -> str:
        action = {"ADD": "新增", "DELETE": "删除", "MODIFY": "修改"}.get(diff.diff_type, "变更")
        if risk_level == "HIGH":
            return f"{element}{action}可能直接影响合同履行、付款或责任承担，需要优先复核。"
        if risk_level == "MEDIUM":
            return f"{element}{action}可能影响履约边界或内部审批口径，建议专项确认。"
        return "该差异风险较低，但仍建议结合业务背景确认文本含义。"

    def _suggestion(self, risk_level: RiskLevel, element: str) -> str:
        if risk_level == "HIGH":
            return f"建议法务、业务负责人共同复核{element}，必要时补充审批或谈判记录。"
        if risk_level == "MEDIUM":
            return f"建议经办人与法务确认{element}变更是否符合审批和履约预期。"
        return "建议由合同经办人确认该变更是否为预期修改。"
