# Precision Phase 2D：跨段/跨页差异连续性修复设计

## 目的

Precision Phase 2D 用于提升合同正文条款在分页、换行、OCR block 拆分场景下的比对精确度。

Phase 2B 已降低 matcher 高风险错配，Phase 2C 已保护关键字段变化，页眉页脚排除也已减少版式噪声。下一阶段重点是解决“同一条款被拆成多个 clause 后，真实小差异被放大成大段 `MODIFY`、假 `ADD` 或假 `DELETE`”的问题。

核心目标：

- 被分页或 OCR block 拆断的同一正文条款，应在进入 matcher/diff 前尽量合并。
- 合并必须保守，不能把明确的新条款、标题、表格、签署页或页眉页脚内容粘到上一条款。
- 合并后应保留 page、evidence、source block 信息，便于报告定位和 debug。
- 合并行为必须可回归、可解释，并写入 split/debug flag。

## 当前基础

现有正文 clause 链路：

```text
Document pages / TextBlock
  -> ClauseSplitter._collect_units()
  -> ParagraphBuilder.build()
  -> ClauseSplitter._detect_clause_items()
  -> _repair_adjacent_clause_boundary()
  -> _repair_continuation_boundaries()
  -> Clause
  -> matcher / diff builder / quality processor
```

关键文件：

- `backend/app/services/clause_paragraphs.py`
  - 当前已有 `ParagraphBuilder`。
  - 已处理同页视觉接近、上一段未终止、同 block 的段落合并。
  - 已写入 `PARAGRAPH_MERGED`。
- `backend/app/services/clause_splitter.py`
  - 负责 block 过滤、条款编号识别、正文 clause 构建。
  - 已有 `_repair_continuation_boundaries()`，用于弱数字、金额/数值续段修复。
  - 已有页眉页脚、表格、低置信、低密度、正文噪声过滤。
- `backend/tests/test_text_cleaning_quality.py`
  - 已覆盖 clause splitter 的段落合并、金额编号误拆、目录噪声、签署页正文判断等行为。

## 推荐方案

采用“扩展 ParagraphBuilder 的保守跨页续段合并”方案。

不新增独立后处理器，不在 diff 生成后补救，而是在 clause 构建前把明显属于同一段/同一条款的 `ClauseUnit` 合并。这样 matcher 和 diff 看到的是更稳定的 clause 输入。

```text
ClauseUnit stream
  -> ParagraphBuilder same-page continuation merge
  -> ParagraphBuilder adjacent-page continuation merge
  -> ClauseSplitter clause detection
  -> matcher / diff
```

## 非目标

本阶段不做：

- 表格关键值差异增强。
- `ADD` / `DELETE` 关键字段保护。
- LLM 语义复核。
- matcher 重构。
- OCR 模型路由调整。
- API 响应结构迁移。
- 前端展示改造。
- 提交真实合同、未脱敏 debug artifacts 或客户数据。

## 组件设计

### 1. ParagraphBuilder 跨页续段判断

在 `backend/app/services/clause_paragraphs.py` 中扩展 `ParagraphBuilder._is_continuation()`。

保留现有同页逻辑：

- 同 block 合并。
- 上一段以续接标点结尾合并。
- 上一段无终止标点，且当前段视觉接近并不像独立标签时合并。

新增相邻页跨页逻辑：

```text
previous.page_no + 1 == current.page_no
AND previous/current section_type 相同
AND current 无明确条款编号
AND previous 未以强终止标点结束
AND current 不像标题/独立标签
AND previous 位于上一页下半部或页底附近
AND current 位于下一页上半部或页首附近
AND x 缩进差异在阈值内
```

该逻辑应作为 `_visually_close()` 之外的独立方法，例如：

```python
_visually_continues_across_adjacent_pages(previous, current) -> bool
```

### 2. 强边界保护

跨页合并必须优先尊重强边界。以下情况不合并：

- `parse_marker(current.text)` 不为空，且不是已知的弱数字/金额续段。
- 当前单元 `block_type` 是标题类、表格类、页眉页脚、印章、图片等。
- 当前文本像独立标题或短标签。
- 当前和上一单元 `section_type` 不一致。
- 当前页首文本是签署页噪声，例如“以下无正文”、签章、甲方/乙方签字块。

第一版可复用现有 `ClauseSplitter` 前置过滤结果，不在 `ParagraphBuilder` 里重新实现全部 block 过滤，但应通过测试覆盖标题和新编号不误合并。

### 3. 合并标记

继续保留现有：

```text
PARAGRAPH_MERGED
```

新增：

```text
CROSS_PAGE_CONTINUATION_MERGED
```

可选新增：

```text
PARAGRAPH_CONTINUATION_MERGED
```

