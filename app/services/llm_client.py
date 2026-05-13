from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from app.config import settings


class LLMClient(ABC):
    @abstractmethod
    def generate_json(self, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model

    def generate_json(self, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你只输出合法 JSON，不输出 Markdown。"},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as exc:
            return {"error": f"OpenAI 调用失败: {exc}"}


class MockLLMClient(LLMClient):
    high_keywords = {
        "金额",
        "付款",
        "账期",
        "违约金",
        "赔偿",
        "解除",
        "终止",
        "期限",
        "验收",
        "交付",
        "发票",
        "税率",
        "账户",
        "价格",
        "费用",
        "责任",
    }
    element_map = {
        "付款": "付款结算",
        "金额": "价款金额",
        "违约金": "违约责任",
        "赔偿": "赔偿责任",
        "解除": "解除终止",
        "终止": "解除终止",
        "验收": "交付验收",
        "交付": "交付验收",
        "发票": "税务发票",
        "税率": "税务发票",
        "账户": "收付款账户",
    }

    def generate_json(self, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        text = prompt
        compact = re.sub(r"[\s，。；：、“”‘’（）()\[\]【】《》,.!?:;\"']", "", text)
        punctuation_only = len(compact) < max(10, len(text) * 0.08)
        keywords = [keyword for keyword in self.high_keywords if keyword in text]
        if punctuation_only:
            risk_level = "LOW"
            risk_score = 15
            summary = "差异主要集中在格式、标点或空白，合同实质影响较低。"
        elif keywords:
            risk_level = "HIGH"
            risk_score = 85
            summary = f"差异涉及{keywords[0]}等关键合同要素，可能影响权利义务或履约成本。"
        elif len(text) > 1500:
            risk_level = "MEDIUM"
            risk_score = 55
            summary = "差异文本较长，建议结合业务背景复核是否改变合同义务。"
        else:
            risk_level = "LOW"
            risk_score = 25
            summary = "未命中关键高风险要素，建议常规复核。"

        contract_element = "一般条款"
        for keyword, element in self.element_map.items():
            if keyword in text:
                contract_element = element
                break

        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "contract_element": contract_element,
            "change_summary": summary,
            "risk_explanation": "该结论由 MockLLMClient 基于关键词和文本长度生成，仅用于本地开发和流程验证。",
            "review_suggestion": "建议法务重点核对变更是否经过授权，并确认业务、财务和履约团队可接受。",
        }


def build_llm_client() -> LLMClient:
    if settings.use_mock_llm or not settings.openai_api_key:
        return MockLLMClient()
    return OpenAIClient()

