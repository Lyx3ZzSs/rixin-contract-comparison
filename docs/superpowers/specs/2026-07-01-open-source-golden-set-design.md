# 合同 Golden Set 成熟开源方案调研与落地建议

## 背景

当前合同差异比对系统已经形成轻量 golden set 与质量回归闭环：

- 从真实比对任务导出 `actual.json`。
- 由人工审核生成或修订 `expected.json`。
- 通过 `review_status` 区分 `DRAFT`、`APPROVED`、`REJECTED`。
- 使用质量评估脚本和回归脚本验证误报、漏报、证据漂移和 baseline 退化。
- 正在建设质量回归工作台，降低人工标注与回归验证门槛。

本调研关注的问题是：市面成熟开源方案是否可以直接用于“合同差异比对 golden set”，以及本项目应该如何吸收这些成熟实践。

## 核心结论

目前没有成熟开源方案可以直接覆盖以下完整产品形态：

```text
合同双文档差异比对
+ OCR 文本/版面证据定位
+ 跨段/跨页差异连续性
+ 误报/漏报人工审核
+ golden set 版本化
+ 自动质量回归门禁
+ 模型路由调优
```

成熟方案主要分散在三类：

1. 合同/法律 NLP benchmark 数据集。
2. 通用人工标注平台。
3. LLM/RAG 评测框架。

因此，本项目不应该直接替换为某个开源平台。更合适的路线是：

```text
保留自研 contract-diff gold schema
+ 借鉴合同数据集的标签设计
+ 借鉴标注平台的人工审核流程
+ 借鉴评测框架的自动化回归门禁
```

## 成熟方案分类

### 1. 合同/法律 Benchmark 数据集

#### CUAD

CUAD 是 Contract Understanding Atticus Dataset，包含商业合同和人工标注的条款类型，适合借鉴合同条款抽取和审查字段设计。

可借鉴点：

- 合同条款类型体系，例如终止、赔偿、义务、付款、日期、主体等。
- 人工标注 gold 数据集结构。
- 合同审查任务中对关键法律字段的关注方式。

不适合作为直接方案的原因：

- CUAD 关注单份合同中的条款抽取，不是双文档差异比对。
- 不覆盖 OCR 噪声、跨页证据、版面定位、误报抑制。
- 不包含本项目需要的 `actual diff -> expected diff -> regression gate` 闭环。

来源：https://www.atticusprojectai.org/cuad

#### ContractNLI

ContractNLI 是合同自然语言推理数据集，任务是判断合同是否支持、反驳或无法判断某个假设，并包含证据 span。

可借鉴点：

- 将法律判断与证据片段绑定。
- 对“合同是否表达某个义务/限制/权利”的语义判断方式。
- 后续做法律风险理解增强时，可以借鉴其 premise、hypothesis、label、evidence 结构。

不适合作为直接方案的原因：

- 它解决的是语义推理，不是逐项 diff。
- 不处理 OCR 对齐、表格错位、跨页连续差异。
- 不提供合同差异误报/漏报回归标准。

来源：https://stanfordnlp.github.io/contract-nli/

#### LexGLUE

LexGLUE 是法律语言理解 benchmark 集合，覆盖法律文本分类、案例匹配、实体判断等任务。

可借鉴点：

- 法律 NLP 任务的标准化评测思路。
- 多任务、多数据集的统一 benchmark 组织方式。
- 后续评估模型法律文本理解能力时可作为参考。

不适合作为直接方案的原因：

- 不是合同差异比对数据集。
- 不包含双文档 evidence alignment。
- 不提供人工 diff gold 标注流程。

来源：https://github.com/coastalcph/lex-glue

#### LegalBench

LegalBench 是法律推理 benchmark，适合评估 LLM 在法律判断、规则适用、条款理解上的能力。

可借鉴点：

- 法律任务 prompt、answer、metric 的组织方式。
- 适合后续评估合同风险摘要、法律义务判断和模型路由。