第一版最低要求是跨页合并写入 `CROSS_PAGE_CONTINUATION_MERGED`。如果同页合并已有 `PARAGRAPH_MERGED` 足够稳定，可以暂不新增 `PARAGRAPH_CONTINUATION_MERGED`，避免扩大改动面。

### 4. Evidence 和页码保留

合并后必须保留：

- `page_numbers`
- `evidences`
- `source_block_ids`
- `char_boxes`

现有 `_merge_units()` 已处理这些字段。Phase 2D 应复用并补充跨页 flag，不重写 evidence 结构。

## 判断策略

### 1. 上一段未结束信号

强续接：

- 上一段以 `，`、`,`、`、`、`：`、`:`、`（`、`(` 结尾。

弱续接：

- 上一段不以 `。`、`；`、`;`、`！`、`?`、`？`、`!` 结尾。
- 当前段不是标题、短标签或新条款编号。

强终止：

- 上一段以终止标点结尾时，默认不跨页合并。

### 2. 跨页位置判断

`ClauseUnit` 当前有 `bbox` 和 `page_no`，但没有直接携带页面高度。第一版可以使用相对 y 坐标的保守阈值：

- `previous.bbox.y0` 或 `previous.bbox.y1` 明显大于 `current.bbox.y0`。
- `previous.page_no + 1 == current.page_no`。
- `current.bbox.y0` 接近页面上方文本区域。
- `abs(previous.bbox.x0 - current.bbox.x0)` 小于缩进阈值。

如果现有 `BBox` 坐标不是归一化坐标，实施计划中应先通过测试 fixture 确认坐标尺度，再选择阈值。设计上不要求使用页面高度，避免改 `ClauseUnit` 数据结构。

### 3. 新条款编号保护

如果 `current` 能解析为明确条款编号，不合并。

例：

```text
上一页：乙方应在收到通知后
下一页：第八条 违约责任
```

不得合并。

### 4. 金额/数量续段保护

有些 OCR 切分会让下一段以数字开头，例如：

```text
上一页：合同总价为人民币
下一页：100000元整，包含税费。
```

这类不是新条款，应允许作为续段。规则应复用或兼容 `ClauseSplitter._is_quantity_or_amount_marker()` 和 `_is_amount_or_value_continuation()` 的思想。

第一版可以先覆盖典型金额、日期、数量开头的续段，避免误拆为 clause。

## 测试设计

建议新增或扩展：

```text
backend/tests/test_text_cleaning_quality.py
```

重点测试：

1. **跨页续段合并**
   - 上一页条款末尾无终止标点。
   - 下一页开头是普通正文。
   - 输出一个 clause，包含两页文本。
   - `split_flags` 包含 `CROSS_PAGE_CONTINUATION_MERGED`。
   - `page_numbers` 包含两页。

2. **跨页新条款不合并**
   - 下一页以明确条款编号开始。
   - 输出两个 clause。

3. **跨页标题不合并**
   - 下一页是“违约责任”等标题。
   - 不并入上一条款。

4. **金额开头续段可合并**
   - 上一页以“合同总价为人民币”结束。
   - 下一页以“100000元整”开始。
   - 应合并为同一 clause。

5. **同页已有合并不回退**
   - 保留已有段落合并测试。

6. **签署页/以下无正文不合并**
   - 下一页开头是“以下无正文”或签章文本。
   - 不并入上一条款。

## 验证方式

最低验证命令：

```bash
cd backend
python -m pytest tests/test_text_cleaning_quality.py -v
python -m pytest tests/test_clause_alignment.py tests/test_matcher_optimization.py tests/test_diff_match_patch_engine.py -v
python -m compileall app tests
python -m ruff check .
```

## 风险和缓解

### 风险 1：过度合并相邻条款

缓解：

- 当前段有明确编号时不合并。
- 当前段像标题/短标签时不合并。
- 上一段以强终止标点结尾时不跨页合并。
- 测试覆盖新条款、标题、签署页边界。

### 风险 2：坐标阈值依赖 OCR 输出尺度

缓解：

- 第一版不依赖页面高度。
- 使用相邻页、缩进、文本边界多个信号组合。
- 阈值在 focused fixture 中固化。

### 风险 3：合并后 evidence 丢失

缓解：

- 复用现有 `_merge_units()`。
- 测试断言 `page_numbers`、`source_block_ids` 和 split flags。

### 风险 4：真实合同样例不足

缓解：

- 先用脱敏构造 fixture 固化常见分页续段。
- 后续从真实 debug artifacts 中抽象最小复现，不提交原始合同。

## 成功标准

- 跨页续段在 clause 层稳定合并。
- 明确新条款、标题、签署页不被误合并。
- 合并后的 clause 保留 evidence 和页码。
- 现有 matcher 和 diff 测试不回退。
