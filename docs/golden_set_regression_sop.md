# Golden Set 与质量回归测试使用手册

## 目标

本文档说明合同差异比对系统从一次真实比对任务沉淀为 golden set，并在后续代码改动后自动做质量回归测试的完整流程。

golden set 的目标不是直接提升算法精度，而是建立一套可重复验证机制，用来回答：

- 这次改动是否漏掉了原本应该识别的差异？
- 这次改动是否新增了误报？
- 差异证据定位是否发生漂移？
- OCR 质量、模型路由、matcher、差异生成层的改动是否让整体质量退化？

## 核心概念

### gold case 目录

一个 gold case 是一个独立目录，通常位于：

```text
backend/tests/fixtures/ocr_compare_cases/<case_id>
```

目录内通常包含：

```text
actual.json
expected.json
README.md
```

### actual.json

`actual.json` 是某次合同对比任务的系统实际输出快照，来源通常是：

```text
storage/tasks/<task_id>/task.json
```

它包含任务状态、OCR 质量画像、差异列表、证据定位、review flags、模型路由分析所需数据等。

注意：如果 gold case 目录里只有 `actual.json`，没有 `original.pdf` 和 `compare.pdf`，质量评估会直接读取这个历史快照，不会重新执行最新比对算法。

### expected.json

`expected.json` 是人工审核后的期望结果，也就是质量评估的 gold 标准。

关键字段：

- `expected_diffs`：人工认可的期望差异列表。
- `review_status`：标注状态，支持 `DRAFT`、`APPROVED`、`REJECTED`。
- `diff_type`：差异类型，例如 `ADD`、`DELETE`、`MODIFY`。
- `source_type`：差异来源，例如 `metadata`、`clause`、`table`、`seal`、`header_footer`。
- `title_contains`：期望匹配的标题片段。
- `original_contains`：原合同侧稳定文本片段。
- `compare_contains`：对比合同侧稳定文本片段。
- `expected_evidence`：可选，期望证据位置，用于验证高亮/定位是否准确。

质量评估只把 `review_status` 为空、缺失或 `APPROVED` 的条目计入 gold。`DRAFT` 和 `REJECTED` 不计入 precision、recall、false negative 等核心指标。

### Schema 1.1 元数据

新的 `expected.json` 推荐使用 `schema_version: "1.1"`。Schema 1.1 在 case 级别增加以下元数据，用于区分数据集用途和回归门禁范围：

- `dataset_split`：数据集分组，支持 `dev`、`regression`、`holdout`、`adversarial`。旧数据如果没有这个字段，评估器会按 `legacy` 处理。
- `case_tags`：样本类型标签，例如 `metadata_date`、`header_footer`、`ocr_noise`、`table`，用于筛选和人工追溯。
- `baseline_required`：是否要求该 case 进入正式回归门禁。日常从真实任务导出的 draft case 先放在 `dev`，人工确认并脱敏后再改为 `regression`，并按需要设置 `baseline_required: true`。

示例：

```json
{
  "schema_version": "1.1",
  "case_id": "case-001",
  "source_task_id": "task-001",
  "dataset_split": "dev",
  "case_tags": ["exported", "requires_human_review"],
  "baseline_required": false,
  "expected_diffs": []
}
```

### README.md

`README.md` 记录 case 来源、原始文件名、对比文件名、OCR 状态、风险页数、人工审核步骤和敏感数据提醒。

它不是评估输入，但用于后续维护和人工追溯。

### baseline

baseline 是某个代码版本下的质量评估结果快照，通常保存为：

```text
backend/.ocr-compare-quality/baselines/<version>.json
```

后续改代码后，回归脚本会把当前评估结果和 baseline 对比，判断是否出现质量退化。

## 总流程

推荐流程如下：

```text
1. 跑一次合同对比任务
2. 从 storage/tasks/<task_id> 导出 draft gold case
3. 人工审核 expected.json
4. 将确认真实存在的差异标为 APPROVED
5. 跑一次质量评估，确认 gold case 可用
6. 建立 baseline
7. 修改 OCR、matcher、差异生成、证据定位等代码
8. 跑质量回归
9. 分析报告
10. 决定修代码、补 gold case、调整阈值或更新 baseline
```

## 可视化工作台推荐流程

