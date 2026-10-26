# Extraction Challenger Minimal Proof

执行日期：2026-09-23；按要求保留文件名日期 20260921。

**结论：Native Text + Oracle Layout 在 B 的 1、13、14 页通过 C1–C5；本地独立 LayoutDetection 尚未达到完全相同的结构能力。部署 Recognition 工作量没有闭环，明确停止在“标准源码路径结论”。目前不进入完整 Challenger 或完整 A–E benchmark。**

本轮只新增独立实验 `experiments/extraction_minimal_proof/` 和本报告。未修改生产 ExtractionStage、ClauseSplitter 或三位编号规则；未恢复 Page Alignment、Matching、Cache、Payload 等实验；未接入 MinerU。实际推理范围为 B 的三页 Layout-only，以及另外三页路由样本。没有运行整份合同 Extraction。

证据目录：[extraction-minimal-proof-20260921](../../output/analysis/extraction-minimal-proof-20260921/)。所有 `unknown/null` 表示没有证据，不代表模型工作量为零。

## 1. 部署环境一对合同实际产生多少 General Text Recognition 工作量？

**实际工作量仍未知，不能写成“部署实测四轮”。** 本轮重新 GET 两个服务的 `/health`、`/openapi.json`，均 200。业务端点仍只有 `/ocr` 和 `/layout-parsing`，`FastAPI 0.1.0` 仅为 API metadata。仓库及现有配置没有给出部署 checkout、服务端 patch、SSH host/user 映射或内部模型 trace；本机没有 SSH config。未尝试猜测账户或把本地模型身份冒充服务端身份。

| 优先取得项 | 部署证据结果 |
|---|---|
| 实际 pipeline YAML / config hash | unknown |
| PaddleX / PaddleOCR version | unknown / unknown |
| 实际 text detection / text recognition model | 精确子型号 unknown；既有用户确认的 PP-OCRv6 family 不足以确定权重 |
| layout / table structure model | unknown / unknown |
| seal / formula model | unknown / unknown |
| model / weight hash | unknown |
| 服务端 patch / commit | unknown |
| 模型入口调用、batch forward、page/crop input | unknown |

[部署身份探测结果](../../output/analysis/extraction-minimal-proof-20260921/deployment-identity.json)保留了这次四个只读 HTTP 请求。没有可用模型边界计数条件，因此未向两个业务端点提交新的识别请求。现存样本目录未找到独立的一页 PDF 文件；为账本登记了已有 PDF 的单页选择器：B-original p1（纯文本）与 RETURN_CONFIRMATION-original p1（表格）。**用户要求的“两份一页 PDF 的部署模型 trace”没有完成，不以本地实验替代。**

[部署 Recognition Work Ledger](../../output/analysis/extraction-minimal-proof-20260921/deployment-recognition-work-ledger.json)包含这两个选择器、两个 endpoint、七类 module 的待采集记录；`request_id=null`，`trigger_reason=NOT_EXECUTED_NO_DEPLOYMENT_TRACE`。这是未完成的采集账本，不是模拟 trace。字段如下：

```text
endpoint / request_id / page_id
module: text_detection | text_recognition | layout_detection | table_structure
        | seal_detection | seal_recognition | formula
input_scope: page | region | line | cell
batch_size / forward_count / input_count / trigger_reason / model_identity
另列 model_invocation_count / http_request_count / ocr_output_item_count
```

计量单位严格分开：

| 单位 | 定义 | 不能用什么反推 |
|---|---|---|
| HTTP request count | 客户端发出的请求数；重试另计 | 不能当作模型执行次数 |
| model invocation | 调用一次模型 predict 入口，可喂入多张图 | 不能当作一个 batch forward |
| batch forward | 实际推理 backend 执行一次批次 | 不能用输出框数替代 |
| page/crop input count | 实际进入模型的 page/region/line/cell 输入数 | 不能用最终 OCR item 数替代 |
| OCR output item count | 解码和过滤后保留的结果数 | 不能证明没有低分丢弃或 fallback |

