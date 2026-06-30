# Precision Phase 2C：关键字段变化保护设计

## 目的

Precision Phase 2C 用于提升差异生成层对合同关键字段变化的识别、保护和可解释性。

Phase 2B 已经把 matcher 错配和高风险匹配过度自信的问题向下压了一层。下一阶段重点不再是“条款是否匹配”，而是“条款匹配正确后，真实业务字段变化是否被稳定生成、准确高亮，并且不会被质量层当作低价值噪声抑制”。

核心目标：

- 关键字段变化必须稳定出现在 diff 结果中。
- 关键字段变化应有明确 review flag，方便质量回归和人工审查定位。
- 关键字段变化不应被 `DiffQualityProcessor` 的低价值噪声规则误删。
- 高亮范围尽量落在字段 token 本身，而不是整句或整段。
- 不把明显 OCR 噪声、排版重排或纯符号变化误升为关键字段变化。

## 当前基础

现有差异链路：

```text
ClausePair
  -> app.services.diff.builder.build_modify()
  -> range_refiner.changed_snippets()
  -> spatial repair / value coverage repair
  -> DiffItem
  -> DiffQualityProcessor
  -> quality regression / attribution
```

当前关键文件：

- `backend/app/services/diff/builder.py`
  - `build_modify()` 创建 clause 级 `MODIFY` diff。
  - `review_flags()` 依据 matcher 和结构风险写入 review flags。
- `backend/app/services/diff/range_refiner.py`
  - `changed_snippets()` 生成文本差异片段和 `TextRange`。
  - 已有 numeric / percent unit range 扩展逻辑。
- `backend/app/services/diff_quality.py`
  - `DiffQualityProcessor` 负责低价值噪声抑制、关键变化标记、结构风险降级。
  - 已有 `CRITICAL_VALUE_CHANGE`、`POSSIBLE_OCR_NOISE`、`suppressed_low_value_noise` 等机制。
- `backend/tests/test_range_refiner.py`
  - 覆盖 range refiner 的字段范围、高亮类型和重排噪声。
- `backend/tests/test_diff_match_patch_engine.py`
  - 覆盖 end-to-end `DiffEngine().build_diffs()` 的 diff range 行为。
- `backend/tests/test_quality_attribution.py`
  - 覆盖质量归因输出，可在后续扩展关键字段 diff attribution。

## 推荐方案

采用“字段级 diff guard 薄层”，不做大规模字段抽取器重构。

```text
changed_snippets / spatial repair
  -> critical field diff guard
  -> DiffItem.review_flags + match_score_details hint
  -> DiffQualityProcessor protection
  -> attribution / regression
```

第一版 guard 只识别高价值、可规则化字段：

1. `AMOUNT`
   - 人民币、元、万元、亿元、`¥`、`￥`。
   - 例：`1000元` -> `5000元`。
2. `DATE`
   - `2026年6月30日`、`2026-06-30`、`2026.6.30`。
   - 例：`2026年6月30日` -> `2027年7月31日`。
3. `PERCENT_RATE`
   - `6%`、`13%`、`‰`、`千分之一`。
   - 例：`6%增值税` -> `13%增值税`。
4. `DURATION`
   - `30日`、`十个工作日`、`12个月`。
   - 例：`30日内付款` -> `45日内付款`。
5. `QUANTITY`
   - `3台`、`10套`、`5份`、`2批`。
   - 例：`3台设备` -> `5台设备`。
6. `PARTY_ROLE`
   - 甲方、乙方、买方、卖方、供应商、客户等角色附近的主体或角色变化。
   - 第一版只做保守标记，不做复杂主体名归一化。

## 非目标

本阶段不做：

- LLM 语义复核。
- 完整合同字段抽取系统。
- 自动法律风险判断。
- 大规模重写 `DiffQualityProcessor`。
- 重构 matcher。
- OCR 模型路由调整。
- 前端审核工作台。
- API 响应结构迁移。
- 提交真实合同或未脱敏 debug artifacts。

## 数据结构设计

优先复用现有 `DiffItem`，不改公开 API model。

建议把字段级诊断写入 `DiffItem.match_score_details`，该字段当前已经用于承载 matcher `score_details`，可扩展调试 hint。

新增字段：

```json
{
  "critical_field_diff_types": ["AMOUNT", "DATE"],
  "critical_field_guard_applied": 1.0
}
```

字段含义：

- `critical_field_diff_types`：当前 diff 命中的关键字段类型，按出现顺序去重。
- `critical_field_guard_applied`：是否命中字段级 guard。

同时在 `review_flags` 中追加：

