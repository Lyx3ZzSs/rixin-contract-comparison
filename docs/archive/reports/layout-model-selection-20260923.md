# Layout 模型选型：PP-DocLayoutV3 vs PP-DocLayout_plus-L

> **历史报告，决策已失效。** 用户已指定 PP-DocLayoutV3 为唯一 Layout 模型；停止比较 plus-L、第三模型测试及模型选型。后续以 [V3 Extraction Challenger](extraction-challenger-v3-20260923.md) 为准。保留下文原始实验事实，不再使用其“不得进入 Challenger”结论。

**结论：`CONTRACT_A: Exact subtype required`；本阶段选型为 `INSUFFICIENT_EVIDENCE`，不进入完整 Contract Extraction Challenger。** 唯一基线为 [extraction-challenger-minimal-proof-20260921.md](extraction-challenger-minimal-proof-20260921.md)。已成立的 native/Oracle/Selective OCR 结论直接沿用，没有重跑 Oracle、Recognition 或部署 trace。

首先回答六个问题：

| 问题 | 本轮答案 |
|---|---|
| 1. 生产是否真的要求区分 doc_title / paragraph_title？ | **是。** 无编号章节、封面过滤、重复标题、封面字段/evidence 的实际分支不同；不能只保留 TITLE_FAMILY。 |
| 2. V3 是否减少 duplicate proposal？ | **是，固定 0.5 阈值下，七页共 2 组 → 0 组。** B p1/p13 各消除一组跨标签重复；网络输出进入官方后处理之前也已是 0 组，不仅是自定义 NMS 的结果。 |
| 3. V3 是否解决 p13 subtype？ | **没有。** baseline=doc_title；plus-L 和 V3 均选 paragraph_title。V3 的 paragraph_title=0.522544、doc_title=0.175628，后者未过阈值。 |
| 4. V3 model reading order 是否优于 plus-L 几何排序？ | **当前页面没有证明业务收益。** B 的已匹配行和两个 Mixed 页的正文顺序均可恢复；V3 表格页因漏掉标题区域，在 fallback 后产生顺序问题。一个页眉顺序差异不当作正文失败。 |
| 5. 谁更适合 Native Text + Layout-only？ | 当前数据上 plus-L 的标题恢复和集成负担较好，V3 的重复框更少；**两者均未满足完整业务门槛，因此不宣布合格主候选**，最终标签为 INSUFFICIENT_EVIDENCE。 |
| 6. 是否达到进入完整 Challenger 的门槛？ | **没有。** Exact semantic 和 geometry 均有失败；核心 B 组 C1–C4 未全通过，本轮 C5 按门槛未运行。 |

全部证据：[output/analysis/layout-model-selection-20260923](../../output/analysis/layout-model-selection-20260923/)。本轮只新增独立实验、证据和本报告；生产 ExtractionStage、ClauseSplitter、三位编号规则均未修改。旧性能主线继续暂停，未接入 MinerU，未运行完整 A–E、E2E 或 P50/P95。

## 1. 真实生产 semantic 契约

结论在模型比较前确定。搜索覆盖 `backend/app`、`frontend/src` 中全部 `.py/.ts/.tsx`，**包含被 gitignore 排除的生产文件**，检索 doc_title、paragraph_title、title、block_type、layout_type。按 AST 记录所在函数，区分 layout subtype 与普通 `Clause.title` 文本字段；82 个命中文件的全部命中、源码 hash 与 25 条消费链说明存于 [business-semantic-contract.json](../../output/analysis/layout-model-selection-20260923/business-semantic-contract.json)。

关键真实分支如下；“相同”与“没有直接分支”也明确记录，未根据名称推断：

