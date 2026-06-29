# Precision Phase 1：文档规范化与条款级对齐设计

## 目的

Precision Phase 1 进入比对精度优化主线。前置阶段已经完成 OCR gold case 人工标注和质量回归体系，本阶段要利用这些质量基础，开始降低合同差异比对中的误报、漏报和错位比对。

本阶段不重写 diff 引擎，也不引入 LLM 语义复核。核心目标是让系统更稳定地回答：

- 原合同的某一条款应该和新合同的哪一条款比对。
- 哪些文本变化只是 OCR、格式、标点或换行噪声。
- 哪些条款对齐低置信，可能造成误报或漏报。
- 某次对齐为什么可信或不可信。

## 成功标准

- OCR 或格式噪声导致的误报不增加，优先下降。
- 条款错配导致的漏报不增加，优先下降。
- 金额、日期、主体、期限等关键字段差异 recall 不下降。
- 低置信条款对齐能在 debug artifact 或质量报告中定位。
- Phase 5 质量回归命令可以验证本阶段收益，不出现质量门禁退化。

## 当前基础

现有主链路已经具备：

```text
ClauseSplitter -> ClauseMatcher -> DiffEngine -> DiffQuality -> Report
```

相关现有结构：

- `Clause` 已包含 `clause_no`、`title`、`text`、`normalized_text`、`match_text`、`clause_key`、`section_type`、`page_numbers`、`source_block_ids`。
- `ClausePair` 已包含 `score`、`match_method`、`score_details`、`match_candidates`、`match_confidence`。
- `DiffItem` 已透传 `match_score`、`match_method`、`match_score_details`、`match_candidates`、`match_confidence`、`review_flags`。
- `CompareDebugWriter.write_matches()` 已输出 `clause_matches.json`，可承载更丰富的对齐诊断。
- Phase 5 已提供 `run_quality_regression.py`、baseline 对比、门禁、HTML 回归报告和中文使用文档。

本阶段应扩展这些已有承载点，而不是新增不兼容的数据通道。

## 推荐方案

采用“条款对齐基础设施优先”的方案：

```text
ClauseAlignmentFingerprint + AlignmentDiagnostics
```

第一版优先做诊断和可解释性，不直接大幅改变匹配决策。只有测试证明安全后，再把诊断结果用于更强的匹配策略。

核心原则：

```text
先诊断，后干预。
```

## 非目标

本阶段不做：

- LLM 条款语义复核。
- 自动法律风险判断。
- 表格单元格级重构。
- 前端审核工作台。
- 大规模重写 `ClauseMatcher`。
- 修改 `/api/compare/*` 或 `/api/extract/*` 兼容结构。
- 引入外部平台依赖。

## 组件设计

### 1. Clause Alignment Fingerprint

新增一个轻量条款对齐特征生成器，建议文件：

```text
backend/app/services/clause_alignment.py
```

它从 `Clause` 生成稳定、可解释的对齐特征：

- `clause_no_key`：规范化后的条款编号。
- `title_key`：规范化后的标题或标题候选。
- `normalized_body_key`：去噪后的正文主干。
- `body_fingerprint`：正文指纹，用于近似判断是否为同一条款。
- `critical_token_fingerprint`：关键 token 指纹。
- `structure_key`：章节类型、层级、section 信息。
- `page_span`：条款覆盖页码范围。

关键 token 第一版包括：

- 金额。
- 日期。
- 百分比。
- 期限。
- 合同编号。
- 主体名称片段。
- 数量。
- 税率。

Fingerprint 不应替代 `Clause.text`、`Clause.normalized_text` 或 `Clause.match_text`。它只是对齐辅助信息。

### 2. Alignment Diagnostics

对每个 `ClausePair` 输出对齐诊断，建议写入 `score_details["alignment"]`：

```json
{
  "number_match": true,
  "title_match": true,
  "body_similarity": 0.92,
  "critical_token_overlap": 0.8,
  "section_type_match": true,
  "page_distance": 1,
  "risk_flags": []
}
```

诊断字段用于回答“为什么这两个条款被匹配”。

第一版重点覆盖：

- 编号是否一致。
- 标题是否一致或高度相似。
- 正文相似度。
- 关键 token 重叠程度。
- section_type 是否一致。
- 页码距离是否异常。
- 是否存在编号冲突、标题冲突或关键 token 冲突。

### 3. 低置信对齐标记

当对齐存在明显风险时，写入 `score_details["alignment"]["risk_flags"]`，并在必要时影响 `ClausePair.match_confidence`。

建议风险标记：

```text
CLAUSE_ALIGNMENT_LOW_CONFIDENCE
CLAUSE_NUMBER_CONFLICT
TITLE_MATCH_TEXT_MISMATCH
TEXT_MATCH_NUMBER_MISMATCH
CRITICAL_TOKEN_MISMATCH
POSSIBLE_CLAUSE_MISALIGNMENT
```