```text
CRITICAL_FIELD_CHANGE
CRITICAL_FIELD_AMOUNT_CHANGE
CRITICAL_FIELD_DATE_CHANGE
CRITICAL_FIELD_PERCENT_RATE_CHANGE
CRITICAL_FIELD_DURATION_CHANGE
CRITICAL_FIELD_QUANTITY_CHANGE
CRITICAL_FIELD_PARTY_ROLE_CHANGE
```

`CRITICAL_FIELD_CHANGE` 是聚合标记，类型级 flag 用于细分统计。

## 组件边界

### 1. Critical Field Diff Guard

建议新建文件：

```text
backend/app/services/diff/critical_field_guard.py
```

职责：

- 接收 `DiffItem` 或原文/改文 change snippets。
- 识别关键字段变化类型。
- 返回字段类型和 review flags。
- 不负责 matcher、不负责 OCR、不负责 evidence bbox。

建议接口签名：

```text
critical_field_diff_types(
    original_text: str,
    compare_text: str,
    original_snippet: str,
    compare_snippet: str,
) -> list[str]
```

以及：

```text
critical_field_review_flags(field_types: list[str]) -> list[str]
```

### 2. Diff Builder Integration

在 `backend/app/services/diff/builder.py` 的 `build_modify()` 里，在 spatial repair 和 `review_flags(pair)` 后应用 guard：

```text
build ranges
  -> rebuild snippets if spatial repair changed ranges
  -> flags = review_flags(pair)
  -> field_types = critical_field_diff_types(original_text, compare_text, original_snippet, compare_snippet)
  -> flags += critical_field_review_flags(field_types)
  -> match_score_details += critical field debug fields
```

注意：

- 只对 `source_type == "clause"` 的 `MODIFY` 生效。
- `ADD` / `DELETE` 暂不做字段级 guard，避免把整条新增/删除都过度标记为关键字段变化。
- 如果 `original_snippet` / `compare_snippet` 为空，不打关键字段变化标记。

### 3. Diff Quality Protection

在 `DiffQualityProcessor` 中保证：

- 带 `CRITICAL_FIELD_CHANGE` 的 clause diff 不被 `_suppress_low_value_noise()` 删除。
- 带字段级 flag 的 diff 应保持或升级为 `CRITICAL_VALUE_CHANGE`。
- 如果同时带 OCR 风险 flag，不删除 diff，但可保留 `NEEDS_REVIEW`。

建议最小修改：

```text
_suppression_reason()
  -> 如果 review_flags 包含 CRITICAL_FIELD_CHANGE，返回 ""

_is_critical_change()
  -> 如果 review_flags 包含 CRITICAL_FIELD_CHANGE，返回 True
```

这样可以复用现有 `CRITICAL_VALUE_CHANGE` 标记和决策链路。

### 4. Attribution Boundary

Phase 2C 第一版不改 `quality_attribution.json` schema，避免把 diff payload 归因扩展和字段 guard 实现混在同一阶段。

本阶段只要求字段级信息进入实际 diff payload：

```text
CRITICAL_FIELD_CHANGE
CRITICAL_FIELD_AMOUNT_CHANGE
CRITICAL_FIELD_DATE_CHANGE
CRITICAL_FIELD_PERCENT_RATE_CHANGE
CRITICAL_FIELD_DURATION_CHANGE
CRITICAL_FIELD_QUANTITY_CHANGE
CRITICAL_FIELD_PARTY_ROLE_CHANGE
```

后续若需要在 `quality_attribution.json` 中聚合字段级 diff 归因，应作为 Phase 2C+ 或 Phase 2D 的独立小阶段处理。

## 检测策略

### 1. 字段 token 识别

使用保守正则，不做复杂语义理解。

建议第一版规则：

- `AMOUNT`
  - `(?:人民币|¥|￥)?\d[\d,]*(?:\.\d+)?(?:万|亿)?元`
  - `\d[\d,]*(?:\.\d+)?(?:万元|亿元)`
- `DATE`
  - `\d{4}年\d{1,2}月\d{1,2}日`
  - `\d{4}[-/.]\d{1,2}[-/.]\d{1,2}`
- `PERCENT_RATE`
  - `\d+(?:\.\d+)?\s*[%‰]`
  - `千分之[一二三四五六七八九十\d]+`
- `DURATION`
  - `\d+(?:\.\d+)?\s*(?:个工作日|工作日|日|天|个月|月|年)`
  - `[一二三四五六七八九十]+个工作日`
- `QUANTITY`
  - `\d+(?:\.\d+)?\s*(?:台|套|个|项|批|份|件|人天)`
- `PARTY_ROLE`
  - 第一版只识别角色词出现在 changed snippet 附近：`甲方|乙方|买方|卖方|供应商|客户`。

### 2. 变化判断

字段 guard 必须比较左右两侧，不应只因为单侧出现字段就标记为关键变化。

建议规则：