如果本地已启动前端和后端服务，可以通过质量工作台完成常见 golden set 操作：

```text
/quality/workbench
```

主导航展开后，也可以从：

```text
合同智能对比 -> 质量工作台
```

进入页面。

质量工作台适合处理以下高频动作：

- 查看 case 列表、数据集分组、标签、actual/expected 数量。
- 对 expected diff 执行 `APPROVED`、`DRAFT`、`REJECTED` 标注。
- 将已确认误报标记为 `should_not_match_again`，用于后续已知误报回归门禁。
- 人工补录漏报 expected diff。
- 编辑 `expected_evidence`，沉淀证据级 gold。
- 直接触发质量评估和质量回归，并查看 precision、recall、已知误报回归和门禁失败结果。

推荐实践：

- 少量快速审核优先使用质量工作台，减少手改 JSON 的格式错误。
- 大批量整理、脱敏、字段重排仍然可以直接编辑 `expected.json`。
- 每次通过工作台标注后，仍应运行一次后端回归命令，确认 CLI 与 UI 结果一致。
- 不要把包含真实敏感合同内容的导出 case 直接提交到仓库；先脱敏，再进入正式 `regression` split。

## 任务级误报复盘

质量工作台支持对已存在的历史任务做只读误报复盘。入口仍然是：

```text
/quality/workbench
```

在“任务复盘”区域输入：

```text
storage/tasks/<task_id>
```

中的 `<task_id>`，例如：

```text
fb67bf36-73bd-48c3-a7b3-59696ed4fb12
```

工作台会读取该任务目录下的 `task.json`，并用当前代码重新运行质量过滤逻辑。它不会重新执行 OCR、条款切分、matcher 或完整比对，也不会改写 `task.json`、`actual.json`、`expected.json` 或任何 golden set 文件。

重点查看：

- `历史 diff 数`：该任务当时输出的差异数量。
- `保留 diff 数`：当前质量过滤后仍保留的历史差异数量。
- `抑制 diff 数`：当前质量过滤会过滤掉的历史差异数量。
- `被抑制 diff`：误报候选及其抑制原因，例如 `clause_ocr_noise` 或 `single_latin_layout_glyph_noise`。
- `保留 diff`：仍需要人工判断的差异，重点看标题、片段、review flags 和 matcher 分数。

推荐用法：

- 修复某类误报规则后，先用任务复盘确认目标 diff 是否已经被抑制。
- 如果目标误报仍在“保留 diff”中，继续查看 review flags、证据片段和 debug artifacts，定位规则未命中的原因。
- 如果任务复盘结果符合预期，再决定是否把该任务脱敏后导出为 golden set。
- 任务复盘不是回归测试本身；它用于快速排查真实任务，正式质量门禁仍应依赖 golden set 和质量回归命令。

## 1. 从对比任务导出 draft gold case

先确保已经完成一次合同对比任务，并且任务目录存在：

```text
storage/tasks/<task_id>/task.json
```

导出命令：

```bash
cd backend
python scripts/export_ocr_compare_gold_case.py \
  ../storage/tasks/<task_id> \
  tests/fixtures/ocr_compare_cases/<case_id>
```

示例：

```bash
cd backend
python scripts/export_ocr_compare_gold_case.py \
  ../storage/tasks/da0e1282-e239-42dd-92dd-447b8f7ec136 \
  tests/fixtures/ocr_compare_cases/da0e1282-e239-42dd-92dd-447b8f7ec136
```

导出后会生成：

```text
tests/fixtures/ocr_compare_cases/<case_id>/actual.json
tests/fixtures/ocr_compare_cases/<case_id>/expected.json
tests/fixtures/ocr_compare_cases/<case_id>/README.md
```

导出的 `expected.json` 默认是草稿，所有差异一般都是：

```json
"review_status": "DRAFT"
```

这表示它还不是可信 gold，只是系统基于实际输出生成的标注起点。

## 2. 人工审核 expected.json

打开：

```text
backend/tests/fixtures/ocr_compare_cases/<case_id>/expected.json
```

逐条检查 `expected_diffs`。

### 标为 APPROVED

当某条差异被人工确认是真实合同差异时，改为：

```json
"review_status": "APPROVED"
```

同时检查并修正以下字段：