不适合作为直接方案的原因：

- 不是 OCR 或合同 diff 任务。
- 不解决精确比对、证据框定位和误报治理。

来源：https://github.com/HazyResearch/legalbench

### 2. 通用人工标注平台

#### Label Studio

Label Studio 是成熟开源数据标注平台，支持文本、图像、音频、HTML、PDF/OCR 类任务配置。

可借鉴点：

- 标注任务导入、分发、审核、导出。
- 标注模板配置。
- 标注状态、review 工作流。
- 多人标注与一致性治理。
- 对文本 span、分类、区域框等证据类标注的支持方式。

适合本项目的用法：

- 中期可以作为人工标注前端，把比对任务导出成 Label Studio task。
- 标注员在可视化界面审核真实差异、误报和漏报。
- 再把审核结果导回本项目 `expected.json`。
- 本项目继续负责合同 diff 专用 metric 和 regression gate。

不建议直接替代本项目的原因：

- Label Studio 是通用标注平台，不理解合同 diff 的 domain schema。
- 本项目仍需维护 `expected_diffs`、证据匹配、precision/recall、baseline 退化检测。
- 如果过早接入，会增加部署、权限、导入导出和数据治理复杂度。

来源：https://labelstud.io/

#### doccano

doccano 是轻量文本标注工具，主要适合文本分类、序列标注、NER、文本生成类任务。

可借鉴点：

- 轻量文本标注体验。
- 简单的标签管理和导出格式。
- 适合纯文本合同条款分类或实体标注。

不适合作为主方案的原因：

- 对 PDF/OCR 版面证据支持弱。
- 不适合跨页、跨段、双文档 diff 审核。
- 不提供本项目需要的证据框、任务级回归和 baseline 机制。

来源：https://github.com/doccano/doccano

#### Argilla

Argilla 更偏向数据治理、人工反馈、LLM 输出审核和数据集管理。

可借鉴点：

- 将模型输出转化为人工反馈数据。
- 支持 reviewer feedback、数据集版本化、LLM 评估工作流。
- 适合后期做模型路由、LLM 解释、法律风险摘要的反馈闭环。

不适合作为当前主方案的原因：

- 不直接解决合同 PDF diff 标注。
- 不提供 OCR evidence alignment。
- 对当前“提升比对精确度”的直接收益弱于完善自研 golden set。

来源：https://github.com/argilla-io/argilla

### 3. LLM/RAG 评测框架

#### Ragas

Ragas 是 RAG 评测框架，提供评测数据集、指标和自动化评估流程。

可借鉴点：

- dataset + metric + evaluator 的结构。
- 自动生成和运行评测集的思路。
- 后续评估证据召回、解释一致性、问答式合同审查时可以参考。

不适合作为直接方案的原因：

- Ragas 的核心任务是 RAG 评估，不是合同 diff。
- 本项目的 precision、recall、unexpected diff、evidence overlap 需要自定义。

来源：https://docs.ragas.io/

#### DeepEval

DeepEval 是 LLM evaluation framework，适合把模型输出质量转化为自动化测试和 CI 门禁。

可借鉴点：

- 把 gold case 组织成可重复执行的测试。
- 建立 metric、threshold、regression gate。
- 后续可用于模型路由、法律风险摘要、LLM 解释质量评估。

不适合作为直接方案的原因：

- 它不提供合同 diff 标注 schema。
- 它不处理 OCR 证据定位和双文档差异对齐。
- 只能作为评测框架思想或外围工具引入。

来源：https://github.com/confident-ai/deepeval

## 对本项目的启发

### 当前方向是正确的

本项目当前的 `actual.json + expected.json + README.md` 方案虽然轻量，但更贴近合同差异比对的真实需求：

