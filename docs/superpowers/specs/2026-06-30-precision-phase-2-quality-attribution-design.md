# Precision Phase 2A：诊断驱动的质量归因设计

## 目的

Precision Phase 2A 用于回答一个具体问题：当前合同差异比对的误报、漏报和低置信结果，主要由哪些原因造成。

上一阶段已经完成条款级对齐基础设施，包括 `ClauseAlignmentAnalyzer`、`score_details.alignment`、低置信 risk flags、`clause_matches.json` 和 `match_matrix_summary.json`。本阶段不直接调 matcher、不改 diff 结论，而是把这些诊断信号沉淀为可重复运行的质量归因报告，为后续 Phase 2B/2C 的精度调优提供依据。

核心原则：

```text
先归因，再调参。
```

## 成功标准

- 能从质量回归结果和 debug artifact 中生成 case 级质量归因。
- 能区分误报、漏报、低置信对齐和可疑匹配方法。
- 能统计 alignment risk flag 分布，例如 `CRITICAL_TOKEN_MISMATCH`、`POSSIBLE_CLAUSE_MISALIGNMENT`。
- 能定位高风险 match method，例如 `same_clause_key_weighted`、`body_weighted_similarity`、`same_clause_no_low_similarity`。
- 不改变 `/api/compare/*`、`/api/extract/*` 兼容结构。
- 不改变线上匹配、diff、置信度判断行为。
- 质量回归命令仍可作为门禁运行，不引入退化。

## 当前基础

现有链路已经具备：

- `run_quality_regression.py`：执行 gold case 质量回归并输出 `quality.json`、`baseline_comparison.json`、`run_summary.json` 和 HTML 报告。
- `evaluate_ocr_compare_quality.py`：具备 expected/actual 差异质量评估能力。
- `clause_matches.json`：包含每个 `ClausePair` 的 `score_details`、`match_method`、`match_confidence`、候选摘要。
- `match_matrix_summary.json`：包含低置信匹配和 alignment risk 统计。
- `score_details.alignment`：包含条款编号、标题、正文相似度、关键 token overlap、section、页距和 risk flags。

Phase 2A 应复用这些产物，而不是重新执行完整比对或新增不兼容数据格式。

## 推荐方案

采用“质量归因报告层”方案：

```text
Quality Regression Output + Debug Artifacts -> Quality Attribution Report
```

新增一个离线分析脚本，读取某次质量回归输出目录和 case debug artifacts，生成结构化归因 JSON，并在可行时扩展 HTML 报告摘要。

本阶段不把归因结论写回业务结果，不影响用户可见差异列表。

## 非目标

本阶段不做：

- matcher 权重调整。
- diff quality 规则调整。
- OCR 模型路由调整。
- 前端审核工作台。
- LLM 语义复核。
- 自动法律风险判断。
- 企业权限、审计、治理能力。

这些能力可以在 Phase 2B 之后基于归因结果继续规划。

## 归因分类

第一版质量归因只做可解释的规则分类，避免过度推断。

### 1. 低置信条款对齐

来源：

- `ClausePair.match_confidence == "LOW"`
- `score_details.alignment.risk_flags` 非空
- `match_matrix_summary.low_confidence_alignment_count`

归因标签：

- `LOW_CONFIDENCE_ALIGNMENT`
- `CRITICAL_TOKEN_MISMATCH`
- `POSSIBLE_CLAUSE_MISALIGNMENT`
- `TEXT_MATCH_NUMBER_MISMATCH`
- `TITLE_MATCH_TEXT_MISMATCH`

### 2. 可疑匹配方法

来源：

- `match_method`
- `score_details`
- `match_candidates`

重点观察：

- `same_clause_key_weighted`
- `same_clause_no_low_similarity`
- `body_weighted_similarity`
- `partial_body_similarity`
- `section_mismatch_blocked`

归因标签：

- `SUSPICIOUS_MATCH_METHOD`
- `SAME_KEY_LOW_BODY_COVERAGE`
- `BODY_ONLY_MATCH`
- `SECTION_MISMATCH_CANDIDATE`

### 3. 关键字段差异风险

来源：

- `score_details.alignment.critical_token_overlap`
- `score_details.alignment.risk_flags`
- diff 质量评估中的 missed expected / unexpected actual

归因标签：

- `KEY_TOKEN_CONFLICT`
- `KEY_TOKEN_RECALL_RISK`
- `POSSIBLE_FIELD_DIFF_SUPPRESSED`

### 4. gold case 覆盖不足

来源：

- expected diff 数量少。
- case 中 DRAFT/REJECTED 项较多。
- actual diff 无法与 approved expected 对齐。

归因标签：