**仅标准源码路径的回答**：沿用已固定 PaddleX release/3.7 commit `ffb64904d23708863ff5b8da312a5cbd52a7f462` 的本地源码快照，普通非空页如下。这里的“一轮”是处理该页检测行集合的常规 Recognition 路径，不是一次 forward，也不保证检测到所有可见文字。

```text
一页普通文本 PDF：

/ocr
  detection   标准路径 1 个 page pass；部署 forward/input 数 ?
  recognition 标准路径 1 轮检测行集合；部署 invocation/forward/crop 数 ?

/layout-parsing
  layout      标准路径 1 个 page pass；部署 forward/input 数 ?
  detection   GeneralOCR 标准路径 1 个 page pass；部署计数 ?
  recognition GeneralOCR 标准路径 1 轮检测行集合 + 条件性局部识别；部署计数 ?
  table       无 table 区域时标准路径不进入表格识别；部署计数 ?

一份文档：标准路径 2 个文档级 General Text Recognition 轮次。
一对合同：标准路径 4 个文档级 General Text Recognition 轮次。
部署实际：上述轮数及模型工作量均未取得服务端 trace 验证。
```

若两份合同共 P 页，标准主路径对应 `2P` 个 page-recognition passes；这不是 `2P` 次 forward。额外局部输入不包含在该数中。源码位置和既有响应证据见[上一轮调用链报告](extraction-architecture-analysis-20260921.md)。

## 2. `/layout-parsing` 是否重复执行 General OCR？

**标准源码是；部署是否按这一路径运行，仍未闭环。** 固定源码的 `_LayoutParsingPipelineV2.predict` 创建/调用 GeneralOCR，GeneralOCR 内分别执行 TextDetection 和 TextRecognition。已有远端响应含 `overall_ocr_res`，但该字段本身不能证明运行次数或排除服务端复用、patch。

额外 Recognition 的标准触发条件如下；本轮未测部署命中频次：

| 触发条件 | 输入范围 / 工作类别 |
|---|---|
| OCR 框跨 layout 区域，`standardized_data` 按交集切分 | region/span text recognition |
| layout 文本区没有匹配文字 | region fallback recognition |
| 公式与文字混排行分割 | text span recognition；公式模型另记 |
| table OCR 框跨 cell，按 cell 拆框 | table line/cell recognition |
| table 方向为 90/180/270 后转正 | table-region detection + recognition |
| `gen_ocr_with_table_cells` 备用路径 | table-cell detection + recognition；非每张表必走 |
| seal 区域且启用印章识别 | seal detection + seal recognition，独立于普通正文 |
| 应用层显式补识别 / 重试 | 按实际 crop 和原因独立登记 |

Table 主入口可复用 `overall_ocr_res`；`table_ocr_pred` 存在不证明再次执行了 OCR。表结构 token、cell 几何和字符识别必须分账。固定源码快照：[OCR pipeline](../../output/analysis/extraction-architecture-20260921/sources/PaddleX-3.7/paddlex/inference/pipelines/ocr/pipeline.py)、[layout pipeline](../../output/analysis/extraction-architecture-20260921/sources/PaddleX-3.7/paddlex/inference/pipelines/layout_parsing/pipeline_v2.py)、[table pipeline](../../output/analysis/extraction-architecture-20260921/sources/PaddleX-3.7/paddlex/inference/pipelines/table_recognition/pipeline_v2.py)。

## 3. Native Text + Oracle Layout 能否恢复现有 title / paragraph / order？

**能，限本次 B-original p1、p13、p14；C1–C5 均通过。** 输入 PDF SHA256 为 `636e8000c2a7dcd71847de480b245873ab39eb3d6d74acb1b4bf9e326a66a599`。没有把三页结果推广为全量合同结论。

