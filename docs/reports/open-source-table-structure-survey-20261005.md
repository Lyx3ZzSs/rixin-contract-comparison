# 开源文档解析项目的表格结构路线调研

访问日期：2026-10-05（Asia/Shanghai）。本报告核对 Docling、PaddleOCR、MinerU、Marker、PyMuPDF、pdfplumber、Camelot、TATR 等项目的官方资料和源码，并结合本项目已有冻结实验判断演进方向。

## 范围与结论边界

本次使用官方仓库、官方文档及原作者论文，读取源代码，没有下载模型、运行推理或修改生产代码。“主流”表示这些有完整公开实现的代表性路线，不表示经过市场份额统计的占比；不按 GitHub stars 或作者宣传分数判定最好。字段存在只证明接口表达能力，不证明每张合同表的值和位置都正确。

调查显示，**原生文本与几何规则、专用结构模型、视觉语言模型同时存在，混合和分流方案很常见**。特别是当前 Marker 已改为原生文字规则加生成式 HTML 回退，当前 MinerU 也有原生几何优先路径，因此不能笼统断言现代开源解析器都依靠专用表格结构模型。证据见下文各项目固定版本源码。

## 版本快照

| 项目 | 本次核对范围 | 需要区分的历史版本 |
|---|---|---|
| Docling | `main`，`eda5b5fc86d10b2b79a900ff3e69fc159d5f6014`，提交时间 2026-10-04 | TableFormer V1 默认、V2 与 Granite Vision 可选，不能把可选模型说成默认 |
| PaddleOCR/PaddleX | PaddleX `release/3.7`，`ffb64904d23708863ff5b8da312a5cbd52a7f462`；PaddleOCR 官方 releases | PP-StructureV3、独立 TableRecognitionV2 与 PaddleOCR-VL 是不同配置和路线 |
| MinerU | `master`，`ed50cc15bc2c9bfb00520dadfe61979866e62236`，提交时间 2026-09-29；当前稳定 release `mineru-4.0.10-released` | 3.4.0 `562ba5849a03489a1e7b9883255b5ff1a768fa01` 的 pipeline/hybrid/vlm 名称不等于 4.x tier |
| Marker | `master`，`e7c67f1d239ea6a805cbf4ed6c6b2056d435e22d`，提交时间 2026-10-02；已发布 2.0.0 | 1.10.2 的 Surya `TableRecPredictor` 不能代表当前主线 |