```json
{
  "diff_type": "MODIFY",
  "source_type": "clause",
  "title_contains": "稳定标题片段",
  "original_contains": "原合同侧稳定文本片段",
  "compare_contains": "对比合同侧稳定文本片段",
  "review_status": "APPROVED"
}
```

`title_contains`、`original_contains`、`compare_contains` 不需要写整段原文，建议写短而稳定的片段。不要选择容易受 OCR 空格、换行、页码变化影响的长文本。

### 保持 DRAFT

当某条差异暂时不确定时，保持：

```json
"review_status": "DRAFT"
```

`DRAFT` 不会进入指标计算，适合保留给后续人工复核。

### 标为 REJECTED

当某条差异确认是误报时，改为：

```json
"review_status": "REJECTED"
```

`REJECTED` 不会作为 gold 期望差异参与 recall 计算。

### 误报作为负向 Golden Set

如果系统输出经人工确认是误报，保留这条 expected diff 并标为：

```json
"review_status": "REJECTED"
```

如果希望它以后不再出现，再设置：

```json
"should_not_match_again": true
```

并填写 `false_positive_reason`。后续 actual 中如果再次出现与该 rejected 条目相似的 diff，评估器会计入 `known_false_positive_regression_count`，回归门禁可以通过 `max_known_false_positive_regression_count` 和 `max_known_false_positive_regression_increase` 阻止这类已知误报回归。

示例：

```json
{
  "diff_type": "MODIFY",
  "source_type": "header_footer",
  "title_contains": "页眉版本号",
  "original_contains": "V1.0",
  "compare_contains": "V1.1",
  "review_status": "REJECTED",
  "should_not_match_again": true,
  "false_positive_reason": "页眉版本号不属于合同正文差异",
  "notes": "人工确认该类页眉变化不应生成正式差异"
}
```

### 补充漏检差异

如果人工发现系统没有识别出的真实差异，需要手工向 `expected_diffs` 增加一条 `APPROVED` 记录。

示例：

```json
{
  "diff_type": "MODIFY",
  "source_type": "metadata",
  "title_contains": "签订日期",
  "review_status": "APPROVED",
  "severity": "critical",
  "original_contains": "2026年4月 日",
  "compare_contains": "2026年4月21日"
}
```

这类条目用于检测后续系统是否仍然漏检。

### 人工补录漏报

人工发现真实差异但系统未输出时，在 `expected_diffs` 中新增一条 `APPROVED` expected diff。可以填写 `false_negative_reason` 记录漏报原因，例如 OCR 丢字、跨页切分失败、表格对齐失败或 matcher 过滤过严。

后续评估时，如果 actual 仍无法匹配这条 `APPROVED` expected diff，它会计入 `false_negative_count`，并出现在 `missed_expected_diffs` 中。

示例：

```json
{
  "diff_type": "MODIFY",
  "source_type": "metadata",
  "title_contains": "签订日期",
  "review_status": "APPROVED",
  "severity": "critical",
  "original_contains": "2026年4月 日",
  "compare_contains": "2026年4月21日",
  "false_negative_reason": "系统未识别空白日期被补全的真实差异",
  "notes": "人工补录漏报"
}
```

## 3. 可选：标注证据位置

如果需要验证高亮框或证据定位是否准确，可以添加 `expected_evidence`。

示例：

```json
"expected_evidence": [
  {
    "side": "compare",
    "page_no": 1,
    "bbox": {
      "x0": 100,
      "y0": 200,
      "x1": 260,
      "y1": 230
    }
  }
]
```

评估器会检查实际证据框和期望证据框是否有足够重叠。当前证据命中率由 `evidence_hit_rate` 体现。

## 4. 跑单次质量评估

人工审核后，先跑一次质量评估，确认这个 gold case 能正常参与指标计算。

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py \
  tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/runs/local-eval/quality.json \
  --html-output .ocr-compare-quality/runs/local-eval/html
```

只评估已经进入正式回归集的 case：

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py \
  tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/runs/local-eval/quality.json \
  --html-output .ocr-compare-quality/runs/local-eval/html \
  --dataset-split regression
```

查看 HTML 报告：

```text
backend/.ocr-compare-quality/runs/local-eval/html/index.html
```

重点检查：

