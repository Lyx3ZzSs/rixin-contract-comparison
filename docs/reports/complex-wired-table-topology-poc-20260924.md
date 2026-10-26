# Complex Wired Table Topology POC

## 十项直接结论

1. **实际验证 9 个固定失败 crop**：RETURN_CONFIRMATION D009 p4/p5/p6；KAILI_FOUR_PROJECTS D007 p3/p4；NANJING_DINGYI_XUZHOU_BANGYU 的 Original/Compare p3/p4。输入的 9 个像素 SHA256 与 OCR 文字+bbox SHA256 全部等于上一轮 ledger；共 836 条冻结 PP-OCRv6 行框。第三份上一轮未留 PNG，本轮按其冻结 PDF、render crop 与逆仿射重建像素，SHA256 完全一致；没有重新 OCR。
2. **Paddle 原始首个失败层**：8/9 在结构解码后的 HTML 网格（缺槽或可见行列不足），1/9 在 cell detector / 后处理。独立几何还存在 bbox 缺失、重叠或错序，不能用统一的“structure failed”概括。
3. **HTML cell 与 bbox 数量不一致的根因**：`SLANeXt_wired` 单独给出结构 token 与每个逻辑 cell 的四边形；`RT-DETR-L_wired_table_cell_det` 单独给出 cell 检测框。5 张表检测器原始可用框就少于结构 cell（95<106、95<98、103<108、103<112、19<20）。后处理只在发现 OCR 漏框时补框；没有漏 OCR 框就保留较少 bbox。后续排序/HTML 填字并不建立可靠的一对一对应。另 3 张检测框多于结构 cell，按分数裁剪/合框损伤几何。
4. **TopologyGeometryAligner 未过 Gate**：使用 detector edge anchors、结构 bbox 位置先验、Hungarian 一对一匹配、OCR 空间重叠与合并跨度约束；拒绝越界和严重重叠，不做 clip。9/9 最终仍失败。它把 `17100`、`12400` 移到更接近真实行/金额区域，但列号或 colspan 仍错；无法修复上游 HTML 缺槽。
5. **merged cell 没有可靠改善**：按完整拓扑、几何跨度和内部隔线严格核验，Paddle raw 8/63 个、Aligner 0/63 个、TATR 0/25 个达到本轮的 merged 验证条件。TATR 识别的 merged 数本身偏少，不可把 0 个局部框错误解释为成功。
6. **关键金额无一满足完整验收**：`64500` 两处、`17100`、`12400`、`118000` 五个图像位置，三方案均 0/5 同时满足文字、逻辑行列、rowspan/colspan、cell 图像 bbox、PDF evidence bbox。`17100` 与 `12400` 的局部图像 bbox 在 Paddle/Aligner 接近正确，但逻辑跨度仍错。
7. **坐标链本身可靠，cell evidence 仍不可靠**：9 张 crop 的 836 个冻结 OCR bbox 经 crop→render page→PDF 逆变换，与上一轮 extraction 的原始 PDF bbox 最大差为 0.0 pt。关键金额的 cell 归属或 bbox 错，故五处 PDF cell evidence 均不能发布。
8. **TATR 仅有局部几何优势**：7/9 张通过本轮独立 bbox 几何 Gate，Paddle raw 为 0/9；但 TATR 9/9 张低估 detector 显示的最少行数，部分还低估列数，OCR 唯一归属 0/9。它没有解决完整表拓扑和金额 evidence。
9. **MinerU wired 路线**：当前 wired recognizer 为 `TSRUnet`，模型资源 `Table/unet.onnx`，以线分割/连通区域恢复 cell，再由 `TableRecover` 得到逻辑位置，最后用外部 OCR 填 HTML。接口可接受固定 OCR；默认对空 cell 调用内部 OCR-rec，若 `ocr_engine=None` 可避免这一步但会失去补字。本轮未安装或运行整个 MinerU pipeline，也未下载其 wired 权重，因此无 9-crop 质量结论。
10. **没有足够证据集成 app**：三个方案的最终 `STRUCTURE_OK` 均为 0/9，关键金额 0/5。没有 candidate 在完整质量上明显优于 baseline，因此未触发三合同完整 pipeline 定向复核；没有跑剩余 12 份 Golden。