- `actual.json` 保存系统输出快照，方便复现和排查。
- `expected.json` 保存人工审核后的 gold。
- `README.md` 保存 case 背景、来源、敏感数据提醒和人工判断说明。
- 回归脚本将 gold case 转化为 precision、recall、误报、漏报和证据漂移指标。

这比直接使用通用 NLP 数据集更适合当前目标。

### 当前需要补齐的成熟能力

成熟开源方案暴露出本项目下一步应补齐的能力：

1. 标注来源与审计信息

   `expected.json` 应逐步支持：

   ```json
   {
     "reviewer": "human",
     "reviewed_at": "2026-07-01T10:00:00+08:00",
     "schema_version": "1.1"
   }
   ```

2. 数据集分层

   每个 case 应标注用途：

   ```text
   dev         日常调试
   regression 代码改动后必须通过
   holdout    不频繁查看，用于防止过拟合
   adversarial 专门覆盖难例、误报高发样本
   ```

3. 误报也要成为 gold

   当前 `REJECTED` 已能表达误报，但后续应进一步显式记录：

   ```json
   {
     "review_status": "REJECTED",
     "false_positive_reason": "header_footer",
     "should_not_match_again": true
   }
   ```

4. 漏报要可手动补入

   人工发现真实差异但系统未输出时，应能新增 `APPROVED` expected diff，并在回归中计入 false negative。

5. 证据级 gold

   每个关键差异应逐步支持：

   ```json
   {
     "expected_evidence": [
       {
         "side": "compare",
         "page": 3,
         "text_contains": "2026年4月21日",
         "bbox": [100, 120, 300, 150]
       }
     ]
   }
   ```

6. 多人一致性

   在标注量变大后，应支持：

   - 双人标注。
   - 审核人确认。
   - 冲突状态。
   - inter-annotator agreement 统计。

7. baseline 版本化

   每次模型、matcher、OCR 或 diff generator 调整后，应能记录：

   - 代码版本。
   - 模型组合。
   - 阈值配置。
   - gold case 数量。
   - 核心指标。
   - 是否允许更新 baseline。

## 推荐落地方案

### 方案 A：继续自研轻量 Golden Set，吸收成熟实践

这是当前最推荐方案。

做法：

- 保留现有 `actual.json`、`expected.json`、`README.md`。
- 增强 `expected.json` schema。
- 增强质量工作台。
- 增强回归指标和失败归因。
- 暂不引入大型外部平台。

优点：

- 改造成本低。
- 与当前代码和测试体系贴合。
- 最直接服务“提升比对精确度”。
- 不引入额外部署和权限复杂度。

缺点：

- 标注体验需要自建。
- 多人协同、任务分发、标注一致性需要逐步补齐。

适用阶段：

- 当前阶段到中短期阶段。
- gold case 规模仍在几十到几百个以内。
- 主要目标是快速修误报、漏报和证据漂移。

### 方案 B：引入 Label Studio 作为标注前端

做法：

- 本项目将 task 或 gold draft 导出为 Label Studio task。
- 人工在 Label Studio 中标注真实差异、误报、漏报和证据区域。
- 本项目提供 importer，把结果转换回 `expected.json`。
- 自动评估和回归仍由本项目负责。

优点：

- 标注体验成熟。
- 适合多人协同。
- 可复用任务分发、审核、导入导出能力。

缺点：

- 接入成本较高。
- 需要维护双向转换。
- Label Studio 不理解合同 diff 专用逻辑。

适用阶段：

- gold case 数量增长到几百到上千。
- 有多名人工审核人员。
- PDF 证据框标注成为高频需求。

### 方案 C：接入 Argilla / DeepEval / Ragas 做高级评测治理

做法：

- 保留本项目 diff gold。
- 将 LLM 输出、模型路由、法律风险摘要导入评测平台。
- 对模型解释、风险判断、法律语义一致性做独立评估。

优点：

- 适合长期治理。
- 可支持模型 A/B 测试。
- 可沉淀人工反馈数据。