- `GOLD_CASE_NEEDS_REVIEW`
- `EXPECTED_DIFF_TOO_SPARSE`
- `UNEXPECTED_ACTUAL_NEEDS_LABEL`

## 输出设计

新增质量归因 JSON，建议路径：

```text
.ocr-compare-quality/runs/<run-id>/quality_attribution.json
```

结构示例：

```json
{
  "run_id": "precision-p2a",
  "case_count": 3,
  "aggregate": {
    "false_positive_count": 1,
    "false_negative_count": 0,
    "low_confidence_alignment_count": 2,
    "alignment_risk_flag_counts": {
      "CRITICAL_TOKEN_MISMATCH": 1,
      "POSSIBLE_CLAUSE_MISALIGNMENT": 2
    },
    "match_method_counts": {
      "same_clause_key_weighted": 8,
      "body_weighted_similarity": 3
    },
    "attribution_counts": {
      "LOW_CONFIDENCE_ALIGNMENT": 2,
      "KEY_TOKEN_CONFLICT": 1
    }
  },
  "cases": [
    {
      "case_id": "simple_scanned",
      "quality_status": "PASSED",
      "false_positive_count": 0,
      "false_negative_count": 0,
      "low_confidence_alignment_count": 1,
      "alignment_risk_flag_counts": {
        "POSSIBLE_CLAUSE_MISALIGNMENT": 1
      },
      "suspicious_matches": [
        {
          "original_clause_id": "O001",
          "compare_clause_id": "N001",
          "match_method": "same_clause_key_weighted",
          "match_confidence": "LOW",
          "risk_flags": ["POSSIBLE_CLAUSE_MISALIGNMENT"],
          "body_similarity": 0.42,
          "critical_token_overlap": 0.0
        }
      ],
      "attribution_tags": ["LOW_CONFIDENCE_ALIGNMENT"]
    }
  ]
}
```

## 数据流

```text
run_quality_regression.py
  -> quality.json
  -> case output/debug artifacts
  -> quality_attribution.py
  -> quality_attribution.json
  -> optional HTML section
```

第一版可以作为独立脚本运行：

```bash
cd backend
python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p1-final
```

后续再决定是否把它集成进 `run_quality_regression.py`。

## 组件设计

### 1. Attribution Loader

职责：

- 读取 `run_summary.json`、`quality.json`、`baseline_comparison.json`。
- 遍历 case 输出目录。
- 尝试读取每个 case 的 `clause_matches.json` 和 `match_matrix_summary.json`。
- 文件缺失时记录 warning，不中断整个分析。

### 2. Match Attribution Analyzer

职责：

- 从 `ClausePair` debug 数据中提取 match method、confidence、alignment diagnostics。
- 统计 risk flags。
- 提取可疑匹配样本。
- 生成 case 级 attribution tags。

### 3. Quality Attribution Writer

职责：

- 输出 `quality_attribution.json`。
- 保持字段稳定，便于后续 HTML 报告和 CI 读取。
- 对缺失 debug artifact 的 case 输出 `warnings`。

### 4. Optional HTML Summary

第一版可选。若实现成本低，可以在现有质量回归 HTML 中增加：

- Attribution summary
- Top risk flags
- Suspicious match methods
- Cases needing gold review

如果 HTML 改动风险较高，第一版只输出 JSON 和中文文档。

## 错误处理

- `run-dir` 不存在：命令失败并输出明确错误。
- `quality.json` 缺失：命令失败，因为无法进行归因。
- 单个 case debug artifact 缺失：不中断，写入 `warnings`。
- `score_details`、`alignment`、`risk_flags` 类型异常：跳过该字段并写入 warning。
- JSON 解析失败：记录 case warning，继续分析其他 case。

## 测试策略

新增 focused tests，优先覆盖纯函数和脚本边界：

- 能从 mock `clause_matches.json` 统计 alignment risk flags。
- 能识别低置信 alignment case。
- 能统计 suspicious match methods。
- 缺失 debug artifact 时不崩溃。
- `quality_attribution.json` 输出字段稳定。
- CLI smoke：给定 fixture run dir，能生成归因文件。

回归验证：

```bash
cd backend
python -m pytest tests/test_quality_attribution.py -v
python -m pytest tests/test_run_quality_regression.py tests/test_evaluate_ocr_compare_quality.py -v
python -m ruff check .
```

## 后续承接

Phase 2A 的输出用于指导后续阶段：

- Phase 2B：基于归因结果调 matcher 策略。
- Phase 2C：保护关键字段差异 recall。
- Phase 2D：把高价值真实失败样本沉淀为 gold case。

进入 Phase 2B 前，应先查看 `quality_attribution.json`，明确主要问题是 matcher 错配、diff 抑制、分条错误，还是 gold case 覆盖不足。
