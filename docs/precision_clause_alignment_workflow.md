# Precision Phase 1 条款级对齐说明

## 目的

本阶段用于提升合同差异比对中的条款级对齐稳定性。它不引入 LLM，也不重写 diff 引擎，而是为每个条款匹配结果提供 fingerprint、对齐诊断和低置信风险标记。

## 关键输出

条款匹配结果会在 `score_details.alignment` 中包含：

- `number_match`
- `title_match`
- `body_similarity`
- `critical_token_overlap`
- `section_type_match`
- `page_distance`
- `risk_flags`

当出现金额、日期、编号或标题等冲突时，`risk_flags` 会记录潜在风险，例如：

- `CRITICAL_TOKEN_MISMATCH`
- `TEXT_MATCH_NUMBER_MISMATCH`
- `TITLE_MATCH_TEXT_MISMATCH`
- `POSSIBLE_CLAUSE_MISALIGNMENT`

## Debug Artifact

`clause_matches.json` 可以查看每个条款对的对齐依据。

`match_matrix_summary.json` 可以查看：

- `low_confidence_alignment_count`
- `alignment_risk_flag_counts`

这些字段用于定位可能导致误报或漏报的条款错配。

## 使用原则

本阶段遵循“先诊断，后干预”。低置信对齐先进入 debug 和质量分析，不直接等同于法律风险结论。
