# Precision Phase 2B：基于质量归因的 matcher 风险感知调优设计

## 目的

Precision Phase 2B 用于把 Phase 2A 的质量归因结果转化为可回归、可解释的 matcher 风险感知调优。

本阶段不改名为 lite。它仍然是正式的 matcher 调优阶段，但调优输入采用组合方式：

- 现有 gold case。
- Phase 2A 生成的 `quality_attribution.json`。
- 后续真实合同任务导出的 debug artifacts。

真实任务 debug artifacts 只用于本地分析，不提交仓库。有价值的真实失败样本必须脱敏或抽象后，再沉淀为可提交的 gold case 或构造型 matcher fixture。

核心目标：

- 降低 matcher 错配导致的误报和漏报。
- 降低高风险候选的过度自信。
- 让 matcher 的关键决策能被 `score_details`、`match_confidence` 和 Phase 2A attribution 解释。

## 成功标准

- 高风险错配不再以 `NORMAL` confidence 输出。
- `same_clause_key_weighted` 不再无条件压过正文覆盖不足、关键 token 冲突或 alignment risk。
- `body_weighted_similarity` 不再对关键字段冲突候选给出过高信心。
- `same_clause_no_low_similarity`、`section_mismatch_blocked` 等风险方法能进入低置信或可疑样本分析。
- 新增 guard 都有 focused tests。
- 质量回归不退化：`precision`、`recall`、`evidence_hit_rate` 不下降，误报/漏报门禁不失败。
- Phase 2A attribution 能解释本阶段新增策略的作用。

## 当前基础

现有 matcher 链路：

```text
ClauseSplitter -> ClauseMatcher -> DiffEngine -> DiffQuality -> Report
```

`ClauseMatcher` 当前关键结构：

- `_score_details()`：计算 `clause_key_score`、`clause_no_score`、`title_score`、`body_score`、`body_length_coverage`、`business_token_score`、`alignment` 等。
- `_weighted_score()`：根据各项 score 生成候选总分。
- `_candidate_acceptable()`：决定候选是否可进入匹配。
- `_match_method()`：给匹配打方法标签，例如 `same_clause_key_weighted`、`body_weighted_similarity`。
- `_match_confidence()`：输出 `LOW`、`MEDIUM`、`NORMAL`。
- `match_candidates`：保留候选摘要和 `score_details`，可被 debug artifact 与 Phase 2A attribution 读取。

Phase 1 已提供：

- `score_details.alignment`
- `alignment.risk_flags`
- `critical_token_overlap`
- `body_similarity`
- `page_distance`
- `match_matrix_summary.low_confidence_alignment_count`
- `alignment_risk_flag_counts`

Phase 2A 已提供：

- `quality_attribution.json`
- `suspicious_matches`
- `attribution_tags`
- `SAME_KEY_LOW_BODY_COVERAGE`
- `BODY_ONLY_MATCH`
- `KEY_TOKEN_CONFLICT`
- `LOW_CONFIDENCE_ALIGNMENT`

## 推荐方案

采用“风险感知 guard 层”方案，而不是大规模重写 matcher 权重。

```text
existing score details
  -> risk guard evaluation
  -> guarded score / confidence / method
  -> existing match output and debug artifacts
```

第一版 guard 只处理明确、高价值、可测试的风险：

1. 同 clause key 但正文覆盖不足。
2. 关键 token 冲突。
3. 编号相同但正文相似度过低。
4. body-only 相似但 alignment 风险高。
5. section mismatch 候选不得成为普通高置信匹配。

这些 guard 不应隐藏真实差异。风险候选可以保留为匹配，但必须降低 confidence、限制强匹配方法或降低 score 上限。

## 非目标

本阶段不做：

- LLM 语义复核。
- 自动法律风险判断。
- 大规模重写 `ClauseMatcher`。
- 自动学习权重。
- 前端审核工作台。
- OCR 模型路由调整。
- diff quality 规则大改。
- API 结构迁移。
- 提交真实合同或真实 debug artifacts。

## 调优输入策略

### 1. 现有 gold case

用于验证不退化。每次实现后必须跑质量回归。

### 2. Phase 2A attribution report

用于决定优先治理的风险类型。Phase 2B 实施前应先运行：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2b-input \
  --run-id precision-p2b-input \
  --fail-on-regression