- `approved_expected_count` 是否大于 0。
- `draft_expected_count` 是否符合预期。
- `unexpected_actual_diffs` 是否包含明显误报。
- `missed_expected_diffs` 是否包含系统漏检。
- `evidence_drift_diffs` 是否包含证据定位偏移。

如果 `approved_expected_count` 是 0，说明当前 case 还没有有效 gold 标注，不能作为有效质量回归依据。

## 5. 建立 baseline

当 gold case 已经人工审核，且当前代码版本的质量结果被认为可以接受时，建立 baseline。

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/baseline-v0.0.2 \
  --write-baseline .ocr-compare-quality/baselines/v0.0.2.json
```

输出：

```text
backend/.ocr-compare-quality/runs/baseline-v0.0.2/quality.json
backend/.ocr-compare-quality/runs/baseline-v0.0.2/baseline_comparison.json
backend/.ocr-compare-quality/runs/baseline-v0.0.2/run_summary.json
backend/.ocr-compare-quality/runs/baseline-v0.0.2/html/index.html
backend/.ocr-compare-quality/baselines/v0.0.2.json
```

建议只把脱敏、公开、经过批准的 fixture 和 baseline 提交到仓库。真实合同导出的 `actual.json`、`expected.json`、报告文件默认不提交。

## 6. 改代码后跑质量回归

每次修改 OCR、条款切分、matcher、差异生成、证据定位、模型路由、质量归因等逻辑后，运行：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --fail-on-regression
```

只跑 `regression` split：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --dataset-split regression \
  --fail-on-regression
```

如果回归门禁失败，命令会返回退出码 `1`。这适合后续接入 CI。

输出文件：

```text
backend/.ocr-compare-quality/runs/local-check/quality.json
backend/.ocr-compare-quality/runs/local-check/baseline_comparison.json
backend/.ocr-compare-quality/runs/local-check/run_summary.json
backend/.ocr-compare-quality/runs/local-check/html/index.html
```

## 7. 如何解读回归报告

### 核心指标

- `precision`：系统报出的差异中，有多少能匹配 approved gold。低 precision 通常意味着误报多。
- `recall`：approved gold 中，有多少被系统找到了。低 recall 通常意味着漏检。
- `false_positive_count`：系统报出但没有匹配 approved gold 的差异数量。
- `false_negative_count`：approved gold 中系统没有报出的差异数量。
- `known_false_positive_regression_count`：actual 再次命中了带 `should_not_match_again: true` 的 rejected expected diff，表示已知误报回归。
- `evidence_hit_rate`：匹配成功的差异中，证据位置命中的比例。
- `low_confidence_ratio`：实际差异中带低置信或 OCR 风险标记的比例。

### 优先查看的报告区域

先看：

```text
Failed gates
Aggregate delta
Case regression ranking
```

再看具体 case：

```text
Missed expected diffs
Unexpected actual diffs
Evidence drift
```

### 常见判断

如果 `false_negative_count` 增加，优先排查：

- 条款切分是否变化。
- matcher 是否匹配到错误段落。
- 差异生成层是否过滤过严。
- OCR 文本是否变化。

如果 `false_positive_count` 增加，优先排查：

- 是否把 OCR 空格、换行、页眉页脚当成差异。
- 是否把跨页连续文本拆成多条差异。
- 是否把低置信 matcher 结果直接生成差异。
- `expected.json` 是否漏标了真实差异。

如果 `evidence_hit_rate` 下降，优先排查：

- 证据定位是否偏移。
- OCR 坐标是否变化。
- diff range refine 是否改变。
- 跨页/跨段连续差异是否被错误拆分。

## 8. 快照回归与重新执行回归的区别

当前评估器支持两种 case 形态：

### 只有 actual.json

```text
actual.json
expected.json
README.md
```

这种模式读取历史输出快照，适合验证：

- 评估器逻辑。
- gold matching 规则。
- 报告生成。
- 质量归因统计。

但它不会重新执行最新比对算法。

### 包含 original.pdf 和 compare.pdf

```text
original.pdf
compare.pdf
expected.json
README.md
```

如果没有 `actual.json`，评估器会尝试用当前代码重新执行比对，适合验证：

- OCR 改动。
- 条款切分改动。
- matcher 改动。
- 差异生成改动。
- 证据定位改动。

真实合同 PDF 通常敏感，不建议直接提交。可以在本地使用，或只提交脱敏后的样例。

## 9. 阈值配置

默认回归阈值在 `run_quality_regression.py` 中定义。也可以用 JSON 文件覆盖。

示例：

```json
{
  "min_precision": 0.9,
  "min_recall": 0.95,
  "min_evidence_hit_rate": 0.9,
  "max_false_positive_count": 0,
  "max_false_negative_count": 0,
  "max_precision_drop": 0.02,
  "max_recall_drop": 0.02,
  "max_evidence_hit_rate_drop": 0.02,
  "max_false_positive_increase": 1,
  "max_false_negative_increase": 0,
  "max_known_false_positive_regression_count": 0,
  "max_known_false_positive_regression_increase": 0,
  "max_task_failure_increase": 0
}
```

运行：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --thresholds .ocr-compare-quality/quality_thresholds.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --fail-on-regression
```