[NativeLayoutMerger](../../experiments/extraction_minimal_proof/native_layout_merger.py)接收 `rawdict` 和严格字段白名单。Structure 仅取 `block_bbox / block_label / block_order` 并转换成 `bbox / type / order`；使用已有 144 DPI 渲染契约将坐标除以 2。任何携带 `text / rec_text / block_content` 等多余字段的 region 都被拒绝。合并器不接受 baseline 文字；baseline 只进入事后评估器。canonical text 和 char boxes 全部来自 native PDF。

```text
rawdict char（page + native char index + block/line/span/char location + bbox）
→ native line
→ char center/coverage 与 layout bbox 做几何归属（不使用文本相似度）
→ 按 native line 内的连续 layout 归属切分；拆开粗 native block
→ 附加 type/layout_order；按显式 order 排序，缺失 order 时按几何放置
→ Existing TextBlock/Page adapter
→ C1–C4 全通过后运行 Existing ClauseSplitter
```

C1：空白规范为删除 `str.isspace()` 字符，但本次连 rawdict 的原始空白字符也全部保留。输入无额外合成换行；每个输出 codepoint 均有 native char index。检查 index 多重集精确等于输入全集，并逐字符反向重建，同时比较去空白后的有序序列。

| 页 | native chars → canonical chars | 去空白 chars | 新增 / 遗漏 / 重复 | provenance |
|---|---|---|---|---|
| 1 | 1713 → 1713 | 1476 | 0 / 0 / 0 | 全覆盖 |
| 13 | 1767 → 1767 | 1530 | 0 / 0 / 0 | 全覆盖 |
| 14 | 1779 → 1779 | 1542 | 0 / 0 / 0 | 全覆盖 |
| 合计 | 5259 → 5259 | 4548 | 0 / 0 / 0 | 全覆盖 |

C2：三页各 10 个 native block、34 行，交付各 34 个 TextBlock。每页均拆开 8 个原先混合标题/正文的 native block；按实际 layout region 切分新增片段分别为 24、8、8。每页 unmatched native lines、部分未归属行、multi-layout assigned lines、没有 native content 的 layout blocks 均为 0。逐字候选框、归属、行框和 split 均已保存，未用仅比较块数替代几何验收。

C3：逐行精确文字与类型对齐，102/102 相同：

| 页 | paragraph_title | doc_title | text | number | table |
|---|---|---|---|---|---|
| 1 baseline / challenger | 9 / 9 | 0 / 0 | 24 / 24 | 1 / 1 | 0 / 0 |
| 13 baseline / challenger | 8 / 8 | 1 / 1 | 24 / 24 | 1 / 1 | 0 / 0 |
| 14 baseline / challenger | 9 / 9 | 0 / 0 | 24 / 24 | 1 / 1 | 0 / 0 |

实际标题见证：B p1 的 `1. Service obligation 1` 从 native 获取全部文字，由 layout 恢复 `paragraph_title`；B p13 的 `Contract performance fixture - Chapter 13` 恢复 `doc_title`；B p14 的 Chapter 14 恢复 `paragraph_title`。**所选三页没有 `3.2 ...`，不伪造这一见证。** B 中也没有 table，因此 B 的 table 验收为“不适用”，不能算表格通过；额外表格页的局部证据见第 5 问。

C4：逐块保存 `baseline reading_order / challenger reading_order / baseline layout_order / challenger layout_order`，102/102 完全相同。负向验证将输入 region JSON 数组倒序后，输出不变，证明依赖显式 order。Layout-only 模型可能有不同 region 粒度，评估器另列 layout_order 原始数值差异；阅读顺序通过条件采用显式 reading_order，不把 region 编号平移误判为内容错序。

C5：只有 C1–C4 全通过后执行。分别对 `[p1]` 和连续 `[p13,p14]` 运行当前 ClauseSplitter，避免虚构 p1→p13 的相邻关系。逐项比较 clause_no、heading、第一/最后 source block 边界、heading membership、body membership、规范化全文和 page_numbers，全部相同。结果为 9/9 和 4/4，仅作为附加观察；验收依据是上述完整签名。