## 冻结输入、运行环境与产物

- [输入 manifest（含上一轮来源路径、9 组 SHA256、OCR bbox、table bbox、render/PDF transform）](../../experiments/complex_wired_table_topology/input-manifest.json)；[原始 Paddle baseline](../../experiments/complex_wired_table_topology/baseline-paddle.json)；[Paddle 源阶段与计数](../../experiments/complex_wired_table_topology/stage-diagnostics.json)。上一轮输入 ledger 位于 `output/analysis/scanned-table-structure-poc-20260924/input-ledger.json`。
- [Paddle 9 次原始中间产物](../../experiments/complex_wired_table_topology/raw_paddle/)：`.npz` 保存结构模型和检测器的原始推理 tensor；同名 JSON 保存结构解码、detector predictor 输出、NMS、按结构数量补框/裁框、matcher 排序与 OCR index、HTML 生成及最终 bbox。PaddleX 3.2.0、`SLANeXt_wired`、`RT-DETR-L_wired_table_cell_det`，分类均为 wired；模型参数和上一轮相同，重放 HTML 与 bbox 9/9 精确一致。
- [TATR 原始 125 query 检测](../../experiments/complex_wired_table_topology/raw_tatr/)与 [统一 TableArtifact](../../experiments/complex_wired_table_topology/artifacts/)。TATR 使用 `microsoft/table-transformer-structure-recognition` 固定 checkpoint `f4d4bdc...`，权重 SHA256 `f581da65a586591124cac9545b09b93e4e8bf28ce3a8357af6ac15ce94fce16b`；在 MPS 上跑同一批 crop，只复用冻结 OCR，没有新 OCR 或正式依赖变更。模型的 [官方标签](https://huggingface.co/microsoft/table-transformer-structure-recognition/blob/main/config.json) 包括 row、column、column header、projected row header、spanning cell。
- TableArtifact 对三个方案统一存 `page_id/table_bbox/rows/cols/cells/html/structure_status/failure_reason`；cell 存逻辑坐标、文字、crop/page/PDF bbox、topology/geometry source 与 confidence、OCR token evidence。没有向 Table Compare 输入模型私有结构。

## Paddle 失败层级与根因

下表 `S/D/N/R/F` 分别为结构模型四边形数、检测器 predictor 框数、NMS 后框数、按结构数量重处理框数、最终框数；HTML 逻辑 cell 数与 S 相同。`holes` 为 HTML 逻辑网格空槽。

| Crop | S/D/N/R/F | holes | 首个失败阶段 | 分层解释 |
| --- | --- | ---: | --- | --- |
| `e03a2a194fd2-p3:v3:3` | 106/95/95/95/95 | 13 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框不足 |
| `e03a2a194fd2-p4:v3:0` | 43/49/49/43/43 | 8 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框有重处理/错序 |
| `3d706111bdf7-p3:v3:3` | 108/103/103/103/103 | 17 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框不足 |
| `3d706111bdf7-p4:v3:0` | 20/19/19/19/19 | 0 | `CELL_DETECTOR_POSTPROCESS` | HTML 无空槽；检测框少 1 个 |
| `7d76c9387a1c-p3:v3:3` | 112/103/103/103/103 | 15 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框不足 |
| `7d76c9387a1c-p4:v3:0` | 19/19/19/19/19 | 4 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框有重处理/错序 |
| `d8301f12f62e-p4:v3:1` | 117/127/125/117/117 | 2 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框有重处理/错序 |
| `d8301f12f62e-p5:v3:0` | 98/95/95/95/95 | 1 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框不足 |
| `d8301f12f62e-p6:v3:0` | 124/131/129/124/124 | 7 | `STRUCTURE_DECODE_HTML` | HTML 网格缺槽；检测框有重处理/错序 |

PaddleX 3.2.0 的 `pipeline_v2.py` 在 wired 分支分别调用结构模型和 cell detector，以 `len(table_structure_pred['bbox'])` 为重处理目标。`cells_det_results_reprocessing()` 在检测框多时先取高分框；检测框少且 OCR 不被判为漏框时直接返回原框数。`table_recognition_post_processing_v2.py` 再将 detector 框转坐标并裁剪到图像、排序、把 OCR index 填入结构 token 生成 HTML。该裁剪是 Paddle 内部行为，本实验的 geometry Gate 没有再 clip。

在数量相等的片段，结构 bbox 与最终 detector bbox 的 index 对应平均 IoU 仅 0.055–0.070；最优一对一空间匹配提高到 0.128–0.191，仍低。这说明纯按序号对应不成立，也说明不能把所有问题解释成一次排列错误：结构位置、合并跨度和检测框本身还不一致。`KAILI p4` 的 `118000` 原始 detector 有覆盖整格的框，但 Paddle 重处理后留下 OCR 派生的窄框 `[701,429,775,497]`，同时把 `29500` 混到同一逻辑 cell。

## Aligner 与 merged-cell 检查

Aligner 先从独立 detector 的 top/bottom、left/right 重复边界聚类出 row/column bands，再将 HTML `(row,col,rowspan,colspan)` 与结构模型四边形作为位置先验，对检测框做全局 Hungarian 匹配。匹配分数只含 IoU、归一化中心距离及预期 band 覆盖；OCR 只用于空间重叠与阅读顺序，未以金额文本决定列。未匹配时曾尝试结构四边形回退；对越界或与已接受 cell 严重重叠的 bbox 一律置为 `UNRESOLVED`，记录原框与拒绝原因，不做 clip。内部隔线扫描参与合并单元格校验。

B 的严重重叠由 A 的 16 对降至 0 对，是拒绝冲突框后的保守输出，**不是**恢复了缺失 cell：仍有 bbox 空缺、OCR 未归属与 HTML 缺槽。严格 merged 校验需要整表拓扑、预期 band 和隔线同时合理；A/B/C 分别仅 8/63、0/63、0/25，未见改善。

## 关键金额：图像、逻辑 cell 与 PDF evidence

[逐金额结构化检查](../../experiments/complex_wired_table_topology/key-amount-inspection.json)保存源 OCR token、人工从固定 crop 标出的期望 cell（0-based 行列）、每个方案的文字、行列/跨度、crop/page/PDF bbox 与 IoU。对应彩色截图中绿色为期望 cell，黄色为 OCR 行框，红/青/紫分别为 A/B/C。金额只用于此人工复核，没有进入对齐算法。

| 图像位置 / 金额 | 期望 row,col,span | Paddle raw | Aligner | TATR | 截图 |
| --- | --- | --- | --- | --- | --- |
| `d8301f12f62e-p4:v3:1` / **64500** | r7 c6 1×2 | r8 c2 1×1，IoU 0.38，FAIL | 未可靠归属 | r7 c6 1×1，IoU 0.26，FAIL | [PNG](../../experiments/complex_wired_table_topology/inspection/d8301f12f62e-p4-v3-1-64500.png) |
| `d8301f12f62e-p6:v3:0` / **64500** | r5 c6 1×2 | r4 c7 1×1，IoU 0.39，FAIL | r5 c0 1×9，IoU 0.17，FAIL | r5 c7 1×1，IoU 0.47，FAIL | [PNG](../../experiments/complex_wired_table_topology/inspection/d8301f12f62e-p6-v3-0-64500.png) |
| `e03a2a194fd2-p3:v3:3` / **17100** | r8 c6 1×2 | r7 c8 1×1，IoU 0.86，FAIL | r8 c7 1×2，IoU 0.86，FAIL | r7 c6 1×3，IoU 0.31，FAIL | [PNG](../../experiments/complex_wired_table_topology/inspection/e03a2a194fd2-p3-v3-3-17100.png) |
| `e03a2a194fd2-p4:v3:0` / **12400** | r5 c6 1×2 | r6 c7 1×1，IoU 0.94，FAIL | r5 c6 1×1，IoU 0.94，FAIL | 未可靠归属 | [PNG](../../experiments/complex_wired_table_topology/inspection/e03a2a194fd2-p4-v3-0-12400.png) |
| `e03a2a194fd2-p4:v3:0` / **118000** | r7 c6 1×2 | r7 c0 1×6，IoU 0.25，FAIL | 未可靠归属 | r6 c6 1×3，IoU 0.24，FAIL | [PNG](../../experiments/complex_wired_table_topology/inspection/e03a2a194fd2-p4-v3-0-118000.png) |

五处 OCR 数字本身均在冻结 PP-OCRv6 token 中正确；问题在 cell 文字混入、行列/跨度及几何。PDF 逆仿射对 836/836 OCR bbox 精确回到上一轮 extraction 坐标（最大误差 0.0 pt），但 cell bbox 与逻辑归属错误时，PDF 坐标的数值正确也不能成为可靠 Evidence。

## 四项 Gate 与 failure matrix

`TOPOLOGY_OK` 要求 HTML 占位无缺槽/重叠、merged span 有几何依据，且行列数不低于独立 cell detector 在同一扫描线上的最大互不重叠框数；`GEOMETRY_OK` 要求所有 cell bbox 存在且在 crop 内、无超过较小 bbox 面积 25% 的严重重叠、普通行列与 band 一致、merged bbox 覆盖预期 band；`OCR_ASSIGNMENT_OK` 要求 836 条 OCR token 在各表内唯一归属；`EVIDENCE_OK` 要求 PDF 坐标链与全部 cell 几何可靠。四项全过才置 `STRUCTURE_OK`。对 TATR 另用 Paddle **原始 cell detector** 仅作可见行列下界，不借用其 HTML；因此 TATR 自己拼出完整网格不能自动过拓扑 Gate。

| Crop | Candidate | Topology | Geometry | OCR Assignment | Evidence | Final | FIRST_FAILURE_STAGE | failure_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `e03a2a194fd2-p3:v3:3` | A | FAIL | FAIL | PASS | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:13`; `DETECTOR_BOX_MISSING:11`; `BBOX_MISSING:11`; `ROW_COLUMN_BAND_MISMATCH:36` |
| `e03a2a194fd2-p3:v3:3` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:13`; `BBOX_MISSING:28`; `ROW_COLUMN_BAND_MISMATCH:18`; `OCR_UNASSIGNED:18`; `MERGED_CELL_GEOMETRY:1` |
| `e03a2a194fd2-p3:v3:3` | C | FAIL | FAIL | FAIL | FAIL | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:12:14`; `BBOX_OVERLAP:9`; `OCR_UNASSIGNED:18` |
| `e03a2a194fd2-p4:v3:0` | A | FAIL | FAIL | PASS | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:8`; `ROW_COLUMN_BAND_MISMATCH:29` |
| `e03a2a194fd2-p4:v3:0` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:8`; `BBOX_MISSING:3`; `ROW_COLUMN_BAND_MISMATCH:5`; `OCR_UNASSIGNED:2`; `MERGED_CELL_GEOMETRY:4` |
| `e03a2a194fd2-p4:v3:0` | C | FAIL | PASS | FAIL | PASS | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:7:9`; `OCR_MULTI_ASSIGNMENT:3`; `OCR_UNASSIGNED:5` |
| `3d706111bdf7-p3:v3:3` | A | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:17`; `DETECTOR_BOX_MISSING:5`; `BBOX_MISSING:5`; `BBOX_OVERLAP:2`; `ROW_COLUMN_BAND_MISMATCH:39`; `OCR_UNASSIGNED:1` |
| `3d706111bdf7-p3:v3:3` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:17`; `BBOX_MISSING:31`; `ROW_COLUMN_BAND_MISMATCH:16`; `OCR_UNASSIGNED:23`; `MERGED_CELL_GEOMETRY:3` |
| `3d706111bdf7-p3:v3:3` | C | FAIL | PASS | FAIL | PASS | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:14:16`; `VISUAL_COLUMN_UNDERCOUNT:8:9`; `OCR_MULTI_ASSIGNMENT:1`; `OCR_UNASSIGNED:9` |
| `3d706111bdf7-p4:v3:0` | A | FAIL | FAIL | PASS | FAIL | **FAIL** | `CELL_DETECTOR_POSTPROCESS` | `DETECTOR_BOX_MISSING:1`; `BBOX_MISSING:1`; `ROW_COLUMN_BAND_MISMATCH:4`; `MERGED_CELL_GEOMETRY:1` |
| `3d706111bdf7-p4:v3:0` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `TOPOLOGY_GEOMETRY_ALIGNMENT` | `BBOX_MISSING:11`; `ROW_COLUMN_BAND_MISMATCH:15`; `OCR_MULTI_ASSIGNMENT:1`; `OCR_UNASSIGNED:8`; `MERGED_CELL_GEOMETRY:9` |
| `3d706111bdf7-p4:v3:0` | C | FAIL | PASS | FAIL | PASS | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:4:5`; `VISUAL_COLUMN_UNDERCOUNT:2:9`; `OCR_UNASSIGNED:3` |
| `7d76c9387a1c-p3:v3:3` | A | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:15`; `DETECTOR_BOX_MISSING:9`; `BBOX_MISSING:9`; `BBOX_OVERLAP:2`; `ROW_COLUMN_BAND_MISMATCH:38`; `OCR_UNASSIGNED:1` |
| `7d76c9387a1c-p3:v3:3` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:15`; `BBOX_MISSING:33`; `ROW_COLUMN_BAND_MISMATCH:22`; `OCR_UNASSIGNED:22`; `MERGED_CELL_GEOMETRY:2` |
| `7d76c9387a1c-p3:v3:3` | C | FAIL | PASS | FAIL | PASS | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:14:16`; `VISUAL_COLUMN_UNDERCOUNT:8:9`; `OCR_MULTI_ASSIGNMENT:1`; `OCR_UNASSIGNED:10` |
| `7d76c9387a1c-p4:v3:0` | A | FAIL | FAIL | PASS | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:4`; `ROW_COLUMN_BAND_MISMATCH:3` |
| `7d76c9387a1c-p4:v3:0` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:4`; `BBOX_MISSING:7`; `ROW_COLUMN_BAND_MISMATCH:10`; `OCR_UNASSIGNED:3`; `MERGED_CELL_GEOMETRY:6` |
| `7d76c9387a1c-p4:v3:0` | C | FAIL | PASS | FAIL | PASS | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:4:5`; `VISUAL_COLUMN_UNDERCOUNT:2:9`; `OCR_UNASSIGNED:4` |
| `d8301f12f62e-p4:v3:1` | A | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:2`; `VISUAL_ROW_UNDERCOUNT:16:17`; `BBOX_OVERLAP:1`; `ROW_COLUMN_BAND_MISMATCH:71`; `OCR_UNASSIGNED:1` |
| `d8301f12f62e-p4:v3:1` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:2`; `VISUAL_ROW_UNDERCOUNT:16:17`; `BBOX_MISSING:14`; `ROW_COLUMN_BAND_MISMATCH:19`; `OCR_UNASSIGNED:14`; `MERGED_CELL_GEOMETRY:3` |
| `d8301f12f62e-p4:v3:1` | C | FAIL | PASS | FAIL | PASS | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:16:17`; `VISUAL_COLUMN_UNDERCOUNT:8:9`; `OCR_UNASSIGNED:2` |
| `d8301f12f62e-p5:v3:0` | A | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:1`; `DETECTOR_BOX_MISSING:3`; `BBOX_MISSING:3`; `BBOX_OVERLAP:2`; `ROW_COLUMN_BAND_MISMATCH:54`; `OCR_UNASSIGNED:1` |
| `d8301f12f62e-p5:v3:0` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:1`; `BBOX_MISSING:8`; `ROW_COLUMN_BAND_MISMATCH:10`; `OCR_UNASSIGNED:5`; `MERGED_CELL_GEOMETRY:3`; `MERGED_CELL_INTERNAL_DIVIDER:2` |
| `d8301f12f62e-p5:v3:0` | C | FAIL | PASS | FAIL | PASS | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:14:15`; `OCR_MULTI_ASSIGNMENT:1`; `OCR_UNASSIGNED:3` |
| `d8301f12f62e-p6:v3:0` | A | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:7`; `BBOX_OVERLAP:9`; `ROW_COLUMN_BAND_MISMATCH:16`; `OCR_UNASSIGNED:9` |
| `d8301f12f62e-p6:v3:0` | B | FAIL | FAIL | FAIL | FAIL | **FAIL** | `STRUCTURE_DECODE_HTML` | `INCOMPLETE_GRID:7`; `BBOX_MISSING:27`; `ROW_COLUMN_BAND_MISMATCH:55`; `OCR_UNASSIGNED:19`; `MERGED_CELL_GEOMETRY:2` |
| `d8301f12f62e-p6:v3:0` | C | FAIL | FAIL | FAIL | FAIL | **FAIL** | `TATR_ROW_COLUMN_DETECTION` | `VISUAL_ROW_UNDERCOUNT:17:18`; `BBOX_OVERLAP:31`; `OCR_MULTI_ASSIGNMENT:1`; `OCR_UNASSIGNED:23` |

A = Paddle raw；B = Paddle + TopologyGeometryAligner；C = TATR + 冻结 PP-OCRv6。完整机器可读矩阵见 [failure-matrix.json](../../experiments/complex_wired_table_topology/failure-matrix.json)。

## TATR、MinerU 与局部性能

TATR 使用 row/column box 交集形成普通 cell，以高置信 spanning-cell box 覆盖连续网格，再用同一 OCR bbox 做唯一归属；原始 125 query 的类别、分数和 bbox 均保存。7/9 张局部几何通过，但 9/9 张的 row 检出数低于独立 detector 可见下界，部分连第一行的 9 列都压成 2 列。两个复杂片段仍有严重重叠；关键金额 0/5 全项通过。因此 `TATR_GEOMETRY_ADVANTAGE` 仅指局部框规整程度，不能推导可集成。

[MinerU 当前 wired 实现](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/model/table/rec/unet_table/main.py) 的 `WiredTableRecognition.__call__(img, ocr_result=...)` 接收外部 OCR；[TSRUnet](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/model/table/rec/unet_table/table_structure_unet.py) 分割横/竖线并恢复多边形，[模型资源注册](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/model/registry.py) 指向 `Table/unet.onnx`。方向归一化、wireless 先行后 wired/低置信回退及 HTML 交接由 [table pipeline](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/backend/analysis/pdf/tables.py) 实现。`ocr_result` 已提供时不必做全表 OCR，但 `fill_blank_rec` 默认对空格再做识别；本轮没有其权重，未做质量 probe，也未运行完整 MinerU。可借鉴的是“线分割几何→逻辑恢复→OCR handoff”的边界，不是直接替换生产模型。

局部性能：Paddle CPU 初始化 7.98s、单表 median 2.05s；TATR MPS 初始化 0.52s、单表 median 0.15s（首次推理 5.63s）。未测峰值 VRAM；不同设备下这些数字不用于选择模型。

## 业务边界与最终状态

本轮只读复用上一轮三合同 extraction、PDF、crop 与 OCR，并在实验目录运行 9 个 table crop。由于 A/B/C 均为 0/9 `STRUCTURE_OK`、关键金额均 0/5，未满足“candidate 在失败 crop 上明显优于 baseline”的条件，因此没有再跑三份完整合同。上一轮 Compare 语义保护 `STRUCTURED_TABLE` 对 `UNSTRUCTURED_TABLE_PRESENT` 输出 `TABLE_STRUCTURE_UNRESOLVED` / `NEEDS_REVIEW` 的逻辑仍在 `backend/app/services/table_compare/facade.py`，本轮未改动；五张 false DELETE 没有因本实验重新生成，真实日期 diff 也未重新验证。本轮没有修改 Golden expectation、Clause/Matching/Diff/Evidence 非表格逻辑、正式 app/requirements/config，亦未部署。

```text
WIRED_TABLE_POC = INSUFFICIENT
BEST_APPROACH = NONE
TOPOLOGY_GATE = A:0/9, B:0/9, C:0/9
GEOMETRY_GATE = A:0/9, B:0/9, C:7/9
EVIDENCE_GATE = A:0/9, B:0/9, C:7/9 (local coordinate/geometry only)
TATR_GEOMETRY_ADVANTAGE = true (local bbox sanity only)
READY_FOR_APP_INTEGRATION = false
FULL_GOLDEN_NOT_RUN = true
PRODUCTION_NOT_MODIFIED = true
```

剩余阻塞：结构模型产生缺槽或漏行；detector 与结构模型数量/跨度不一致；裁框/补框会损伤正确的原始几何；合并金额格的 OCR 唯一归属和 PDF cell evidence 尚不能同时通过。报告生成后本轮停止。