时间与 release 状态核对来自各官方仓库 GitHub API 的 commits/tags/releases；固定链接保留在各节，不从动态 README 推定已安装版本。MinerU 当前 release 见[官方发布记录](https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.10-released)，Marker 2.0 见[官方发布记录](https://github.com/datalab-to/marker/releases/tag/v2.0.0)。

## 1. Docling：专用结构模型，结构与文字解耦

默认 PDF 表格配置仍是 **TableFormer V1、ACCURATE、`do_cell_matching=True`**；`TableStructureV2Options` 和 `GraniteVisionTableStructureOptions` 是可选配置，不能因为文件存在就称其已经默认替代 V1。[固定配置源码](https://github.com/docling-project/docling/blob/eda5b5fc86d10b2b79a900ff3e69fc159d5f6014/docling/datamodel/pipeline_options.py#L151)，[默认实例](https://github.com/docling-project/docling/blob/eda5b5fc86d10b2b79a900ff3e69fc159d5f6014/docling/datamodel/pipeline_options.py#L2144)。

TableFormer 的结构预测读取表格图像，文字通过页面已有 text cells 匹配。当前 V1 实现组织 `id/text/bbox` 输入调用 `multi_table_predict(..., do_matching=...)`，再构造 `TableCell`；它并非必须自己重做 OCR。论文的结构解码器和 cell bbox 解码器也证明其不仅生成可读表格文本。[当前 V1 源码](https://github.com/docling-project/docling/blob/eda5b5fc86d10b2b79a900ff3e69fc159d5f6014/docling/models/stages/table_structure/table_structure_model.py#L231)，[原作者论文](https://arxiv.org/abs/2203.01017)。

**已有 OCR 可以复用，但需要适配内部数据契约。** 当前 table cluster 的 cells 包括已有 OCR 分配的文字；V1 的独立图像推理辅助方法也接收具有唯一 index、文本和页面坐标的 cells。它不是一个现成的“传任意外部 OCR JSON”公开端点，应做受控适配并固定版本。[输入与唯一 ID 检查](https://github.com/docling-project/docling/blob/eda5b5fc86d10b2b79a900ff3e69fc159d5f6014/docling/models/stages/table_structure/table_structure_model.py#L321)，[V2 明确使用 OCR-assigned cells](https://github.com/docling-project/docling/blob/eda5b5fc86d10b2b79a900ff3e69fc159d5f6014/docling/models/stages/table_structure/table_structure_model_v2.py#L484)。

可选 **TableFormer V2** 输出 OTSL 与 `predicted_bboxes`，转换成 `bbox`、`row_span`、`col_span`、行列起止 offset，bbox 再变换到页面坐标；text 按 overlap 匹配。bbox 可能为 `None`，匹配仍可能失败，因此这些字段需要本项目 validator 和 evidence gate 校验。[V2 模型与预测](https://github.com/docling-project/docling/blob/eda5b5fc86d10b2b79a900ff3e69fc159d5f6014/docling/models/stages/table_structure/table_structure_model_v2.py#L39)，[字段构造](https://github.com/docling-project/docling/blob/eda5b5fc86d10b2b79a900ff3e69fc159d5f6014/docling/models/stages/table_structure/table_structure_model_v2.py#L252)。

定位：适合作为“保留现有 OCR，仅增加结构推断”的新增评估对象；尚无本项目实测收益。先评估已发布且能固定的 V1，V2 单独记版本与结果，不默认认为 V2 更好。此为基于接口的工程判断，不是性能结论。

## 2. PaddleOCR：模块化专用模型与 VL 两条路线

当前核对的 **PP-StructureV3** 是版面、OCR、表格等子模块的组合：表格子流水线为 `table_recognition_v2`，有线结构 `SLANeXt_wired`、无线结构 `SLANet_plus`，两类都有 RT-DETR-L cell detection；子流水线 `use_ocr_model=False`，上层提供 OCR。[固定 PP-StructureV3 配置](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/configs/pipelines/PP-StructureV3.yaml)。

**独立 TableRecognitionV2 的默认配置有所不同**：有线和无线都使用 SLANeXt（`wired`/`wireless`），配表格分类、RT-DETR-L cell detector 和 OCR。不要把独立流水线配置套到 PP-StructureV3 上，也不要把旧 SLANet 路线当成全部现状。[固定独立配置](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/configs/pipelines/table_recognition_v2.yaml)，[官方流水线使用文档](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/table_recognition_v2.html)。

TableRecognitionV2 接收 `overall_ocr_res` 与 `layout_det_res`，配合 `use_ocr_model=False`、`use_layout_detection=False` 可复用已有 OCR/版面结果。输出 `cell_box_list` 和 `pred_html`；HTML 表达行列及 rowspan/colspan，但该结果不等于 Docling 式显式 cell row/col/span JSON。需要解析 HTML 并保持与 box 一一对应，不能只凭列表长度假定对齐。[输入接口](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/inference/pipelines/table_recognition/pipeline_v2.py#L1124)，[输出匹配源码](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/inference/pipelines/table_recognition/table_recognition_post_processing_v2.py#L410)。

复用 OCR 不代表永远零次追加识别：`use_ocr_results_with_table_cells=True` 分支可能按 cell 重新识别或拆分跨 cell 字框；已有文字完全复用要显式配置并验证分支。源码还会按结构期望数量过滤、补充 cell boxes，这是模型加规则后处理，并非无误差结构证据。[追加 OCR 分支](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/inference/pipelines/table_recognition/pipeline_v2.py#L1067)，[cell 数量与 OCR 补充处理](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/inference/pipelines/table_recognition/pipeline_v2.py#L404)。

**PaddleOCR-VL 是另一条生成式路线。** 官方 3.6.0 已发布 VL-1.6；已核对的 VL-1.6 配置用 PP-DocLayoutV3 定位，`PaddleOCR-VL-1.6-0.9B` 识别区域。VL 代码以 `Table Recognition:` 提示处理表格图像，结果字符串作为 block content，block bbox 是表格区域位置，不能直接视为每个 cell 的定位证据。当前核对的接口没有外部 OCR cell 匹配输入，也没有证实统一、可靠的 cell bbox＋row/col/span 契约；text spotting 的 bbox 也不自动等于 table cell bbox。[官方 1.6 发布](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.6.0)，[固定 VL-1.6 配置](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/configs/pipelines/PaddleOCR-VL-1.6.yaml)，[VL 表格与 block 输出实现](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/inference/pipelines/paddleocr_vl/pipeline.py#L543)。

## 3. MinerU：3.x 多后端与 4.x 原生优先、多档位

历史 **3.4.0 pipeline** 用 OCR、表格分类、无线 SLANet_plus 和有线 UNet 路线，比较候选 HTML 并回填。OCR 与结构不是同一个模型；原子模型可接受 `ocr_result`。上层 pipeline 最终主要保存 table HTML，不能由底层模型字段的存在推断普通 CLI 输出已保留完整 cell graph。[3.4.0 模型初始化](https://github.com/opendatalab/MinerU/blob/562ba5849a03489a1e7b9883255b5ff1a768fa01/mineru/backend/pipeline/model_init.py)，[3.4.0 表格处理](https://github.com/opendatalab/MinerU/blob/562ba5849a03489a1e7b9883255b5ff1a768fa01/mineru/backend/pipeline/batch_analyze.py#L658)。

3.x 的 **hybrid/vlm** 是整页/区域解析系统，而非一个独立表格结构 API；官方 changelog 记录 hybrid 结合原生 PDF 文本、扫描 OCR 和 VLM 能力，并切换默认后端。4.x 已把公开入口改为 `flash/basic/standard/advanced` tiers，旧 backend 名称不可直接映射为同一质量默认值。[官方变更记录](https://opendatalab.github.io/MinerU/reference/changelog/)，[4.0 迁移说明](https://opendatalab.github.io/MinerU/reference/migration_4/)。

**当前 4.x 也有原生表格优先**：从 PDF 文字与矢量几何调用 docvortex `recover_table_region`；通过原生恢复 gate 后使用其 HTML，失败或复杂内容保留模型回退。`high` 路径会从 VLM 输入中移除已经原生命中的表格，再恢复回结果，不是所有表格都必须送 VLM。[原生优先与接受/拒绝](https://github.com/opendatalab/MinerU/blob/ed50cc15bc2c9bfb00520dadfe61979866e62236/mineru/backend/analysis/pdf/tables.py#L202)，[命中后跳过 VLM](https://github.com/opendatalab/MinerU/blob/ed50cc15bc2c9bfb00520dadfe61979866e62236/mineru/backend/analysis/pdf/tables.py#L361)。

当前 `basic→medium`、`standard→high`、`advanced→xhigh`；默认 tier 为 standard。`medium` 表格分支依然是逐表 OCR＋UNet/SLANet_plus 结构识别。底层 SLANet 对象提供 `cell_bboxes/logic_points`，UNet 输出也有 polygons 和逻辑行列，可传入 OCR；但上层常规结果仍以 table block 的 `content=HTML` 为中心。若要做本项目的 cell evidence，必须指定提取底层 metadata 的适配路径。[当前 tier 映射](https://github.com/opendatalab/MinerU/blob/ed50cc15bc2c9bfb00520dadfe61979866e62236/mineru/parser/tier.py#L12)，[medium 表格识别](https://github.com/opendatalab/MinerU/blob/ed50cc15bc2c9bfb00520dadfe61979866e62236/mineru/backend/analysis/pdf/tables.py#L884)，[SLANet 输出与 OCR 输入](https://github.com/opendatalab/MinerU/blob/ed50cc15bc2c9bfb00520dadfe61979866e62236/mineru/model/table/rec/slanet_plus/main.py#L29)，[UNet metadata 输出](https://github.com/opendatalab/MinerU/blob/ed50cc15bc2c9bfb00520dadfe61979866e62236/mineru/model/table/rec/unet_table/main.py#L271)。

VL 路线的统一模型承担 layout 和 text/table/equation recognition；公开模型工具描述的是结构化解析块，并不能证明生成 HTML 的每格都附带可信原始文字框。当前未验证所有 high/xhigh engine 的低层 cell 定位一致性，应列为待验证而不是断言它们绝无 bbox。[官方 VL 工具仓库](https://github.com/opendatalab/mineru-vl-utils)。

## 4. Marker：当前已移除专用表格结构模型

**当前 `TableProcessor` 明确写出没有 dedicated table-structure model**：原生数字 PDF 表格从 pdftext 页面文字以 CPU 几何规则重建 HTML；低分/稀疏等失败表格由 Surya `RecognitionPredictor` 读取区域图像生成 HTML；扫描页已有全页 OCR 表格 HTML 时直接复用。这说明规则路线在当前综合解析器里仍有实质地位。[当前处理器](https://github.com/datalab-to/marker/blob/e7c67f1d239ea6a805cbf4ed6c6b2056d435e22d/marker/processors/table.py#L22)。

当前数字 PDF 与 crop OCR 成功分支都设置 `block.structure=[]` 并写 `block.html`。这两条分支没有建立逐 cell 的 schema 对象，不能因为 README 历史示例称 JSON 可输出 cell bbox，就认为当前所有表格都可靠提供 cell bbox、row/col/span。表格 block bbox 与 HTML rowspan/colspan 仍可存在，原始每格证据需要额外验证和回填。[原生分支](https://github.com/datalab-to/marker/blob/e7c67f1d239ea6a805cbf4ed6c6b2056d435e22d/marker/processors/table.py#L75)，[crop OCR 与输出分支](https://github.com/datalab-to/marker/blob/e7c67f1d239ea6a805cbf4ed6c6b2056d435e22d/marker/processors/table.py#L128)。

历史 **1.10.2** 则确实使用 Surya `TableRecPredictor`，创建 `TableCell` 的 `row_id/col_id/rowspan/colspan` 并匹配文字；当前 schema 文件仍保留这些字段不等于当前默认链路会填充它们。外部 OCR 任意输入并非已核实的公开稳定接口。[历史处理器](https://github.com/datalab-to/marker/blob/5a41cdbac623/marker/processors/table.py#L11)，[当前 cell schema](https://github.com/datalab-to/marker/blob/e7c67f1d239ea6a805cbf4ed6c6b2056d435e22d/marker/schema/blocks/tablecell.py)。

可选 LLM 表格纠正主要接收图像和 HTML；解析纠正 HTML 时新建 bbox 可使用行列单位网格数值，而非图像上原始测量的每格边界。故“JSON 出现 bbox”仍须审核来源语义，不应直接作为本项目差异高亮证据。[当前 LLM 表格重建](https://github.com/datalab-to/marker/blob/e7c67f1d239ea6a805cbf4ed6c6b2056d435e22d/marker/processors/llm/llm_table.py)。

## 5. 结构接口对比

| 路线 | 外部已有 OCR 复用 | cell bbox | row/col/span | 对本项目的限制 |
|---|---|---|---|---|
| Docling 默认 TableFormer V1 / 可选 V2 | 内部 text-cell 契约可复用，需要适配 | 模型预测并转换；可缺失 | 结构化字段，V2 可直接核对构造代码 | 匹配与 bbox 仍需独立校验；V2 不能假定已优于 V1 |
| Paddle TableRecognitionV2 | `overall_ocr_res` 明确支持 | `cell_box_list` | 主要来自 HTML 结构，需适配 | 默认可能追加 OCR；cell 数量和排序由后处理修复 |
| PaddleOCR-VL | 未核实外部 OCR 字框结构匹配 API | 已证实 block 定位；未证实统一 cell 定位 | HTML 结构，非已核实统一 cell graph | 生成内容和 evidence 需要分离验证 |
| MinerU 原子 UNet / SLANet | 可传 `ocr_result` | 低层 metadata 存在 | 低层 logic points/HTML | 常规输出不保证保留；wrapper 与底层接口不同 |
| MinerU 当前标准/高级 VL 路线 | 复用原生文字是系统能力，不等于任意外部 OCR 注入 | table block 已证实，逐 cell 需核实 | HTML/块内容 | 必须固定 tier、模型与 engine，不能沿用旧 backend 结论 |
| Marker 当前主线 | 原生 pdftext / 已有 full-page OCR HTML | 常规表格两分支不建立 cell objects | HTML 中可能表达 span | 空 structure 与合成 bbox 不足以支撑 cell evidence |

以上对比由各节固定源码直接支持；“可提供”不表示经过本项目 accuracy 验收，也不表示推断位置是真实测量位置。

## 6. 尚未验证的范围

- 未复跑本项目已有实验，不用这些项目自己的 benchmark 或 marketing 数字替代合同集验收。
- 未核对全部历史模型/engine、设备性能、显存和权重许可证；上线选择需要针对固定版本另行核对这些部署条件。
- 本报告的模块适配判断不是实施结果；原生 PDF 工具、TATR 与本项目最新样本结果见下文。

## 原生 PDF 与经典 TSR 路线补充

| 项目 / 固定范围 | 官方实现与证据 | 对本项目的意义 |
|---|---|---|
| PyMuPDF 当前文档 / 仓库 `27bd8f8` | `find_tables()` 默认 `lines`，使用 PDF 向量图形；另有 `lines_strict` 和按文字位置生成虚拟边界的 `text`。返回表区与 cell 坐标。[官方 API](https://pymupdf.readthedocs.io/en/latest/page.html#Page.find_tables) | 当前 native 路线属于成熟实现；项目目前只调用默认策略，不能将库全部能力等同于现有覆盖能力。 |
| pdfplumber / `4c64b92` | PDF 线条或文字对齐 → 合并线段 → 交点 → cells → tables；默认 lines，也有 text/explicit。官方说明更适合机器生成 PDF。[官方 README](https://github.com/jsvine/pdfplumber/blob/4c64b92d5caccd71c645e98e0fabb0c4dba7ff45/README.md#extracting-tables) | 证明原生 PDF 的几何规则仍是常用路线，不能据此声称同一方法能恢复扫描表。 |
| Camelot 当前 2.0 文档 / 仓库 `a68bb9f` | Lattice 用 OpenCV 形态学获取横纵线，恢复交点与跨格；Stream/Network 用文字位置；Hybrid 结合两者。新文档还提供可选 TATR ML，用模型结构与 PDF 原文或 OCR 填字。[官方原理](https://camelot-py.readthedocs.io/en/latest/user/how-it-works.html) | OpenCV 格线方法有成熟同类；项目当前 contour→graph 是自研实现，不能直接继承 Camelot 的合并格处理能力或测试结论。新版已含扫描/ML 路线，不沿用旧版“只能文字 PDF”的笼统结论。 |
| Microsoft TATR / `16d124f` | 图像模型检测 table、row、column、spanning cell；文字需单独从 PDF/OCR 输入。后处理形成 `bbox/row_nums/column_nums/spans` 并导出 HTML/CSV。[README](https://github.com/microsoft/table-transformer/blob/16d124f616109746b7785f03085100f1f6247575/README.md)、[inference.py](https://github.com/microsoft/table-transformer/blob/16d124f616109746b7785f03085100f1f6247575/src/inference.py) | 与现有 OCR 复用思路兼容，但“接口有字段”不保证复杂合同字段正确；项目已有失败样本，不应换一层包装就当新方案。 |
| RapidDoc / `60cd038`，辅助对照 | `rule_table.py` 实际实现 native chars + vector rules、覆盖率/冲突拒绝及合并跨度；demo 暴露 `use_rule_table` 与模型回退配置。RapidTable 接口支持传入外部 `ocr_results`。[规则源码](https://github.com/RapidAI/RapidDoc/blob/60cd038d424e0e839462ba4bd96345e0279290fe/rapid_doc/model/table/rule_table.py)、[示例配置](https://github.com/RapidAI/RapidDoc/blob/60cd038d424e0e839462ba4bd96345e0279290fe/demo.py)、[OCR 交接](https://github.com/RapidAI/RapidDoc/blob/60cd038d424e0e839462ba4bd96345e0279290fe/rapid_doc/model/table/rapid_table_self/main.py) | 支持“原生规则优先、模型补救”的架构方向；示例注释不作整套部署默认值或效果证明。 |

以上是代表性项目的实现调查，不是安装量、市场份额统计；“主流路线之一”指多个成熟项目持续采用同类方法，不能推出某一种方法占多数。

## 当前项目定位与已有实验结论

当前 `TableResolver` 只在 PP-DocLayoutV3 标记的 table region 内处理结构；有 native chars 时尝试 PyMuPDF 默认 `find_tables()`，否则/未找到时用 OpenCV 固定二值阈值与形态学、轮廓外框构建 cell graph。文字来自已有 native/OCR tokens，几何和文字归属经过 validator。无法确认的结构不发布 HTML。[正式入口](../../backend/app/services/extractors/contract_v3/tables.py)、[graph](../../backend/app/services/extractors/contract_v3/table_cell_graph.py)。

这属于规则结构恢复与模型版面/OCR组合。原生表路径有清晰证据优势；扫描表恢复当前高度依赖可见格线，尚无独立扫描结构模型或模型补救路径。原生结构候选一旦被选中，即使发布校验失败，也不会继续尝试另一个结构 provider；这是后续路由设计可考虑的具体接缝，不代表应该放宽 validator。

已有数据限制了“引入专用模型就会成功”的推断：

- 有限收口回放中，16 个既存 graph 有 3 个发布、13 个拒绝；9 个 PDF vector 表均发布。这是固定样本的发布状态，不是总体识别准确率。[acceptance-closure](acceptance-closure.md)
- Paddle V2 历史实验恢复五张误判表中的三张简单表；复杂报价表失败，五个关键金额要求 text/row/col/span/bbox 同时正确时为 0/5。TATR 同批 16 crop 的 Evidence gate 为 0/16。[Paddle/TATR POC](scanned-table-paddle-vs-tatr-poc-20260924.md)
- 旧 MinerU wired 单独模型实验使五个关键金额达到 5/5，但 NANJING p3 错合并且重叠、p4 漏备注整行，未通过整表 gate。该结果只针对当时固定源码/权重，不能代表当前 MinerU4 全流程。[wired benchmark](complex-wired-table-benchmark-20260924.md)

## 建议的演进与下一次实验边界

建议保留 native 字符/向量的优先路径，补充结构候选 provider，而不是将所有 PDF 统一重新 OCR。按 native 可验证结构、扫描/无线/复杂表、无法恢复三类处理；所有输出归一为现有 cell graph / TableArtifact，再经同一安全门。规则失败可以尝试模型，模型失败仍保留全文、页面证据和待复核状态。此处是调研后的架构判断，尚未实施。

优先新增 **Docling TableFormer 对照**：它能将结构预测与现有文字框匹配，且公开 cell/跨度/坐标，较贴合本项目的程序化比较与证据要求。先固定 V1 ACCURATE 与 V2 各自源码、模型权重、配置；只在已有困难 crop 上适配现有 PP-OCR 字框，核实坐标粒度和 matching 行为。此优先级来自接口匹配和“本项目尚未评估”，不是声称其中文合同效果最好。

Paddle、TATR 和旧 MinerU wired 沿用现有失败基线；只有模型/配置/实现有明确变化时才重测。MinerU4 / PaddleOCR-VL / Marker 的识别或生成路径可作为后续补救对照，但若只有整表 HTML 与 table bbox，需补齐真实 cell 定位和 token evidence，才能输入确定性结构比较。不能按序把 HTML cells 与独立检测框强配。

评价应同时检查：

1. 整行/整列完整性、空格子和合并跨度；局部金额成功不能代替整表通过。
2. 关键金额/日期原文保真、OCR 唯一归属、未分配连续行带。
3. cell bbox 与 page/PDF 坐标、原文高亮实际对应。
4. Topology、Location、Content 可分开统计；GriTS 官方也是按这三个维度评价。[GriTS/TATR 评价说明](https://github.com/microsoft/table-transformer/blob/16d124f616109746b7785f03085100f1f6247575/README.md#evaluation)
5. 冻结 crop 验证通过后，再进行三合同 downstream 回放与完整 Golden；识别指标和最终业务误差分别记录，另测实际延迟和内存。

本轮只进行网页/源码调研及读取既有证据，未下载模型、运行推理、修改正式算法、更新 Golden 或部署服务。