三位编号过滤仍存在：100+ 不成为编号边界，99 的正文会吞入后续内容；Chapter 14 的语义标题恢复了与 baseline 相同的后续边界。本实验只证明兼容，未证明该规则正确。未修改三位编号过滤，也未用 114/113 作为验收依据。

每一步的 diff 与完整 provenance：[p1 C1–C4](../../output/analysis/extraction-minimal-proof-20260921/oracle/p1-C1-C4.json)、[p13 C1–C4](../../output/analysis/extraction-minimal-proof-20260921/oracle/p13-C1-C4.json)、[p14 C1–C4](../../output/analysis/extraction-minimal-proof-20260921/oracle/p14-C1-C4.json)、[C5 boundary/heading/body diff](../../output/analysis/extraction-minimal-proof-20260921/oracle/C5-clause-diff.json)。同目录有 `p*-adapter.json` 和 `p*-blocks.diff`；Oracle 后者为空差异。

## 4. 独立 Layout-only 模型能否达到相同结构能力？

**本次所测 PP-DocLayout_plus-L 没有达到完全相同能力；不是 Layout-only 思路被否定，也未代表其它型号。** Oracle 通过后才启动该模型；未创建 GeneralOCR 或 PP-Structure pipeline。运行时 guard 在初始化和预测阶段阻止这些 pipeline 构造以及 TextDetection/TextRecognition 模型进入。通过 `PaddleInferChainLegacy.__call__` 边界记录实际 batch forward。

本地环境为 PaddleOCR 3.2.0、PaddleX 3.2.0、PaddlePaddle 3.2.2，CPU、4 threads、关闭 MKLDNN，threshold=0.5，输入 144 DPI。模型没有 reading-order head；本实验只对单栏样本按几何 `(y0,x0)` 建立显式 order，不能推广到复杂多栏。

权重 `inference.pdiparams` SHA256：`24ca3e2e442164505e250deef59f7ee9a54ea12dd32875c9cd6155d959dc97da`。初始化约 1.917 s，单次局部诊断时间如下，不是性能 benchmark：

| 页 | latency s | raw layout blocks / 类型分布 | model invocation / forward / page input | Text Recognition |
|---|---|---|---|---|
| 1 | 1.309 | 35：paragraph_title 9、doc_title 1、text 24、number 1 | 1 / 1 / 1 | 0 |
| 13 | 0.819 | 19：paragraph_title 9、doc_title 1、text 8、number 1 | 1 / 1 / 1 | 0 |
| 14 | 0.865 | 18：paragraph_title 9、text 8、number 1 | 1 / 1 / 1 | 0 |

C1 三页通过，仍保留 5259 个 native 字符。C2 在 p1/p13 各发现一个没有分配 native content 的重复 layout：同一 bbox 同时预测 `paragraph_title` 和 `doc_title`。几何归属保留一份文字，没有复制字符来填满重复框。

C3 在 p13 失败：baseline Chapter 13 为 `doc_title`，模型的同框两个预测分别为 paragraph_title=0.552574、doc_title=0.527832，当前几何归属确定为前者。p1/p14 类型通过。按归属后的 native 行块，title 大类 precision=27/27，但精确 title subtype precision=26/27（96.30%）。这是事后块级指标，不是 raw proposal precision；29 个 raw title proposals 含两组重复框。p13 不能因为仍是“某种 title”就算完全恢复。

C4 三页 reading_order 102/102 相同；原始 layout_order 的 67 项数值差异单独保留，来自重复框编号平移和页码无 model order。C2/C3 总门槛失败，**该分支 C5 未运行**。未按 baseline 答案硬编码消歧规则，也未扩大模型测试。

