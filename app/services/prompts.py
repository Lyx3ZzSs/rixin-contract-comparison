from __future__ import annotations

from app.models import DiffItem


def build_diff_prompt(diff: DiffItem) -> str:
    return f"""你是合同风控审查助手。请基于合同差异输出 JSON：
字段：risk_level(LOW/MEDIUM/HIGH), risk_score(0-100), contract_element, change_summary, risk_explanation, review_suggestion。

差异编号：{diff.diff_id}
差异类型：{diff.diff_type}
条款编号：{diff.clause_no}
原合同内容：{diff.original_text[:2000]}
对比合同内容：{diff.compare_text[:2000]}
可读变化：{diff.readable_change[:1000]}
"""


def build_summary_prompt(diffs_payload: str) -> str:
    return f"""你是合同风控审查助手。请根据以下差异分析生成 JSON：
字段：summary。要求中文，包含总体风险、重点关注条款和复核建议。

差异数据：
{diffs_payload}
"""