python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2b-input
```

重点看：

- `aggregate.attribution_counts`
- `aggregate.alignment_risk_flag_counts`
- `aggregate.match_method_counts`
- `cases[].suspicious_matches`

### 3. 后续真实任务 debug artifacts

真实任务用于发现问题，不直接进入仓库。

允许本地使用：

```text
storage/tasks/<task-id>/debug/clause_matches.json
storage/tasks/<task-id>/debug/match_matrix_summary.json
```

不允许提交：

- 原合同。
- 新合同。
- 包含真实条款文本的 debug artifact。
- 由真实合同生成且未脱敏的 expected/gold case。

可提交：

- 脱敏后的最小复现 fixture。
- 抽象化构造型 matcher test。
- 不含敏感内容的质量归因统计。

## 风险感知 guard 设计

### 1. Same Key Low Coverage Guard

问题：

`same_clause_key_weighted` 当前可能因为 `clause_key_score >= 96` 和 `body_score >= 35` 提前接受候选。若正文覆盖不足，容易把同编号或同 key 的不同条款强行匹配。

建议策略：

- 当 `clause_key_score >= 96` 且 `body_length_coverage < 0.70` 时：
  - 不允许 `match_confidence == "NORMAL"`。
  - `match_method` 应能体现风险，或至少 `score_details` 写入 guard flag。
  - 若 `body_score < 55` 且无标题强匹配，不应凭 clause key 直接接受。

建议新增 score detail：

```json
{
  "matcher_risk_flags": ["SAME_KEY_LOW_BODY_COVERAGE"]
}
```

### 2. Critical Token Conflict Guard

问题：

金额、日期、期限、主体、数量、税率等关键 token 冲突时，即使正文整体相似，也不能高置信输出。

建议策略：

- 当 `alignment.risk_flags` 包含 `CRITICAL_TOKEN_MISMATCH`：
  - `match_confidence` 必须是 `LOW`。
  - 若同时 `critical_token_overlap == 0.0` 且 `body_score < 85`，限制候选 score 上限。
  - 不直接删除候选，避免把真实修改误判为 add/delete。

### 3. Same Number Low Body Similarity Guard

问题：

编号相同但正文严重不同，可能是合同重排、条款替换或分条错误。应保留诊断，但不能过度自信。

建议策略：

- 当 `clause_no_score == 100` 且 `alignment.body_similarity < 0.45`：
  - `matcher_risk_flags` 追加 `SAME_NUMBER_LOW_BODY_SIMILARITY`。
  - `match_confidence` 为 `LOW`。
  - 若标题也不匹配，不允许 `same_clause_no_weighted` 成为正常强匹配。

### 4. Body Only High Risk Guard

问题：

`body_weighted_similarity` 容易在模板化合同中把正文相似但业务对象不同的条款匹配起来。

建议策略：

- 当 `match_method == "body_weighted_similarity"` 且存在 alignment risk：
  - `match_confidence` 为 `LOW`。
  - `matcher_risk_flags` 追加 `BODY_ONLY_ALIGNMENT_RISK`。
  - 候选仍保留在 `match_candidates` 供 attribution 定位。

### 5. Section Mismatch Guard

问题：

跨 section 的同编号或相似正文不应成为普通匹配。

现有 `_candidate_acceptable()` 已阻止 section_type 不同的候选进入普通匹配，并通过 section mismatch candidate summary 保留诊断。本阶段只做回归保护：

- 确认 `section_mismatch_blocked` 不会输出 `NORMAL`。
- 确认 attribution 能看到这类候选。

## 数据结构设计

第一版优先复用 `score_details`，不改 Pydantic model。

建议在 `score_details` 中新增：

```json
{
  "matcher_risk_flags": [
    "SAME_KEY_LOW_BODY_COVERAGE",
    "CRITICAL_TOKEN_CONFLICT",
    "SAME_NUMBER_LOW_BODY_SIMILARITY"
  ],
  "matcher_guard_applied": 1.0
}
```

字段含义：

- `matcher_risk_flags`：matcher guard 层识别到的风险。
- `matcher_guard_applied`：是否有 guard 生效，便于统计和回归分析。

`alignment.risk_flags` 仍保留原有含义，不被覆盖。

## 组件边界

### 1. Matcher Risk Guard Helper

建议在 `backend/app/services/matcher.py` 内先实现私有 helper，避免过早拆文件：

```text
_matcher_risk_flags(details, candidate or left/right)
_apply_matcher_guards(details, weighted_score)
```

如果 helper 变复杂，再考虑拆到独立模块。

职责：

- 读取现有 `details` 和 `alignment`。
- 生成 `matcher_risk_flags`。
- 为 `_weighted_score()` 或 `_match_confidence()` 提供 guard 判断。

### 2. Score Guard

职责：

- 对高风险候选设置 score 上限。
- 不改变所有候选的基础分计算，只在风险条件满足时做保守限制。

建议第一版只对明确风险做上限：

- `SAME_KEY_LOW_BODY_COVERAGE`：限制高分强匹配。
- `CRITICAL_TOKEN_CONFLICT` + 低 overlap：限制过度高分。

### 3. Confidence Guard

职责：

- 任何 matcher risk flag 非空时，默认不允许 `NORMAL`。
- 对最严重风险返回 `LOW`。
- 对弱风险可返回 `MEDIUM`。

第一版建议保守：

- `CRITICAL_TOKEN_CONFLICT`：`LOW`
- `SAME_KEY_LOW_BODY_COVERAGE`：`LOW`
- `SAME_NUMBER_LOW_BODY_SIMILARITY`：`LOW`
- `BODY_ONLY_ALIGNMENT_RISK`：`LOW`

### 4. Attribution Compatibility

Phase 2A 需要能看到本阶段 guard。

建议在 `analyze_quality_attribution.py` 中后续识别：

- `score_details.matcher_risk_flags`
- `matcher_guard_applied`

但这可以作为 Phase 2B 的后半段任务，不必和 matcher 第一刀强耦合。

## 测试策略

新增或扩展 `backend/tests/test_matcher_optimization.py`：

- same key + low body coverage 不能 NORMAL。
- same key + low body coverage 不能仅凭 key 得到过高 score。
- critical token conflict 保持 LOW。
- same clause number + low body similarity 标记 matcher risk。
- body weighted similarity + alignment risk 标记 LOW。
- section mismatch 仍被阻止或只进入可疑候选。

扩展 `backend/tests/test_quality_attribution.py`：

- 能统计 `matcher_risk_flags`。
- 能把 `SAME_KEY_LOW_BODY_COVERAGE`、`BODY_ONLY_ALIGNMENT_RISK` 等归因标签写入 `quality_attribution.json`。

回归验证：

```bash
cd backend
python -m pytest tests/test_matcher_optimization.py tests/test_quality_attribution.py -v
python -m pytest tests/test_run_quality_regression.py tests/test_evaluate_ocr_compare_quality.py -v
python -m ruff check .
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2b \
  --run-id precision-p2b \
  --fail-on-regression