证据：[identity](../../output/analysis/extraction-minimal-proof-20260921/layout-only/model-identity.json)、[forward ledger](../../output/analysis/extraction-minimal-proof-20260921/layout-only/inference-ledger.json)、[逐级 summary](../../output/analysis/extraction-minimal-proof-20260921/layout-only/summary.json)、[title precision](../../output/analysis/extraction-minimal-proof-20260921/layout-only/title-precision.json)。同目录 `p*-C1-C4.json` 与 `p*-blocks.diff` 保留逐块差异。

## 5. Mixed page 能否做到 native + region OCR，而不是 full OCR？

**已证明一个本地实例可以；真实“native + 扫描正文区域”的完整验收仍未完成。** 只追加三页，没有创建新合成 PDF，也没有向部署服务发识别请求。最小分类器按“无 native → SCANNED；native 可打印且大图区域无 native glyph → MIXED；其余当前样本 → NATIVE_GOOD”路由，未按样本名称直接决定路径。这不是生产质量 gate，乱码、叠加隐藏文本和复杂重叠等泛化问题未验证。

| 单页样本 | canonical text | 模型工作量实测 |
|---|---|---|
| RETURN_CONFIRMATION original p1，原生 4×2 表格 | 全页 117 个 native chars，包含表内 50 chars；均可追溯 | layout 1 forward；table structure 1 forward；普通正文 recognition=0；table OCR=0 |
| HUANENG_DINGBIAN compare p1，纯扫描封面 | OCR；native chars=0 | page detection 1 invocation/1 forward/1 page；recognition 1 invocation/3 forwards（6/6/1）/13 line crops/13 outputs |
| SHENNENG_NAIMAN original p12，原生正文 + 栅格结构图 | 469 个 native chars + 图区 16 条 OCR；native 无增删重 | region detection 1 invocation/1 forward/1 crop；recognition 1 invocation/3 forwards（6/6/4）/16 line crops/16 outputs；整页 OCR=0 |

所有行识别 crop id 唯一，forward 实际 batch sizes 之和等于输入数。扫描页的“coverage=1 次”严格指**此次 detector 输出的 13 条行各送入 Recognition 一次**，不是所有可见正文、印章、手写内容均被识别的证明。扫描样本是封面；图像可见手写和印章，本实验没有做专门印章识别或人工全文准确率验收。

Mixed 识别区域为 PDF 坐标 `[96.0,141.95999,495.0,457.67999]`，区域内 native glyph=0。只渲染该 region 供 detection/recognition；全页图像仅供 layout。native 文本按行保留，OCR 行带 crop id 和回投 page polygon，几何顺序合并。逐字验证 469 个 native index 全部且仅出现一次。图中文字如“数值天气预报”“反向隔离”“交换机”等来自 OCR；外部正文仍来自 native。

**样本缺口：这一混合区是栅格图中文字，不是扫描合同正文。** 现有 original PDF 的搜索只找到这一类合适的大图 mixed 页，没有为凑通过而合成 scanned-text 混合样本。因此 region-only 技术路径有实证，但用户要求的真实 scanned-region Mixed 验收保留为未完成。复杂图内阅读顺序、OCR 准确率和漏检率也未验收。

Table 单独分账：Standalone PP-DocLayout_plus-L 恢复 `table` bbox，对既有 baseline table bbox 的覆盖率 99.82%，50 个 native chars 可按几何赋予 table 语义。Standalone SLANeXt_wired 输出 4 行/8 cell skeleton，与 PyMuPDF 原生线框检测的 4×2 一致；无需 table OCR。**尚未验证 cell 文字归属、cell bbox 坐标映射、table reading order 或完整 HTML 等价**。B 无表格，本页证据只补 table 类型和独立结构任务，不补签一份完整 C1–C5 表格通过证书。

E 使用本机已有 `PP-OCRv5_server_det / PP-OCRv5_server_rec` 独立模型，**与用户确认的部署 PP-OCRv6 不同**；没有借此判断部署质量。权重 SHA256（完整配置/文件 hash 见账本）：

