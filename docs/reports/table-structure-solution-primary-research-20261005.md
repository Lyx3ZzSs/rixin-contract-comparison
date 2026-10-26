# 合同有线表格可靠性：主源方案调研

日期：2026-10-05。范围：当前合同项目的轻量表格结构组件，目标同时保留行列、合并格、空格、OCR 归属及金额证据；不替换生产链路，不重新识别全部文字，不引入重 VLM。本报告仅新增主源调研；本轮旋转和证据定位实验由主 Agent 独立执行；本报告已读取其机器结果，局部实验结论不等于完整业务验收通过。

**建议顺序：先修接口和证据定位，再把 UNet 线分割与真实边线结合恢复拓扑；剩余失败才试单组件替代。** 当前结果没有证明必须换大模型。另一方面，规则修复也不能恢复模型从未检出的边线，持续漏检应进入领域数据标注及模型评估。

## 证据及版本边界

| 主源 | 固定依据 | 用途 |
| --- | --- | --- |
| MinerU 4.0.10 | 官方 tag `mineru-4.0.10-released`，commit `c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93` | UNet、连通域过滤、TableRecover、外部 OCR 接口 |
| PaddleX table v2 | `release/3.2` 固定源码快照 `523874c407834ac60c12b209b564ea25d19df2b2` | 与既有 PaddleX 3.2.0 实验接近的接口及后处理；不把该分支后续快照称作精确的 3.2.0 发布源码 |
| PaddleX 当前配置 | `release/3.7` 固定快照 `ffb64904d23708863ff5b8da312a5cbd52a7f462` | 核对截至调研日 wired/wireless 默认组件仍为 SLANeXt + RT-DETR-L |
| Docling | 源码快照 `b91e6b22b3fac9c15f3cb78b8db573c7c54f4d64`；TableFormer V1 官方模型 revision `v2.3.0` → `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | 小型专用结构模型的备用候选，不外推其它 TableFormer 版本 |
| Microsoft TATR | 官方固定快照 `16d124f616109746b7785f03085100f1f6247575` | 行列、spanning cell、内容匹配及空列后处理的边界 |
| LORE / SEMv3 | 作者论文及固定作者源码，下文逐项列出 | 逻辑位置预测、split-and-merge 的后续研发参考 |

当前项目实测依据：[UNet 边界与业务回放](mineru-unet-boundary-business-replay-20261005.md)、[UNet 组件验证](mineru-unet-validation-20261005.md)、[Paddle/TATR 拓扑诊断](complex-wired-table-topology-poc-20260924.md)、[三路线重测](table-routes-retest-20261005.md)。42 张追加表已参与问题检查与规则迭代，不能继续称独立未知测试集；5/42 检查未通过也不能称模型错误率。

## 1. 优先修复已有组件的接口与证据定位

**源码事实。** `TSRUnet.postprocess` 写的是 `rotated_fix = kwargs.get("rotated_fix") if kwargs else True`。传入 `need_ocr=False` 等任意 kwargs 后，未显式提供 `rotated_fix` 会得到 `None`，使旋转修正失效。旋转逻辑本来在角度超过 0.3° 时把线图转正，再把 cell 多边形逆变换。[MinerU 固定源码](https://github.com/opendatalab/MinerU/blob/c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93/mineru/model/table/rec/unet_table/table_structure_unet.py)

**适用建议。** 在适配器显式传 `rotated_fix=True`，或在受控分支修默认参数读取；先做单因素实验。它可以恢复原本存在的能力，不需要新权重。本轮 58 张回归单因素验证维持“默认失败才 pad64”策略：可发布表由 53 增至 54，只有 StateGrid 状态改变、无新增拒绝。原始 crop 默认仍为 7×15、估角 0°；pad64 后估角约 0.889°，启用转正得到 7×9，78/78 OCR 归属，人工核图确认行列及所查合并格。这证明该失败存在可修后处理因素，仍不能概括为所有模型错误都能靠转正解决。[本地机器结果](../../output/analysis/table-solution-exploration-20261005/deskew-summary.json)

**项目证据。** 主 Agent 本轮探针已发现华北 6 项金额证据回退到整格：HTML 挂在首个“不含税价” block 上，查找仅在该 anchor block 内进行，金额 OCR 则位于表格区域内其它 block。用实际差异文字、cell/page 范围及冻结 OCR 行独立寻找唯一候选，事后对照 Golden，6 项均通过现有位置阈值，IoU 从 0.169–0.296 提高到 0.811–1.000。这说明这批定位失败可以不换识别模型修复；它不是全部证据故障的总解释，也尚未通过完整业务回放。[定位探针机器结果](../../output/analysis/table-solution-exploration-20261005/evidence-probe.json)

**验证。** 输出每个候选的原始/转正多边形、逆变换误差、逻辑格及发布拒绝原因；定位输出 OCR/char evidence 的原始来源，重复金额必须用空间范围消歧，禁止根据期望框挑候选。分别报告“结构改进”和“证据改进”，再回放实际 CompareService。

**成本。** 一个参数及范围查找即可实现首轮验证，不增加模型加载。CPU 时间、收益和是否引入回归仍要以本地实测为准。

## 2. 保留 UNet，改进有证据支持的拓扑恢复

**源码事实。** TableRecover 用左上角 y 与固定 `rows_thresh=10` 分行，以包含最多格子的行为初始列模板，再把其它行的起止 x 插入边界；默认 `col_thresh=15`。合并跨度由宽高累计与固定 `merge_thresh=10` 决定。这里没有跨多行的边线支持投票，也没有对原图分隔线逐段核验。几何抖动可变成新的逻辑列，是合理机制解释，但仍需对失败中间输出验证。[TableRecover 固定源码](https://github.com/opendatalab/MinerU/blob/c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93/mineru/model/table/rec/unet_table/table_recover.py)

**适用建议，属于设计推断。** 在统一转正坐标系下：先聚类原始 cell 边界与 UNet 横竖线，按线宽/表格尺度校准容差；以多个行段的重复支持、原图边线和 cell 连接关系决定是否增加边界。合并格按“这一行段有没有实际分隔线”处理，不把短局部竖线补成贯穿整表的整列。候选恢复完成后才检查完整网格、跨度、冲突及每条 OCR 的唯一归属；存在歧义就拒绝发布，不强迫所有候选拼成漂亮 HTML。

这能针对边界抖动造列、部分合并格跨度错误及假空白边列；无法保证恢复完全漏检的细线、非矩形/嵌套表、严重遮挡。当前空白边列检查已经能拒绝已知 PO 伪列，但“拒绝”不等于“还原为正确的 12 列”。是否修复应有独立物理边界依据。

**字符归属的具体边界。** 本轮 PO p8 诊断发现 690 个字符中，`35100` 的 5 个字符中心落在相邻格子的间隙，距最近格约 0.75/1.25 pt；整词 overlap 归属虽然能通过，严格字符中心归属仍失败。同表 `16000` 的跨度还是 3、真值为 4，说明只放宽 owner 判定会把结构错误一起放行。应分别修共享边界几何与合并跨度，并保留字符证据检查；不能靠容差变松宣称表格修好了。

**更强的反例：检查全过仍有内部假分隔。** 本轮另对两张“默认组件已过、最终发布失败”的 PO 表补做 pad64 late retry。p1 从 13 列变为 12 列，但网格仍缺槽，所以继续拒绝。p8 的 143 条 OCR、690 个字符全部归属，`35100` 恢复到 col7、跨度 2，两处 `16000` 均恢复跨度 4，最终 Gate 也通过；然而原图核对发现首个横跨全表的标题仍被拆成 10 格，软件标题行同样残留假分隔。这是现行只检查外侧空列所覆盖不到的内部结构错误。不能把该候选称为整表正确，不能用“55/58 可发布”宣称准确率提高，也不能上线 late retry。它进一步支持逐行段真实边线约束及合并判断：文字全部归属、行列自洽只是必要条件。[late retry 机器记录](../../output/analysis/table-solution-exploration-20261005/late-retry-summary.json)

**大备注格特例。** MinerU 的连通域转 cell 仅保留 `w*h < 0.5*W*H`；这解释了南京面积 51.7% 的备注格被过滤。后续可把“外部背景”判定从单一面积规则改成连通域是否触及图像外缘、是否由可信线包围等；禁止直接取消面积过滤，否则背景也可能成为大格。已有失败时加白边是低成本隔离验证手段，不能对所有表无条件启用。[连通域过滤源码](https://github.com/opendatalab/MinerU/blob/c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93/mineru/model/table/rec/unet_table/utils_table_line_rec.py)

**验证。** 第一轮保持 UNet 输出不变，仅替换 TableRecover；另一轮保持恢复规则不变，仅调整大格过滤。对 58 张全量逐格标注，统计误发布和真空格误拒绝；额外使用未参与调参的合同盲测。合成倾斜、DPI 和空白边列样本只验证不变量，不能替代真实合同准确率。

**成本。** 不加模型，主要增加小规模边界聚类/连接计算和中间证据存储。现有独立 UNet 进程峰值约 1.48 GB，而非只有 8 MB 权重；应继续测 RSS，避免把线图/标签图和 PDF 渲染缓存一起常驻。

## 3. Paddle 只做 detector-only 消融，避免重跑已失败的 HTML 对齐

**官方路线。** PP-StructureV3 的有线和无线表分别使用 SLANeXt 结构模型与 RT-DETR-L cell detector；当前固定配置仍是这套组合。[当前固定配置](https://github.com/PaddlePaddle/PaddleX/blob/ffb64904d23708863ff5b8da312a5cbd52a7f462/paddlex/configs/pipelines/table_recognition_v2.yaml)

官方说明 SLANeXt 的 cell 坐标不能作为有效预测使用；SLANet/SLANet_plus 为 6.9 MB，SLANeXt 为 351 MB。SLANet_plus 改进无线、复杂表和裁剪偏移鲁棒性，但这些说明不能推导它在当前复杂有线合同里优于 UNet，更不能推导完整金额证据正确。[结构模块官方文档](https://www.paddleocr.ai/main/en/version3.x/module_usage/table_structure_recognition.html) RT-DETR-L wired detector 官方列出的模型存储为 124 MB；文档的 Intel Xeon/T4 推理时间不是本项目 Mac 的延迟。[cell detector 官方文档](https://www.paddleocr.ai/main/en/version3.x/module_usage/table_cells_detection.html)

**不同于旧对齐的消融机制。** `use_wired_table_cells_trans_to_html=True` 跳过 SLANeXt HTML，由 detector 框直接恢复结构；`use_ocr_results_with_table_cells=False` 使用已有 OCR，避免换文字识别变量。它与此前失败的 TopologyGeometryAligner 不同：旧 B 方案保留已经缺槽的 HTML，只重新匹配几何；新消融把 HTML 结构来源整体去掉。官方转换却使用固定 5 px 聚类，缺槽填空 td、重叠按先占位处理；这些不能当作真实格子的证明。[Paddle 固定 pipeline 源码](https://github.com/PaddlePaddle/PaddleX/blob/523874c407834ac60c12b209b564ea25d19df2b2/paddlex/inference/pipelines/table_recognition/pipeline_v2.py)

**本轮缓存实测。** 主 Agent 已在历史 9 个 crop 上完成 detector-only 消融：从本机 PaddleX 3.7.2 用 AST 原样抽取 `cluster_positions`、`split_string_by_keywords`、`trans_cells_det_results_to_html` 三个纯函数；使用已有 RT-DETR after-NMS 框，跳过 SLANeXt 及其按结构数量裁框/补框，不加载模型、不增加 OCR。crop SHA 和 OCR 文字/bbox 与冻结输入逐项一致。几何直接来自 detector 框，以官方 5 px 端点聚类映射逻辑位置，不按 HTML 序号配框；另外审计物理占位的 holes/overlaps，未把 HTML 自动填空 td 算作真实格。

统一组件与 RasterGate **4/9** 通过：南京 p4 原件/对比件、凯里 p3/p4；5 个原金额验收 **3/5** 通过，`17100`、`12400`、`118000` 的文字、行列、跨度、图片框和 PDF 框全部通过，两处 `64500` 仍失败。余下 5 张有严重重叠或 OCR 未归属等问题。[本轮机器结果](../../output/analysis/table-solution-exploration-20261005/paddle-detector-only-summary.json)

**适用边界。** 这证实部分旧失败在结构/检测后处理而非数字识别；不能解释为完整 PP-StructureV3 新推理达到 4/9，也不支持全量替换 UNet。若继续研发，应对失败的 detector 框增加真实线支持、冲突校准及有证据的缺格补全，而不是重复 Hungarian 排列实验。

**验证及成本。** 本轮已复用缓存验证机制差异；下一步应在真正未知 crop 上重新推理并与 UNet 比较，重点核查真实空格、跨格金额和 cell 数不足的表。官方完整 pipeline 的构造仍实例化 wired/wireless 两套结构及 detector；只改 predict 开关并不能声称内存降到 124 MB。真正轻量接入需只加载单组件和自有适配器，增加 detector→逻辑位置的实现成本。

## 4. 剩余难表的备用候选：TableFormer；TATR 借鉴约束，不再泛泛重测

**TableFormer 的可用边界。** Docling 固定实现可输入 table crop 与外部文本 token，输出带逻辑跨度及框的 TableCell，支持 fast/accurate 和 cell matching；当前实现对 MPS 强制回退 CPU。[Docling 固定源码](https://github.com/docling-project/docling/blob/b91e6b22b3fac9c15f3cb78b8db573c7c54f4d64/docling/models/stages/table_structure/table_structure_model.py) 官方 `v2.3.0` 文件元数据：fast safetensors 为 145,453,276 bytes，accurate 为 212,758,388 bytes。这是权重存储，不是 RSS，也不是已测性能。[fast 权重](https://huggingface.co/docling-project/docling-models/tree/fc0f2d45e2218ea24bce5045f58a389aed16dc23/model_artifacts/tableformer/fast)、[accurate 权重](https://huggingface.co/docling-project/docling-models/tree/fc0f2d45e2218ea24bce5045f58a389aed16dc23/model_artifacts/tableformer/accurate)

它可作为“UNet+边线约束仍无法发布”的少量表格候选，复用当前 OCR；避免整份 PDF 又跑 Docling 布局/OCR。尚未对当前难表做新 TableFormer 推理，不报告收益、错误率或资源峰值。自回归结构也可能漏行/错跨度，仍必须接受同一发布检查。fast/accurate 应一起做本地小样本对照，而不是直接采信名字。

**TATR 的实际边界。** 当前项目旧 C 方案是 TATR，9/9 拓扑失败、7/9 几何通过，不能称可接入。官方专用结构权重约 110 MB，模型不读文字，需要外部 OCR/PDF token；官方仓库在调研时显示已于 2026-09-21 archived。[官方仓库](https://github.com/microsoft/table-transformer) 不建议没有实质新机制就重测相同 checkpoint。

可借鉴其行列对齐、spanning-cell 冲突消解、唯一 token 归属；但官方 `refine_rows/refine_columns` 会调用 `remove_objects_without_content`，空行空列可能被删除。合同有真实空白列，不能原样照搬；需由实际边线支持空格，不能以“没文字”判其虚构。[TATR 固定后处理](https://github.com/microsoft/table-transformer/blob/16d124f616109746b7785f03085100f1f6247575/src/postprocess.py)

**验证。** 相同 crop 和外部 OCR，显式保存完整空格与 token；检验 header/body、长跨度、图像框/PDF框、端到端差异；记录首次加载和稳态延迟、RSS。接入成本是额外 PyTorch/docling 模型依赖、外部 token 适配和模型常驻管理，明显大于 UNet 参数/规则修复。

## 5. 只有确认持续模型漏检后，再做领域标注和轻量模型研发

**研究事实。** LORE 同时预测 cell 几何和逻辑起止行列，减少纯规则恢复依赖；SEMv3 先预测分隔线，再学习网格合并。它们对应两种有价值的研发方向：学习跨度关系，或学习缺线/形变后的分隔与合并。[LORE 作者论文](https://arxiv.org/abs/2303.03730)、[SEMv3 作者论文](https://arxiv.org/abs/2405.11862)

**工程边界。** LORE 作者代码的 wired DLA 路线依赖旧 CUDA/DCNv2 栈，README 还包含仅研究用途表述；当前不能称 Mac 上开箱即用的生产替代。[LORE 固定 README](https://github.com/AlibabaResearch/AdvancedLiterateMachinery/blob/fb6187ebe5374d5682ac0bc05f31e3639ab6fb15/DocumentUnderstanding/LORE-TSR/README.md) SEMv3 作者发布主要是训练/推理脚本，需要自行配置数据和路径；研究方法“fast”不是当前项目 CPU 延迟的证据，其依赖、权重获取和训练成本必须另测。[SEMv3 固定代码](https://github.com/Chunchunwumu/SEMv3/tree/d6889afe691865e18345a2580e9bfbc3573b7a19)

**适用建议。** 暂不优先引入这两个完整研究工程。先按首个失败阶段给合同表分类：原始线 mask 漏边、连通域损坏、拓扑恢复错误、OCR跨格、证据查找错误。只有原始模型层失败持续占主导，才比较 UNet 领域微调、SLANet_plus 领域训练或新的逻辑位置模型；训练集应包含细线、章遮挡、实际空格、倾斜、长备注及大跨度。Paddle 官方提供 SLANet_plus 训练/导出入口，但没有证据保证其单模型坐标能满足合同验收。[SLANet_plus 训练配置](https://github.com/PaddlePaddle/PaddleOCR/blob/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf/configs/table/SLANet_plus.yml)

**验证。** 按合同/模板分组切分训练与未知测试，禁止同模板原件/对比件分别落进训练和测试。指标分别统计逐格拓扑、金额文字与归属、空格保存、证据定位、错误结果放行率、拒绝率；最终再核验合同差异的真实漏报/误报。当前没有逐格完整真值，不能据已有通过数决定训练规模、准确率目标或成本。

## 决策与最小验证顺序

1. 完成旋转参数和表区域 OCR/char evidence 查找的单因素验证；保持文字来源与生产验收阈值不变。
2. 原始线 mask 固定，试尺度校准的边界聚类和逐段真实边线支持；大备注格过滤独立消融。
3. detector-only 缓存消融已完成（4/9 表、3/5 金额通过）；仅在未知样本重新推理及解决框冲突后评估备用接入。
4. 仅剩余无法发布的难表评估 TableFormer fast/accurate；仍不满意才启动模型领域适配。
5. 对 58 张回归表补全真值，再增加未参与调参的合同；完整合同差异通过前不切生产。

已证实局部收益的是旋转参数、金额范围定位及 Paddle detector-only 缓存消融；尚未证实的是有线拓扑恢复改造、未知样本泛化及 TableFormer 备用候选的收益。固定面积过滤、Paddle 结构/检测分离及替代模型公开资源/接口边界均有主源依据。不能把所有 Golden 失败项归因于表格结构，也不能把采样金额全部通过当成整体准确率。