- 同一字段类型在两侧均有 token，且规范化 token 集合不同，标记该类型。
- 一侧有字段 token，另一侧无对应字段 token，但两侧 snippets 都非空，标记该类型。
- 如果两侧 token 规范化后相同，不标记。
- 如果变化文本只包含 OCR 风格符号或布局弱标点，不标记。

### 3. 范围保护

第一版不强制重写所有 range。只在 `range_refiner` 已能定位字段 token 时保护 diff 不被抑制。

但应补两个 focused tests：

- 金额变化时，高亮范围覆盖金额 token。
- 日期变化时，高亮范围覆盖日期 token。

后续若发现高亮仍过粗，再进入 Phase 2D 或 Phase 3 的 range refinement。

## 测试策略

### Focused Unit Tests

新增：

```text
backend/tests/test_critical_field_guard.py
```

覆盖：

- `1000元` -> `5000元` 命中 `AMOUNT`。
- `2026年6月30日` -> `2027年7月31日` 命中 `DATE`。
- `6%` -> `13%` 命中 `PERCENT_RATE`。
- `30日` -> `45日` 命中 `DURATION`。
- `3台` -> `5台` 命中 `QUANTITY`。
- `甲方` -> `乙方` 或角色附近主体变化命中 `PARTY_ROLE`。
- `1000 元` -> `1000元` 不命中。
- `2026.6.30` -> `2026-06-30` 不命中或保持可配置的等价处理。
- 纯标点、空格、换行变化不命中。

### Diff Builder Tests

扩展：

```text
backend/tests/test_diff_match_patch_engine.py
```

覆盖：

- 金额变化生成 `CRITICAL_FIELD_CHANGE` 和 `CRITICAL_FIELD_AMOUNT_CHANGE`。
- 日期变化生成 `CRITICAL_FIELD_DATE_CHANGE`。
- `match_score_details.critical_field_diff_types` 写入。
- 关键字段变化 diff 保持 `MODIFY`。

### Diff Quality Tests

新增或扩展：

```text
backend/tests/test_evaluate_ocr_compare_quality.py
backend/tests/test_run_quality_regression.py
```

覆盖：

- 带 `CRITICAL_FIELD_CHANGE` 的 diff 不被 `_suppress_low_value_noise()` 删除。
- 即使存在 OCR review flag，关键字段 diff 仍保留并进入 `NEEDS_REVIEW` 或 `CRITICAL_VALUE_CHANGE`。

### Regression Smoke

必须运行：

```bash
cd backend
python -m pytest tests/test_critical_field_guard.py tests/test_diff_match_patch_engine.py tests/test_quality_attribution.py -v
python -m compileall app tests
python -m ruff check .
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2c-final \
  --run-id precision-p2c-final \
  --fail-on-regression
python -m pytest
```

## 成功标准

- 关键字段变化 diff 带 `CRITICAL_FIELD_CHANGE` 和字段类型 flag。
- 金额、日期、比例、期限、数量至少各有 focused tests。
- 关键字段变化不会被低价值噪声抑制。
- 纯排版、标点、空格变化不会误升为关键字段变化。
- 现有质量回归不退化：
  - `precision` 不下降。
  - `recall` 不下降。
  - `evidence_hit_rate` 不下降。
  - `false_positive_count` 不增加。
  - `false_negative_count` 不增加。
- 全量 backend tests 通过。

## 风险与缓解

### 风险 1：正则过宽导致误报

缓解：

- 只在左右 snippets 都有实际差异时标记。
- 规范化后相同的 token 不标记。
- 纯标点/空格/换行不标记。
- 不对 `ADD` / `DELETE` 第一版启用字段 guard。

### 风险 2：字段 guard 与 OCR 噪声冲突

缓解：

- 字段变化不直接标为可信最终结论，只保护 diff 不被删除。
- OCR 风险 flag 仍可让 diff 进入 `NEEDS_REVIEW`。

### 风险 3：范围高亮仍然过粗

缓解：

- 第一版只要求关键字段 diff 不丢失。
- 高亮范围优化作为后续独立阶段处理，避免 Phase 2C 过大。

### 风险 4：复用 `match_score_details` 语义不清

缓解：

- 使用明确字段名 `critical_field_diff_types` 和 `critical_field_guard_applied`。
- 不覆盖 matcher 原有字段。
- 文档说明这些字段是 diff guard hint，不是 matcher score。

## 后续方向

Phase 2C 完成后，后续可选：

1. **Phase 2D：字段级高亮范围优化**
   - 当 range 过粗成为主要问题时，专门优化 `range_refiner`。
2. **Phase 2E：真实任务关键字段 gold case 扩容**
   - 从真实任务中脱敏沉淀金额、日期、期限、主体变化样本。
3. **Phase 3：ClauseSplitter / split-merge 结构漂移调优**
   - 处理切分、合并、拆分导致的漏报误报。