缺点：

- 对当前 diff precision 直接收益有限。
- 不解决 OCR 和 matcher 的核心问题。
- 容易过早复杂化。

适用阶段：

- 模型路由和法律风险理解进入主优化阶段后。
- 已经有稳定 diff golden set。
- 需要评估不同 LLM 在法律理解任务中的表现。

## 推荐优先级

```text
P0 继续完善自研 golden set schema
P0 强化误报/漏报/证据漂移回归
P1 在质量工作台中支持人工可视化标注
P1 引入 dataset split 和 baseline metadata
P2 评估是否接入 Label Studio
P3 引入 Argilla / DeepEval / Ragas 支持 LLM 评测治理
```

## 建议的本项目 Golden Set Schema 演进

Schema 1.1 的日常标注、负向 golden set 和回归命令操作见 `docs/golden_set_regression_sop.md`。

### Case 级字段

```json
{
  "schema_version": "1.1",
  "case_id": "da0e1282-e239-42dd-92dd-447b8f7ec136",
  "source_task_id": "da0e1282-e239-42dd-92dd-447b8f7ec136",
  "dataset_split": "regression",
  "case_tags": ["ocr_noise", "metadata_date", "cross_page"],
  "created_at": "2026-07-01T10:00:00+08:00",
  "review_status": "PARTIAL",
  "baseline_required": true
}
```

### Diff 级字段

```json
{
  "diff_type": "MODIFY",
  "source_type": "metadata",
  "severity": "critical",
  "title_contains": "签订日期",
  "original_contains": "2026年4月 日",
  "compare_contains": "2026年4月21日",
  "review_status": "APPROVED",
  "reviewer": "human",
  "reviewed_at": "2026-07-01T10:00:00+08:00",
  "notes": "人工确认真实日期差异"
}
```

### 误报字段

```json
{
  "review_status": "REJECTED",
  "false_positive_reason": "header_footer",
  "should_not_match_again": true,
  "notes": "页眉版本号变化，不属于合同正文差异"
}
```

### 证据字段

```json
{
  "expected_evidence": [
    {
      "side": "original",
      "page": 2,
      "text_contains": "2026年4月 日",
      "bbox": [88, 210, 260, 236]
    },
    {
      "side": "compare",
      "page": 2,
      "text_contains": "2026年4月21日",
      "bbox": [88, 210, 280, 236]
    }
  ]
}
```

## 对后续阶段的建议

### 短期

继续加强当前方案，而不是引入外部平台：

- 完善 `expected.json` schema。
- 在工作台中支持 `APPROVED`、`DRAFT`、`REJECTED` 可视化编辑。
- 增加误报原因分类。
- 增加漏报手工补录。
- 增加 evidence-level 检查。

### 中期

当人工审核量明显增加后，再评估 Label Studio：

- 先做单向导出实验。
- 再做标注结果导入。
- 不让 Label Studio 成为唯一数据源，本项目的 `expected.json` 仍作为回归测试真源。

### 长期

在模型路由和法律风险理解进入主阶段后，引入 LLM 评测治理：

- 用 DeepEval/Ragas 思路组织模型输出评测。
- 用 Argilla 思路沉淀人工反馈。
- 将法律语义判断、风险解释、摘要一致性纳入独立 benchmark。

## 最终建议

本项目应采用：

```text
自研 contract-diff golden set 为核心
+ Label Studio 工作流思想
+ ContractNLI 证据绑定思想
+ CUAD 条款类型思想
+ DeepEval/Ragas 回归门禁思想
```

近期不建议直接接入大型外部标注平台。当前最能提升比对精确度的工作仍然是：

1. 建立高质量真实 golden cases。
2. 把误报和漏报都结构化沉淀。
3. 每次 matcher、OCR、diff generator 改动后自动跑回归。
4. 对失败 case 做质量归因，再反向修算法。
