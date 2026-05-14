from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import settings
from app.models import AIAnalysis, DiffItem


class AIReviewService:
    def analyze_diffs(self, diffs: list[DiffItem]) -> tuple[list[DiffItem], list[str]]:
        errors: list[str] = []
        for diff in diffs:
            try:
                diff.ai_analysis = self.analyze_diff(diff)
            except Exception as exc:
                errors.append(f"{diff.diff_id} AI 风险分析失败: {exc}")
                diff.ai_analysis = self._rule_based_analysis(diff)
        return diffs, errors

    def analyze_diff(self, diff: DiffItem) -> AIAnalysis:
        if not self._is_llm_configured():
            return self._rule_based_analysis(diff)
        payload = self._request_payload(diff)
        url = self._chat_completions_url(settings.ai_llm_base_url)
        headers = {"Content-Type": "application/json"}
        if settings.ai_llm_api_key:
            headers["Authorization"] = f"Bearer {settings.ai_llm_api_key}"

        with httpx.Client(timeout=settings.ai_analysis_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return self._parse_ai_analysis(content)

    def _is_llm_configured(self) -> bool:
        return bool(settings.ai_llm_base_url and settings.ai_llm_model and settings.ai_llm_api_key)

    def _request_payload(self, diff: DiffItem) -> dict[str, Any]:
        user_prompt = {
            "diff_id": diff.diff_id,
            "diff_type": diff.diff_type,
            "clause_no": diff.clause_no,
            "title": diff.title,
            "original_text": diff.original_text,
            "compare_text": diff.compare_text,
            "original_snippet": diff.original_snippet,
            "compare_snippet": diff.compare_snippet,
            "readable_change": diff.readable_change,
        }
        return {
            "model": settings.ai_llm_model,
            "messages": [
                {"role": "system", "content": settings.ai_system_prompt},
                {
                    "role": "user",
                    "content": (
                        "请审查以下合同差异，判断风险等级并给出复核建议。"
                        f"输入 JSON：{json.dumps(user_prompt, ensure_ascii=False)}"
                    ),
                },
            ],
            "temperature": 0.1,
        }

    def _chat_completions_url(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _parse_ai_analysis(self, content: str) -> AIAnalysis:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                raise ValueError("AI 返回内容不是 JSON。") from None
            payload = json.loads(match.group(0))
        try:
            return AIAnalysis.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"AI 返回字段不符合约定: {exc}") from exc

    def _rule_based_analysis(self, diff: DiffItem) -> AIAnalysis:
        text = " ".join(
            [
                diff.title,
                diff.clause_no,
                diff.readable_change,
                diff.original_snippet,
                diff.compare_snippet,
                diff.original_text,
                diff.compare_text,
            ]
        )
        element = self._contract_element(text)
        risk_level, risk_score = self._risk_level(diff, text, element)
        change_summary = diff.readable_change or self._fallback_change_summary(diff)
        return AIAnalysis(
            risk_level=risk_level,
            risk_score=risk_score,
            contract_element=element,
            change_summary=change_summary,
            risk_explanation=self._risk_explanation(diff, element, risk_level),
            review_suggestion=self._review_suggestion(element, risk_level),
            raw_response={"source": "rule_based_fallback"},
        )

    def _contract_element(self, text: str) -> str:
        rules = [
            ("付款与价款", ["付款", "价款", "金额", "总价", "单价", "结算", "支付", "pay", "price", "amount"]),
            ("交付与验收", ["交付", "交货", "验收", "收货", "delivery", "acceptance"]),
            ("质量与标准", ["质量", "标准", "规格", "检验", "合格", "quality", "standard"]),
            ("违约与赔偿", ["违约", "赔偿", "罚金", "责任", "breach", "liability", "damages"]),
            ("期限与生效", ["期限", "日期", "日内", "生效", "终止", "解除", "term", "date"]),
            ("保密与知识产权", ["保密", "知识产权", "商业秘密", "confidential", "intellectual"]),
            ("争议解决", ["争议", "仲裁", "诉讼", "法院", "jurisdiction", "dispute"]),
            ("主体信息", ["甲方", "乙方", "名称", "地址", "统一社会信用代码", "party", "address"]),
        ]
        lowered = text.lower()
        for element, keywords in rules:
            if any(keyword.lower() in lowered for keyword in keywords):
                return element
        return "一般条款"

    def _risk_level(self, diff: DiffItem, text: str, element: str) -> tuple[str, int]:
        high_keywords = ["违约", "赔偿", "解除", "争议", "仲裁", "诉讼", "责任", "confidential", "liability", "damages"]
        medium_keywords = ["付款", "金额", "价款", "期限", "验收", "交付", "质量", "支付", "pay", "amount", "delivery"]
        lowered = text.lower()
        if diff.diff_type == "DELETE" and element in {"违约与赔偿", "争议解决", "保密与知识产权"}:
            return "HIGH", 82
        if any(keyword.lower() in lowered for keyword in high_keywords):
            return "HIGH", 78
        if any(keyword.lower() in lowered for keyword in medium_keywords) or diff.diff_type in {"ADD", "DELETE"}:
            return "MEDIUM", 58
        return "LOW", 28

    def _fallback_change_summary(self, diff: DiffItem) -> str:
        if diff.diff_type == "ADD":
            return f"新增条款：{diff.compare_snippet or diff.compare_text[:80]}"
        if diff.diff_type == "DELETE":
            return f"删除条款：{diff.original_snippet or diff.original_text[:80]}"
        return f"修改条款：{diff.original_snippet or diff.original_text[:40]} -> {diff.compare_snippet or diff.compare_text[:40]}"

    def _risk_explanation(self, diff: DiffItem, element: str, risk_level: str) -> str:
        if risk_level == "HIGH":
            return f"该差异涉及{element}，可能改变合同核心权利义务或争议处理结果，需要重点审查。"
        if risk_level == "MEDIUM":
            return f"该差异涉及{element}，可能影响履约成本、时间安排或验收责任，建议业务与法务共同确认。"
        return f"该差异涉及{element}，暂未识别出显著风险，但仍应结合交易背景复核。"

    def _review_suggestion(self, element: str, risk_level: str) -> str:
        if risk_level == "HIGH":
            return f"建议法务负责人复核{element}相关表述，并确认是否需要补充审批或谈判记录。"
        if risk_level == "MEDIUM":
            return f"建议合同经办人与业务负责人确认{element}变化是否符合商业预期。"
        return "建议经办人核对文本变更是否符合双方真实意思表示。"