## 10. 什么时候更新 baseline

只有在以下情况才建议更新 baseline：

- 当前代码改动是预期行为变化，并且人工确认质量没有下降。
- 新增了更完整的 approved gold case。
- 修复误报或漏检后，当前结果明显优于旧 baseline。
- baseline 来自旧评估逻辑，已经不能代表当前质量标准。

不要在回归失败时直接更新 baseline。先判断失败原因：

- 如果是代码退化，修代码。
- 如果是 gold 漏标，补 `expected.json`。
- 如果是阈值过严，调整阈值并说明原因。
- 如果是预期行为变化，人工确认后再更新 baseline。

## 11. 推荐日常工作流

### 新增一个 golden set

```bash
cd backend
python scripts/export_ocr_compare_gold_case.py \
  ../storage/tasks/<task_id> \
  tests/fixtures/ocr_compare_cases/<case_id>
```

然后人工审核：

```text
tests/fixtures/ocr_compare_cases/<case_id>/expected.json
```

把真实差异改为 `APPROVED`，误报改为 `REJECTED`，不确定的保留 `DRAFT`。

### 提交算法改动前

```bash
cd backend
python -m compileall app tests
python -m pytest
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --fail-on-regression
```

### 发现回归失败

打开：

```text
backend/.ocr-compare-quality/runs/local-check/html/index.html
```

按顺序看：

```text
Failed gates
Case regression ranking
Missed expected diffs
Unexpected actual diffs
Evidence drift
```

然后决定：

- 修复算法。
- 补充或修正 `expected.json`。
- 调整阈值。
- 人工确认后更新 baseline。

## 12. 数据安全规则

真实合同数据默认敏感。以下内容不要提交到仓库，除非已经脱敏并明确批准：

- 原始合同 PDF。
- 对比合同 PDF。
- 从真实任务导出的 `actual.json`。
- 包含真实合同片段的 `expected.json`。
- 包含真实合同内容的 HTML/JSON 质量报告。
- `.ocr-compare-quality/runs/*` 下的本地运行报告。

适合提交的内容：

- 脱敏后的 fixture。
- 脱敏后的 `expected.json`。
- 不含敏感内容的 baseline。
- 文档和脚本。

## 可视化工作台接口

后端质量工作台接口以 `/api/quality` 为前缀。第一期接口用于支持后续前端可视化页面：

- `GET /api/quality/cases`：列出 golden cases。
- `POST /api/quality/cases/export`：从已完成任务导出 draft golden case。
- `GET /api/quality/cases/{case_id}`：查看单个 case 的 expected/actual 摘要。
- `PATCH /api/quality/cases/{case_id}/expected-diffs/{index}`：更新一条 expected diff 标注。
- `POST /api/quality/cases/{case_id}/expected-diffs`：新增一条 expected diff。
- `DELETE /api/quality/cases/{case_id}/expected-diffs/{index}`：删除一条 expected diff。

这些接口仍然复用现有 golden set 文件格式；命令行脚本和可视化接口可以并行使用。

## 相关文档

- `docs/ocr_compare_gold_case_workflow.md`
- `docs/quality_regression_workflow.md`
- `backend/scripts/export_ocr_compare_gold_case.py`
- `backend/scripts/evaluate_ocr_compare_quality.py`
- `backend/scripts/run_quality_regression.py`