python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2b
```

## 回滚策略

每个 guard 应独立提交，便于回滚。

如果质量回归失败：

1. 查看 `quality.json` 和 `quality_attribution.json`。
2. 判断失败来自 recall 下降、precision 下降，还是 evidence drift。
3. 只回滚导致失败的 guard，不回滚 Phase 1/2A 基础设施。
4. 若失败来自 gold case 过窄或标注问题，先补 gold case，再继续调优。

## 风险与缓解

### 风险 1：过度降低 score 造成漏报

缓解：

- 第一版优先降低 confidence，不轻易删除候选。
- score cap 只用于明确错配风险。
- 每条 score guard 必须有 recall 不退化验证。

### 风险 2：构造型测试过拟合

缓解：

- 使用 Phase 2A attribution 作为输入，不只依赖人工想象。
- 真实任务样本只做本地分析，脱敏后再沉淀为 fixture。

### 风险 3：matcher 复杂度继续上升

缓解：

- guard 逻辑集中在 helper。
- `score_details.matcher_risk_flags` 作为统一输出，不把规则散落在多个分支。
- 若 helper 超过可读范围，再拆独立模块。

## 交付物

- matcher 风险 guard。
- matcher risk flags 写入 `score_details`。
- attribution 对 matcher risk flags 的读取。
- focused matcher regression tests。
- quality attribution regression tests。
- 中文说明更新或新增 Phase 2B workflow 文档。

## 下一阶段承接

Phase 2B 完成后，根据 attribution 结果决定：

- 若关键字段仍漏检，进入 Phase 2C：关键字段差异保护。
- 若 gold case 覆盖不足，进入 Phase 2D：真实样本脱敏和 gold case 扩充。
- 若 matcher 风险明显下降，再考虑 Phase 3 的 diff 粒度和证据定位优化。