第一版不要直接把这些标记全部提升为用户可见风险结论。它们先作为 debug 和质量分析依据，避免产生过度告警。

### 4. Debug Artifact

扩展现有 `clause_matches.json`，保留原字段并增加 alignment 诊断：

```json
{
  "original_clause_id": "O001",
  "compare_clause_id": "N001",
  "original_clause_no": "3.1",
  "compare_clause_no": "3.1",
  "score": 93.2,
  "match_method": "number_title_body",
  "match_confidence": "NORMAL",
  "score_details": {
    "alignment": {
      "number_match": true,
      "title_match": true,
      "body_similarity": 0.92,
      "critical_token_overlap": 1.0,
      "risk_flags": []
    }
  }
}
```

可新增或扩展 match summary，包含：

- `total_original_clauses`
- `total_compare_clauses`
- `matched_clause_count`
- `unmatched_original_count`
- `unmatched_compare_count`
- `low_confidence_alignment_count`
- `match_method_counts`
- `risk_flag_counts`

### 5. 回归验证接入

本阶段必须通过 Phase 5 质量回归验证。

每次实现完成后至少运行：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p1 \
  --fail-on-regression
```

如果有 baseline，则使用：

```bash
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/precision-p1 \
  --fail-on-regression
```

判断重点：

- `precision` 不下降。
- `recall` 不下降。
- `false_positive_count` 不增加。
- `false_negative_count` 不增加。
- `evidence_hit_rate` 不下降。
- 低置信对齐 case 能在 debug artifact 中定位。

## 数据兼容策略

本阶段优先使用现有字段承载新信息：

- `Clause.clause_key`：可承载更稳定的条款 key。
- `ClausePair.score_details`：承载 alignment diagnostics。
- `ClausePair.match_candidates`：承载候选摘要。
- `ClausePair.match_confidence`：承载 `NORMAL` 或 `LOW` 等已有信号。
- `DiffItem.match_score_details`：透传最终用户和报告可用的匹配细节。

不新增 API 必填字段，不改变现有响应的字段语义。

## 测试策略

新增或扩展测试应覆盖：

- 金额、日期、百分比、合同编号等关键 token 提取。
- 全半角、标点、空白、换行对 fingerprint 的影响。
- 相同编号但正文差异过大时产生 `TITLE_MATCH_TEXT_MISMATCH` 或相关风险。
- 正文高度相似但编号冲突时产生 `TEXT_MATCH_NUMBER_MISMATCH`。
- 关键 token 冲突时产生 `CRITICAL_TOKEN_MISMATCH`。
- 低置信对齐不会直接丢失真实差异。
- `clause_matches.json` 输出 alignment 诊断。
- Phase 5 quality regression smoke 通过。

优先测试文件：

- `backend/tests/test_clause_alignment.py`
- `backend/tests/test_matcher_optimization.py`
- `backend/tests/test_text_cleaning_quality.py`
- `backend/tests/test_compare_integration.py`

## 分阶段实施建议

### Task 1：新增 clause alignment fingerprint

创建 `backend/app/services/clause_alignment.py`，实现关键 token 提取和 fingerprint 生成。

### Task 2：接入 ClauseSplitter 或 Clause 构建流程

写入更稳定的 `Clause.clause_key`，并保持 `normalized_text` 和 `match_text` 兼容。

### Task 3：增强 ClauseMatcher 诊断

在候选和最终 `ClausePair.score_details` 中增加 `alignment` 诊断。

### Task 4：低置信对齐风险标记

基于编号、标题、正文相似度和关键 token 冲突设置 alignment risk flags，并在必要时降低 `match_confidence`。

### Task 5：debug artifact 和 summary

扩展 `clause_matches.json` 或 match summary，使低置信对齐可被定位。

### Task 6：回归验证和中文说明

使用 Phase 5 质量回归体系验证，并补充中文开发说明或使用说明。

## 风险与控制

- 风险：过度依赖编号会导致编号错识别时漏报。  
  控制：编号只作为一个信号，必须结合标题、正文和关键 token。

- 风险：过度依赖正文相似度会错配相似模板条款。  
  控制：对关键 token 冲突和 section mismatch 加风险标记。

- 风险：新增诊断过多导致报告噪声。  
  控制：第一版先进入 debug artifact，不大量提升用户可见告警。

- 风险：优化对齐后改变 diff 数量，引入质量退化。  
  控制：每个任务通过 Phase 5 质量回归门禁。

## 验收标准

本阶段完成后，系统应能做到：

- 对每个重要 `ClausePair` 说明主要匹配依据。
- 对低置信条款对齐给出明确风险标记。
- 在 debug artifact 中统计对齐质量。
- 不破坏现有 API 兼容性。
- 不降低 Phase 5 gold case 的 precision、recall 和 evidence hit rate。
- 为后续表格字段级比对和语义等价判断提供稳定条款基础。