```text
text_detection 183146fe9d9910352f68482f623bcbbb9fa7b9e8fa1463b9ad288cef00524d2d
text_recognition 63853f062a5f4089befc16f565a68277618e0da5cb45468b49d11079de0ada77
table_structure ff0362a30f707fa7d27d8453c4396b992fca287377a42d01515bca6eeb3908f5
```

[本地 Recognition Work Ledger](../../output/analysis/extraction-minimal-proof-20260921/routes/recognition-work-ledger.json)完整列出七类 module，实际不调用的模块有显式零记录；包含 scope、model identity、input ids、forward batch shapes、触发原因及 `recognition_category`。Mixed 图中文字标为 `raster_figure_text`，未混算为 table OCR。这里 HTTP request count 全为 0，`request_id=null` 合理，因为是本地直接模型执行。

[路由 summary](../../output/analysis/extraction-minimal-proof-20260921/routes/summary.json)、[Mixed canonical/provenance](../../output/analysis/extraction-minimal-proof-20260921/routes/mixed.json)、[table semantic diff](../../output/analysis/extraction-minimal-proof-20260921/routes/table-semantic-diff.json)保留结果与边界。

## 6. 哪些功能仍强制需要 OCR？

对于缺少可用原生字符、又要求获取文字内容的区域，仍需要识别：纯扫描正文、Mixed 中的栅格文字、图片/扫描表格里的文字、必须读出的栅格印章文字与手写内容，以及 native 编码损坏或可验证缺字的区域。范围应由缺失区域决定，不因此强制整页重复识别。

Layout type/bbox、表结构 skeleton、cell geometry、阅读顺序和有可靠 native 字符的正文/表格文字，本身不强制需要 Text Recognition。公式图像转 LaTeX 等需要独立公式识别，不能算作普通正文 OCR；仅检测印章存在也不等于读取印章文字。现有应用修复分支仍可能消费第二来源文字，是否能替换为按需局部识别尚需专门契约验证。

## 7. 下一阶段是否值得进入完整 Challenger？

**值得继续最小验证，但现在不进入完整 Challenger，更不启动完整 A–E benchmark。** Oracle 已排除“native 文字先天无法满足现有 splitter”的推断，并交付独立、可复现的合并器；尚需补齐：部署端模型边界账本、独立 layout 标题 subtype/重复框策略、真正 scanned-text Mixed 页，以及需要交付表格时的 cell/HTML 契约。三位编号过滤另立问题，继续不在本实验修复。

复现顺序如下，前三页门槛由代码强制检查；脚本中没有完整 benchmark 或生产 ExtractionStage 入口：

```bash
backend/.venv/bin/python experiments/extraction_minimal_proof/probe_identity.py
backend/.venv/bin/python experiments/extraction_minimal_proof/run_oracle.py
experiments/layout_decoupling/.venv/bin/python experiments/extraction_minimal_proof/run_layout_only.py
experiments/layout_decoupling/.venv/bin/python experiments/extraction_minimal_proof/run_routes.py
backend/.venv/bin/python experiments/extraction_minimal_proof/verify.py
backend/.venv/bin/python experiments/extraction_minimal_proof/finalize.py
```

没有新增依赖。Layout 和 route 使用现有独立实验环境及本地已下载权重；脚本不会导入旧 benchmark runner。

[verification.json](../../output/analysis/extraction-minimal-proof-20260921/verification.json)记录 17 项检查，包括字符反向重建、缺失/重复 layout 的负向门槛、禁止文字字段注入、显式顺序不依赖数组顺序、batch/input 对账、native+region provenance 及生产文件 hash。生产 `pipeline_stages.py / clause_splitter.py / clause_heading.py / clause_numbering.py / clause_paragraphs.py` 与既有 baseline hash 一致。[manifest](../../output/analysis/extraction-minimal-proof-20260921/manifest.json)记录本轮范围、代码 hash 和所有未闭环项。
