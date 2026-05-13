from __future__ import annotations

import pytest

from app.models import AIAnalysis
from app.services.llm_client import MockLLMClient


def test_mock_llm_marks_payment_risk_high() -> None:
    payload = MockLLMClient().generate_json("付款金额从100万调整为150万，账期延长。")
    assert payload["risk_level"] == "HIGH"
    assert payload["contract_element"] in {"付款结算", "价款金额"}


def test_ai_analysis_validates_risk_level() -> None:
    analysis = AIAnalysis(risk_level="high", risk_score=90)
    assert analysis.risk_level == "HIGH"
    with pytest.raises(Exception):
        AIAnalysis(risk_level="CRITICAL", risk_score=90)

