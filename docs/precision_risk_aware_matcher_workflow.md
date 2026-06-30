# Precision Phase 2B：风险感知 Matcher 调优工作流

## 目标

本阶段把 Phase 2A 的质量归因结果转化为 matcher guard。它不重写 matcher，也不引入 LLM 复核，而是在现有候选评分后识别高风险匹配，降低过度自信，并把原因写入 debug artifacts。

## 新增输出

每个候选的 `score_details` 会包含：

```json
{
  "matcher_risk_flags": ["SAME_KEY_LOW_BODY_COVERAGE"],
  "matcher_guard_applied": 1.0
}
```

`matcher_risk_flags` 为空时，`matcher_guard_applied` 为 `0.0`。

## 风险标签

| 标签 | 含义 | 默认处理 |
| --- | --- | --- |
| `SAME_KEY_LOW_BODY_COVERAGE` | clause key 相同但正文覆盖不足 | 不允许普通高置信；弱正文且标题不强时不靠 key 接受 |
| `CRITICAL_TOKEN_CONFLICT` | 金额、日期、期限等关键 token 冲突 | `match_confidence=LOW`；关键 token 完全不重合且正文分低时限制分数 |
| `SAME_NUMBER_LOW_BODY_SIMILARITY` | 编号相同但正文相似度过低 | `match_confidence=LOW`；标题不强时限制强匹配 |
| `BODY_ONLY_ALIGNMENT_RISK` | body-only 候选存在 alignment 风险 | `match_confidence=LOW`，保留候选给 attribution 定位 |

## 如何运行质量回归

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2b-final \
  --run-id precision-p2b-final \
  --fail-on-regression

python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2b-final
```

重点查看：

- `aggregate.matcher_risk_flag_counts`
- `aggregate.attribution_counts`
- `cases[].suspicious_matches[].matcher_risk_flags`
- `false_positive_count`
- `false_negative_count`
- `precision`
- `recall`
- `evidence_hit_rate`

## 如何使用真实任务 debug artifacts

真实任务 debug artifacts 只用于本地分析，不提交仓库。可查看：

```text
storage/tasks/<task-id>/debug/clause_matches.json
storage/tasks/<task-id>/debug/match_matrix_summary.json
```

如果真实任务发现新的错配模式，先抽象为不含真实合同文本的最小复现，再写入：

- `backend/tests/test_matcher_optimization.py`
- 或 `backend/tests/fixtures/ocr_compare_cases/<case-id>/`

禁止提交：

- 原始合同。
- 包含客户、金额、人员、项目等敏感信息的 debug artifact。
- 未脱敏的 expected/gold case。

## 人工判断建议

- 如果 `matcher_risk_flags` 出现但质量回归通过，说明 guard 正在降低风险候选的置信度，不代表一定是错误匹配。
- 如果 `SAME_KEY_LOW_BODY_COVERAGE` 高频出现，应优先检查 clause splitter 是否把签署页、附件、报价段切得过短。
- 如果 `CRITICAL_TOKEN_CONFLICT` 高频出现，应检查实际 diff 是否已正确暴露金额、日期、期限变化。
- 如果 `BODY_ONLY_ALIGNMENT_RISK` 高频出现，应优先补充模板化合同的 gold case。
