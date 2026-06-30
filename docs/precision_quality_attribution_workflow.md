# Precision Phase 2A 质量归因使用说明

## 目的

质量归因用于回答：当前合同差异比对的误报、漏报和低置信条款对齐，主要来自 matcher 错配、关键字段冲突、diff 抑制，还是 gold case 标注覆盖不足。

本阶段只做离线分析，不改变线上匹配、diff 结论、API 返回结构或质量门禁。

## 前置条件

先运行质量回归：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2a \
  --run-id precision-p2a \
  --fail-on-regression
```

## 生成归因报告

```bash
cd backend
python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2a
```

输出文件：

```text
.ocr-compare-quality/runs/precision-p2a/quality_attribution.json
```

## 重点字段

- `aggregate.false_positive_count`：当前回归中的误报数量。
- `aggregate.false_negative_count`：当前回归中的漏报数量。
- `aggregate.low_confidence_alignment_count`：存在 alignment 风险的条款匹配数量。
- `aggregate.alignment_risk_flag_counts`：各类 alignment risk flag 的分布。
- `aggregate.match_method_counts`：匹配方法分布。
- `aggregate.attribution_counts`：归因标签分布。
- `cases[].suspicious_matches`：可疑条款匹配样本。
- `cases[].warnings`：缺失或异常 debug artifact。

## 常见归因标签

- `LOW_CONFIDENCE_ALIGNMENT`：存在低置信条款对齐。
- `KEY_TOKEN_CONFLICT`：金额、日期、期限、主体、数量或税率等关键 token 冲突。
- `SUSPICIOUS_MATCH_METHOD`：命中了需要关注的匹配方法。
- `SAME_KEY_LOW_BODY_COVERAGE`：同 clause key 但正文覆盖不足。
- `UNEXPECTED_ACTUAL_NEEDS_LABEL`：存在未被 expected/gold case 覆盖的实际 diff。
- `KEY_TOKEN_RECALL_RISK`：存在漏报风险，需要检查关键字段差异是否被压制。
- `GOLD_CASE_NEEDS_REVIEW`：gold case 覆盖偏少或需要人工复核。

## 如何用于下一阶段

进入 Phase 2B 前，先查看：

1. `alignment_risk_flag_counts` 中最高频的风险。
2. `suspicious_matches` 中是否集中出现某个 `match_method`。
3. 误报是否主要来自 `UNEXPECTED_ACTUAL_NEEDS_LABEL`，如果是，应优先补 gold case。
4. 漏报是否伴随 `KEY_TOKEN_RECALL_RISK`，如果是，应优先做关键字段差异保护。

只有当归因显示 matcher 错配是主要问题时，才进入 matcher 策略调优。
