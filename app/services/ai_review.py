from __future__ import annotations

import json
from collections import Counter

from app.models import AIAnalysis, DiffItem
from app.services.llm_client import LLMClient, build_llm_client
from app.services.prompts import build_diff_prompt, build_summary_prompt


class AIReviewService:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or build_llm_client()

    def analyze_diffs(self, diffs: list[DiffItem], enabled: bool = True) -> list[DiffItem]:
        for diff in diffs:
            if not enabled:
                diff.ai_analysis = self._disabled_analysis(diff)
                continue
            payload = self.client.generate_json(build_diff_prompt(diff))
            if "error" in payload:
                diff.ai_analysis = self._error_analysis(diff, payload["error"])
            else:
                try:
                    diff.ai_analysis = AIAnalysis(**payload, raw_response=payload)
                except Exception as exc:
                    diff.ai_analysis = self._error_analysis(diff, f"AI 返回格式校验失败: {exc}")
        return diffs

    def _disabled_analysis(self, diff: DiffItem) -> AIAnalysis:
        return AIAnalysis(
            risk_level="LOW",
            risk_score=0,
            contract_element="未启用 AI",
            change_summary=f"{diff.diff_id} 未启用 AI 审查。",
            risk_explanation="本次任务关闭了 AI 分析。",
            review_suggestion="请人工复核该差异。",
        )

    def _error_analysis(self, diff: DiffItem, error: str) -> AIAnalysis:
        return AIAnalysis(
            risk_level="MEDIUM",
            risk_score=50,
            contract_element="AI 审查异常",
            change_summary=f"{diff.diff_id} AI 审查失败，需人工复核。",
            risk_explanation=error,
            review_suggestion="建议人工复核该差异，并检查 LLM 配置或网络状态。",
        )


class AISummaryService:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or build_llm_client()

    def summarize(self, diffs: list[DiffItem], enabled: bool = True) -> str:
        if not diffs:
            return "未发现合同条款差异。"
        if enabled:
            payload = [
                {
                    "diff_id": diff.diff_id,
                    "type": diff.diff_type,
                    "risk": diff.ai_analysis.risk_level if diff.ai_analysis else "LOW",
                    "summary": diff.ai_analysis.change_summary if diff.ai_analysis else diff.readable_change[:120],
                }
                for diff in diffs
            ]
            result = self.client.generate_json(build_summary_prompt(json.dumps(payload, ensure_ascii=False)))
            if result.get("summary"):
                return str(result["summary"])
        return self._fallback_summary(diffs)

    def _fallback_summary(self, diffs: list[DiffItem]) -> str:
        risks = Counter(diff.ai_analysis.risk_level if diff.ai_analysis else "LOW" for diff in diffs)
        types = Counter(diff.diff_type for diff in diffs)
        return (
            f"本次共识别 {len(diffs)} 处差异，其中新增 {types['ADD']} 处、删除 {types['DELETE']} 处、修改 {types['MODIFY']} 处；"
            f"高风险 {risks['HIGH']} 处、中风险 {risks['MEDIUM']} 处、低风险 {risks['LOW']} 处。"
            "建议优先复核高风险差异及涉及付款、违约、解除、交付验收等条款。"
        )

