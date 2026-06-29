# Phase 5 质量回归体系使用说明

## 目的

质量回归体系用于回答：本次代码、提示词、OCR、模型路由或配置变更，相比 baseline 是否让合同差异比对质量变好或变差。

它不直接提升比对算法精度，而是为后续精度优化提供可重复、可追踪、可门禁的质量判断。

## 前置条件

1. 已经有 gold case 目录。
2. `expected.json` 中需要计入质量指标的差异已经人工标注为 `APPROVED`。
3. `DRAFT` 和 `REJECTED` 不会计入 gold 指标。

## 建立 baseline

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/baseline-v0.0.2 \
  --write-baseline .ocr-compare-quality/baselines/v0.0.2.json
```

baseline 文件建议只保存经过确认的公开 fixture 结果。包含真实合同内容的 baseline 不应提交到版本库。

## 运行本地回归

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --fail-on-regression
```

如果门禁失败且传入了 `--fail-on-regression`，命令会以退出码 `1` 结束，方便后续接入 CI。

## 输出文件

默认输出：

```text
<output-dir>/quality.json
<output-dir>/baseline_comparison.json
<output-dir>/run_summary.json
<output-dir>/html/index.html
```

`quality.json` 是当前完整质量评估结果。

`baseline_comparison.json` 是当前结果和 baseline 的差值。

`run_summary.json` 是本次运行摘要，包含 run id、git branch、git commit、dirty 状态、门禁结果和输出路径。

HTML 报告适合人工查看，重点看：

- Quality regression
- Failed gates
- Aggregate delta
- Case regression ranking
- Missed expected diffs
- Unexpected actual diffs
- Evidence drift

## 阈值配置

可以通过 JSON 文件覆盖默认阈值：

```json
{
  "min_recall": 0.95,
  "min_precision": 0.9,
  "min_evidence_hit_rate": 0.9,
  "max_false_positive_increase": 1,
  "max_false_negative_increase": 0,
  "max_recall_drop": 0.02
}
```

运行：

```bash
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --thresholds .ocr-compare-quality/quality_thresholds.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --fail-on-regression
```

## gold case 数量较少时如何解读

当 approved expected diff 很少时，precision、recall、evidence_hit_rate 会非常敏感。一个误报或漏检就可能导致大幅波动。

这种情况下不要只看 aggregate 指标，还要查看 case 级明细：

- 是哪个 case 退化？
- 是漏检、误报还是证据漂移？
- 是否因为 gold case 标注还不完整？
- 是否只是 DRAFT 尚未转为 APPROVED？

## CI 接入方式

后续 CI 可以直接调用：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/ci \
  --fail-on-regression
```

CI 需要归档这些产物：

- `.ocr-compare-quality/runs/ci/run_summary.json`
- `.ocr-compare-quality/runs/ci/quality.json`
- `.ocr-compare-quality/runs/ci/baseline_comparison.json`
- `.ocr-compare-quality/runs/ci/html/index.html`

## 敏感数据注意事项

不要提交真实合同、客户数据、从真实任务导出的 `actual.json`、包含敏感片段的 `expected.json`、或者基于真实合同生成的质量报告。

公开 fixture、脱敏 gold case 和脱敏 baseline 才适合进入版本库。