| Consumer / source function | doc_title branch | paragraph_title branch | 实际影响 |
|---|---|---|---|
| [ClauseHeading.is_unnumbered_section_title](../../backend/app/services/clause_heading.py#L204) | main_contract 通常拒绝；附件/安全协议有特例 | 无编号、长度等条件满足时可成为章节标题 | **clause/chapter boundary、heading/body membership 不同** |
| ClauseHeading.candidate | 属于 document_title，附件目录上下文可能抑制再次切分 | 除文字本身属于封面噪声外，不走该 subtype 分支 | 附件边界不同 |
| [ClauseSplitter._is_pre_body_noise / _looks_like_cover_unit](../../backend/app/services/clause_splitter.py#L936) | cover_block_types 包含 doc_title/title，正文开始前可过滤 | 不在 cover_block_types 中 | 封面过滤、首条边界和正文归属不同 |
| ClauseSplitter._detect_clause_items → ClauseSectionPathBuilder.path | cover 分支、可能无章节候选 | 可创建 level=1 的无编号章节候选 | **间接影响 hierarchy / section_path** |
| ClauseNumberParser / ClauseSectionPathBuilder | 不直接消费 subtype | 相同 | **没有 subtype 驱动的 numbering reset 分支**；号码解析与层级栈基于文字/number/level/section_type |
| ClauseParagraphs._has_strong_boundary_block_type | title family 强边界 | 相同 | 只在这一消费者中等价，不代表全系统等价 |
| [document_role_repair.repeated_title_ids](../../backend/app/services/document_role_repair.py#L36) → DocumentPreparation | 首页合同/协议 doc_title/title 可成为重复标题种子，后页匹配会设置 enter_clause_compare=False | 不进入同一个种子/后页 subtype 集合 | **页眉/重复文档标题过滤与正文成员不同** |
| [cover._extract_cover](../../backend/app/services/cover/extractor.py#L106) | 后续页 preamble_titles 候选，生成 CoverField/evidence、消费 block id | 不进入后续页文档标题集合 | **封面字段、diff/evidence ownership 直接不同** |
| cover.geometry.is_body_start | 不走末尾的宽松编号+文字分支 | 可走该分支 | cover/body 边界不同 |
| cover.party_ocr.reconcile_cover_title_roles | peer 精确 doc_title 可提升匹配 text/ocr_line | peer paragraph_title 不触发 | 对侧封面 role 修复不同；本轮只读，不执行 OCR |
| DocumentUnderstanding / flow_role_for_region | heading flow role | 相同 | 确定性主规则不直接区别二者；可选语义请求携带原始 block_type，不能承诺远端分类等价 |
| DocumentPreparation._section_role | 有一个 doc_title+quote 的后置条件 | 无该后置条件 | **该条件冗余**：同一 quote 文字此前已返回，不能拿它当有效差异证据 |
| PageAlignment._layout_token / _layout_similarity | token 保留 doc_title | token 保留 paragraph_title | exact token set/Jaccard 不同，layout score 有贡献；**只读追踪，没有启动 PageAlignment 实验** |
| 当前 hybrid adapter / 替代 orchestrator | 默认 hybrid 保留 doc_title；替代 orchestrator 转成 legacy title | 均保留 paragraph_title | 没有全局 TITLE_FAMILY 折叠；legacy title 仍属 cover 类型 |
| HeaderFooter / RepeatedOverlay / NativeHeadingRepair | 有共享 meaningful/title 集合；header/footer 主集合不直接区别 subtype | 同左 | 共享保护规则；重复标题过滤的间接影响仍存在 |
| Diff / Evidence / summary / event projection | 没有额外直接 subtype 条件 | 同左 | 消费前序生成的 Clause、CoverField、DiffItem、evidence；前序边界和归属差异会传递，不能保证最终相同 |

同一原生 Chapter 13 文字的**条件函数探针**：doc_title 得到 `unnumbered_section_eligible=False / pre_body_noise=True`；paragraph_title 得到 `True / False`。仅调用这两个生产判断函数，没有提前执行 ClauseSplitter.split。`1通用要求` 在 cover.geometry 的末尾分支也分别为 False/True。

因此：

```text
CONTRACT_A: Exact subtype required
TITLE_FAMILY = {doc_title, paragraph_title} 仅为额外统计口径，不能替代业务 gate。
```

这说明“现有消费者依赖 subtype”，**不说明 baseline 每一个 subtype 都是理想语义真值**。本阶段保持生产消费者不变，按现有契约做兼容选型。

## 2. 固定七页、统一输入与独立环境

| ID | 既有 PDF / 页 | native chars | 作用 |
|---|---|---:|---|
| B-p1 / B-p13 / B-p14 | B-original / 1、13、14 | 1713 / 1767 / 1779 | 已有标题与章节缺口 |
| table-p1 | RETURN_CONFIRMATION original / 1 | 117 | native 4×2 表格及封面标题 |
| scan-p1 | HUANENG_DINGBIAN compare / 1 | 0 | 纯扫描封面、text/doc_title/seal 等区域 |
| mixed-p12 | SHENNENG_NAIMAN original / 12 | 469 | native 正文 + 栅格结构图 |
| mixed-text-p4 | LIANJIANG_PINGPO original / 4 | 531 | **真实 native 正文 + 栅格公式/中文合同说明** |

额外样本搜索只做现有 golden-intake 的 PDF 元数据/native 读取，共 30 份、571 页，没有整合同推理。发现廉江平坡 p3/p4 的嵌入图片含公式和中文合同说明，视觉核查后选 p4，因为 native 正文更多。[搜索记录](../../output/analysis/layout-model-selection-20260923/mixed-sample-search.json)。它是已有真实合同中的栅格合同文字，**是否由扫描仪产生未知**；没有合成页面，也未把它描述为已认证的扫描仪来源。

[dataset.json](../../output/analysis/layout-model-selection-20260923/dataset.json)记录 PDF/hash/page、cropbox、mediabox、rotation、DPI、坐标变换、图像及 rawdict hash。一次冻结 `page.png + native-rawdict.json`，两组读取同一文件。全部页面 rotation=0，144 DPI，坐标除以 2 回投 PDF。baseline 仅供事后评估，不进入模型或 merger。

旧 `experiments/layout_decoupling/.venv` 不支持 V3，因此创建 **`experiments/layout_v3/.venv`**。没有升级 backend/.venv 或修改生产依赖。两模型均在这个新环境运行，以避免运行库成为另一变量：

```text
Python           3.12.8, macOS arm64
PaddlePaddle     3.2.2
PaddleOCR        3.5.0
PaddleX          3.5.0
PyMuPDF          1.28.0
runtime/backend  paddle_static / CPU
threads          4
MKLDNN / TRT     false / false
input DPI        144
threshold policy score > 0.5，所有 label 同一阈值
layout_nms       false（primary run），无自定义 NMS
```

权重 SHA256：

```text
PP-DocLayout_plus-L inference.pdiparams
24ca3e2e442164505e250deef59f7ee9a54ea12dd32875c9cd6155d959dc97da

PP-DocLayoutV3 inference.pdiparams
70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137
```

完整模型 YAML、全部模型文件 hash、版本、flags、官方后处理源码 hash 分别存于 [plus-L identity](../../output/analysis/layout-model-selection-20260923/plus-l/model-identity.json)、[V3 identity](../../output/analysis/layout-model-selection-20260923/v3/model-identity.json)。依赖锁：[requirements.lock](../../experiments/layout_v3/requirements.lock)。官方 [Layout Analysis 文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/layout_analysis.en.md)仅用于确认独立模块入口；实际能力判断来自本轮样本、已安装版本源码及 trace，没有引用官方榜单决定选型。

## 3. Layout-only 工作量与统一 merger

两组均直接创建 `LayoutDetection(model_name=..., model_dir=...)`。guard 在构造前启用，覆盖模型构造、predict/process 和后台 Python 线程；禁止 TextDetection、TextRecognition、GeneralOCR 和 PP-Structure 的边界进入。真实 forward 记录于 Paddle 的 `PaddleRunnerChainLegacy.__call__`，其实现内部执行一次 `predictor.run()`。

| 组 | model invocation | batch forward | page input | TextDetection | TextRecognition | GeneralOCR | PP-Structure | table structure / table OCR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| plus-L | 7 | 7 | 7 | 0 | 0 | 0 | 0 | 0 / 0 |
| V3 | 7 | 7 | 7 | 0 | 0 | 0 | 0 | 0 / 0 |

每页 batch_size=1；没有远端业务 HTTP 请求，也没有 Recognition 重试。账本：[plus-L](../../output/analysis/layout-model-selection-20260923/plus-l/inference-ledger.json)、[V3](../../output/analysis/layout-model-selection-20260923/v3/inference-ledger.json)。本阶段没有运行表格结构模型；上一阶段表结构证明直接沿用，未混入本轮 Layout 指标。

两组调用**同一个、未修改的** `experiments/extraction_minimal_proof/native_layout_merger.py`，同一份 [evaluate.py](../../experiments/layout_v3/evaluate.py)，使用冻结 rawdict。model 只供 bbox/type/order；canonical text 全部取 native chars。V3 的 polygon、pixel polygon、enclosing bbox 和官方 coordinate bbox 全部保存在 raw-proposals；本次公共 merger 仍按 bbox 归属，未单独给 V3 使用 polygon-aware merger。

唯一模型差异包含该模型自带的官方预测/后处理。V3 的官方 `filter_boxes` 与 order 解码实际执行，plus-L 没有这些能力；这不是新增的自定义 NMS。网络候选在官方后处理前也已截获，因而可以拆开分析模型与后处理的效果。

## 4. C1–C4 与 duplicate/subtype 结果

C1 对每页比较 input/output char count、native index 多重集、逐字符反向重建。两个模型均无新增、遗漏、重复，正文不读取模型文字。scan 页 native=0 的 C1 是**空集守恒，不表示 OCR 完成**。PDF storage sequence 是否等于阅读顺序单独保留，未把 rawdict 中提早存储的 footer 当作顺序真值；C4 独立验收顺序。

C2 按 IoU≥0.90 的连通组统计 duplicate，保存 bbox/polygon/label/score、逐 pair IoU 及涉及 native lines。以下是官方 predict 输出，网络前置候选的计数另存：

| 页 | plus-L raw/assigned proposals | V3 raw/assigned proposals | duplicate plus-L / V3 | 未归属 native 行 plus-L / V3 |
|---|---:|---:|---:|---:|
| B p1 | 35 / 34 | 33 / 33 | 1 / 0 | 0 / 1 |
| B p13 | 19 / 18 | 18 / 18 | 1 / 0 | 0 / 0 |
| B p14 | 18 / 18 | 17 / 17 | 0 / 0 | 0 / 1 |
| table p1 | 4 / 4 | 6 / 6 | 0 / 0 | 1 / 2 |
| scan p1 | 8 / 0 | 8 / 0 | 0 / 0 | 0 / 0（无 native） |
| mixed p12 | 13 / 12 | 13 / 12 | 0 / 0 | 0 / 0 |
| mixed-text p4 | 16 / 11 | 13 / 11 | 0 / 0 | 0 / 0 |

七页中 plus-L 为 **2 cross-label duplicate groups、0 same-label groups**，V3 两者都是 0。B p1/p13 是精确同框、IoU=1 的 doc_title/paragraph_title；不是文字重复，merger 仍只消费每个 native 字符一次。所有页面 multi-assigned line=0；两组 B 每页均拆开 8 个 native block。完整 empty regions/split/native line 详情：[duplicate-proposals.json](../../output/analysis/layout-model-selection-20260923/comparison/duplicate-proposals.json)。scan 和栅格图/文字区没有 native content 是预期的 OCR-required 区域，不因为它们为空就判 geometry 失败；原生 B 的标题漏框与重复框则不能这样豁免。

C3 同时保留两种统计口径，B 的分母均为 baseline 的 27 个标题行，**漏检仍计入分母**：

| B 三页指标 | plus-L | V3 |
|---|---:|---:|
| Exact subtype accuracy | 26/27 = 96.30% | 24/27 = 88.89% |
| Title-family accuracy | 27/27 = 100% | 25/27 = 92.59% |
| 全部 102 个 native 行块 exact type | 101/102 | 99/102 |

Chapter 13 的原始候选与选择：

| 模型 | paragraph_title score | doc_title score | 0.5 阈值后 | merger 最终语义 |
|---|---:|---:|---|---|
| plus-L | 0.552574 | 0.527832 | 同框两项均保留 | paragraph_title |
| V3 | 0.522544 | 0.175628 | 仅 paragraph_title | paragraph_title |

p13 baseline 为 doc_title，所以两者均不满足当前生产 exact 契约。V3 另外在 B p1/p14 的 Chapter 标题上分别只给 paragraph_title **0.485783 / 0.453469**，低于共同阈值；native 文字仍保留，但无 layout 归属而保持 text。不是 native 漏字，也不是正确标题被 NMS 删除。本轮未调低 V3 阈值或为其单设策略。

[chapter-proposals.json](../../output/analysis/layout-model-selection-20260923/comparison/chapter-proposals.json)保存上述未过阈值候选与 raw network order；[semantic-diff.json](../../output/analysis/layout-model-selection-20260923/comparison/semantic-diff.json)同时保存 text/number/table/image/seal/formula 等分布和逐块 label diff。scan 无 native 的 subtype 指标记不适用，另比较原始区域 label/coverage，不以空分母报 100%。

## 5. Reading order：保留 V3 原始模型顺序

plus-L 沿用显式 `(y0,x0)` 几何顺序，相同几何以 score 等稳定属性打破平局。V3 不先进行几何排序，保留三层字段：

1. `raw_network_order`：网络输出第 7 列，官方解码前截获；
2. `raw_model_order`：官方输出的 order（某些 header/image/table/caption 标签为 null）；
3. `raw_full_model_sequence`：官方按模型 order 解码后的完整序列位置，包含被官方 order 字段跳过的标签。

进入公共 merger 的 V3 显式 order 来自第 3 层，避免把 order=null 的 table/image/caption 全部错误移到页尾。它不是事后按 y 排序。随机打乱 raw proposal JSON 数组，保留这些显式字段后，两模型七页输出的 final reading order 均不变。

B 三页的可映射 native block pairs 均无顺序反转；V3 的缺标题归属在 C2/C3 失败，不能把“剩余匹配行有序”当全页通过。

复杂布局检查使用表格页和两个 Mixed 页：

- table 页：两模型已匹配 proposal 相对顺序都符合 baseline。V3 漏掉两行封面标题后，公共 merger 的未匹配 fallback 把它们放到 table 后，造成正文/标题顺序失败。**不能将此归因于 order head 把已有标题排序错了，根因先在区域漏检。**
- mixed-p12：正文和图/图题关系没有发现 V3 优势。V3 将两个页眉按左→右排列，baseline/plus-L 为右→左，因此全块顺序有一处反转。两者都是生产过滤的 header，单独报告，不拿它阻止正文 C4。
- mixed-text-p4：两模型均保持公式→栅格中文说明→native 正文顺序；V3 官方输出的公式区更集中，**减少碎片不等于已证明 learned reading order 更优**。
- scan 封面：baseline 是现有 OCR/layout 的片段参考，plus-L/V3 各有 4/5 个片段未映射，已映射区域对中有 3/4 个相对顺序差异。这里不把 raw proposal 数和 OCR 行数混用；也没有人工全文顺序真值，因此不能宣称任一方完整通过扫描页的 reading-order 验收。

[reading-order-diff.json](../../output/analysis/layout-model-selection-20260923/comparison/reading-order-diff.json)逐块列 baseline order、候选 order、V3 raw order、反转 pair、缺失区域和 shuffle 结果。

## 6. 官方后处理、NMS 与 refinement 边界

先保存未经实验改写的网络候选和官方输出，才做离线实验。真实 trace 表明两组 primary `layout_nms` 均未启用；V3 官方 `filter_boxes`、`update_order_index` 执行。源码及调用证据见 [official-postprocess-audit.json](../../output/analysis/layout-model-selection-20260923/comparison/official-postprocess-audit.json) 和 `runtime-sources/`。

B 页 V3 的 thresholded network 与官方最终输出数量相同，且都没有 IoU≥0.90 duplicate；故 B 的重复减少来自模型候选本身。新增 mixed-text 页 V3 则从 **16 个 thresholded network candidates → 13 个官方输出**，涉及官方 overlap/containment 过滤。其“1 formula + 1 text”较少碎片，应归于模型与官方后处理的组合，不能全部归功于视觉 head。

随后在两组相同 raw outputs 上比较 class-aware / class-agnostic NMS，IoU=0.80、0.90、0.95，规则只输入 bbox/label/score/stable id，**没有重新 forward，没有 OCR，没有 baseline label 参与规则输入**：

| 策略 | plus-L | V3 |
|---|---|---|
| class-aware，各 IoU | 跨标签重复仍保留 | 核心 B 失败未改变 |
| class-agnostic，各 IoU | p1/p13 重复移除，但较高分 paragraph_title 获胜，p13 exact 仍失败 | 不能补回漏标题，也不能把 paragraph_title 改成 doc_title |

没有一个 variant 让 B 三页通过全部 C1–C4。[nms-study.json](../../output/analysis/layout-model-selection-20260923/comparison/nms-study.json)保留每页移除的 proposal id、两种 title 指标和门槛。

没有实现 subtype refinement：三页 Chapter 的 native 字体、字号、粗细和垂直位置相同，而当前 baseline 要求 p1/p14=paragraph_title、p13=doc_title。没有查到能据此确定该差异的生产规则。根据页号或 Chapter 数字拟合会使用 baseline 答案，不予实施。[native-subtype-features.json](../../output/analysis/layout-model-selection-20260923/comparison/native-subtype-features.json)记录字体/位置/行数/编号形态，供后续研究，不把特征存在当作 refinement 已证明。

## 7. Table、Mixed 与 Scan 的 Layout 能力

**Table region 两者通过，整页 geometry 不等于通过。** 对同一 retained baseline table bbox：

| 指标 | plus-L | V3 |
|---|---:|---:|
| table proposals | 1 | 1 |
| baseline table bbox coverage | 99.82% | 100% |
| IoU | 99.40% | 98.17% |
| duplicate table groups | 0 | 0 |
| 本轮 table structure / table OCR | 0 / 0 | 0 / 0 |

plus-L 的一个 native “合同编号”行未获 layout；V3 的两行封面标题未获 layout，其他封面标题也未恢复成 baseline doc_title。表内 native 文字仍守恒，表结构/HTML/cell 结果不计入本轮 Layout accuracy。[table-diff.json](../../output/analysis/layout-model-selection-20260923/comparison/table-diff.json)。

Mixed coverage 按每个 proposal 的 native char provenance 标记 NATIVE_COVERED / OCR_REQUIRED：

| 页面 | plus-L | V3 |
|---|---|---|
| mixed-p12 | 12 个含 native 区域 + 1 个无 native image region | 相同；469 native chars 保留 |
| mixed-text-p4 | 11 个含 native 区域 + 5 个需识别区域（formula 4、text 1） | 11 个含 native 区域 + 2 个需识别区域（display_formula 1、text 1）；531 native chars 保留 |
| scan-p1 | 8 个无 native 区域，含 header/text/doc_title/seal/number | 8 个无 native 区域，含 text/doc_title/seal/number |

这足以让既有 region-only 路径获得“native 保留，缺失区域交给文字/公式等相应识别任务”的输入；没有重跑或优化 OCR。image 表示需检查/按需识别，不能凭 image 标签保证内部全是文字。scan 仍有手写/标识参考片段没被两模型完整覆盖，不宣称完整视觉召回。

[mixed-diff.json](../../output/analysis/layout-model-selection-20260923/comparison/mixed-diff.json)保存每个区域的 type、bbox、native char count、routing status 和参考 label/order。**PP-OCRv5 识别准确率未参与模型比较**；上一轮已证实的 Selective OCR orchestration 只作为已有能力引用。

## 8. C5 gate 与最终候选对照

核心组为 `[B p1]` 与连续 `[B p13,p14]`；不虚构 p1→p13 相邻。本轮两组 raw model 都未过各自核心 C1–C4，所以 C5 明确 `NOT_RUN_C1_C4_BUSINESS_GATE_FAILED`，没有调用 ClauseSplitter.split。scan/mixed 的 native-only canonical 不含栅格正文，也不拿去切分后声称兼容。三位编号缺陷保持原样。

产物：[plus-L C1–C5](../../output/analysis/layout-model-selection-20260923/plus-l/C1-C5.json)、[V3 C1–C5](../../output/analysis/layout-model-selection-20260923/v3/C1-C5.json)，包含 clause gate 状态及所有前置证据。未用 clause count 代替兼容检查。

## PP-DocLayout_plus-L

| 维度 | 结论 |
|---|---|
| Geometry | B p1/p13 重复框；table 页一行未归属，其余所选 native 区域可归属 |
| Semantic exact | B 26/27 title、101/102 全部 native 行块；p13 失败 |
| Title family | B 27/27 |
| Reading order | 所选 native 正文/表格/Mixed 可由显式几何序恢复；不证明复杂多栏能力 |
| Duplicates | 2 组，均跨 doc_title/paragraph_title；class-agnostic NMS 可去框但不能修 subtype |
| Table | table 区域通过，封面标题能力较好；整页 geometry 未通过 |
| Mixed | 可分 native 与 raster；公式/说明区较碎 |
| Latency | 本轮每页 0.790–1.341 s，初始化 3.390 s；仅工程观察 |
| Integration complexity | 已有模型和几何顺序路径可复用；仍需合理的重复/subtype 决策，不能直接进入完整 Challenger |

## PP-DocLayoutV3

| 维度 | 结论 |
|---|---|
| Geometry | duplicate 少，但 B p1/p14 漏 Chapter 区域；table 页两行标题未归属 |
| Semantic exact | B 24/27 title、99/102 全部 native 行块；p13 未解决，表格页封面标题也有明显差异 |
| Title family | B 25/27 |
| Reading order | 模型原始 order 已保留；本数据未证明优于几何排序，需处理官方 skipped-label order=null 及漏框 fallback |
| Duplicates | 0 组（0.5 阈值、IoU≥0.90） |
| Table | table 区域通过；封面标题/未匹配 fallback 影响整页结构 |
| Mixed | 正文/栅格区域分离可用；官方后处理后的公式区更集中，非已证明的 order head 优势 |
| Latency | 本轮每页 1.113–1.790 s，初始化 1.348 s；仅工程观察 |
| Integration complexity | 需要支持 V3 的独立 runtime、polygon/order provenance、官方 overlap 与 skip-order 适配；公共 bbox merger 尚未利用 polygon 形状 |

每页 latency/RSS 原始值在两组 ledger。RSS：plus-L 约 1229–1302 MiB，V3 1298–1437 MiB；未控制冷启动/文件缓存，也未做多轮分布，因此不能据初始化差异或本次单页值做正式性能结论。

## 9. 进入完整 Challenger 的门槛与停止点

| 门槛 | 结果 |
|---|---|
| Native text conservation | PASS 两组；scan 为无 native 的空集检查 |
| Title semantic 满足真实生产契约 | **FAIL 两组（CONTRACT_A）** |
| Reading order | B 可映射部分通过；V3 table fallback 失败；复杂布局优越性未证明 |
| Geometry | **FAIL 两组** |
| Table region | PASS 两组 |
| Mixed coverage routing | PASS 当前两个 Mixed 的 native/raster 区域分离；不扩张成全面 OCR recall 保证 |
| NATIVE_GOOD 上无 Text Recognition | PASS，事实上本轮所有页 Recognition 均为 0 |
| ClauseSplitter compatibility | **未运行：业务门槛失败；不重用 Oracle 的通过结果替模型通过** |

最终：**`INSUFFICIENT_EVIDENCE`。两模型当前均未合格，停止在 geometry / exact semantic 层，不进入完整 Challenger。** 部署 trace unknown 没有阻塞本阶段，也没有用于这次否决。

复现按同一输入、同一评估器执行：[实验 README](../../experiments/layout_v3/README.md)。[verification.json](../../output/analysis/layout-model-selection-20260923/verification.json)核对冻结输入 hash、14 次真实 layout forward、全部 OCR/Structure 零调用、shuffle 不变性、C5 门槛及生产源文件未变。没有执行旧性能实验或完整 A–E。
