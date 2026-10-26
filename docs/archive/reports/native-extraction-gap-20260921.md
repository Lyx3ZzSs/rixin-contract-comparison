# Native Extraction Gap — 源码与既有产物核查

日期：2026-09-21。范围：Extraction 架构分析 Task 3，只读既有产物与源码；未运行 compare、远端识别、A–E benchmark，未修改生产或已有实验。以下统计是 JSON/PDF 的离线读取。

## 结论

已有 B 实验不能证明 Native Fast Path 不可用。它证明当前 `PyMuPDFExtractor` 交付的 artifact 缺少下游使用的 layout semantics，而且现有 splitter 对三位数字编号的抑制使标题缺失的后果放大。

两侧各 40 页中，baseline 与 native 按既有 block 顺序拼接、去空白后的文字 **80/80 页逐字符完全一致**；native 与输入 PDF `get_text()` 也 **80/80 页完全一致**。不存在本样本中文字识别缺失的证据。两种抽取的换行、block 粒度、标题类型和 bbox 并不等价。

超长 native 条款是编号 **99**，从第 13 页一直吞到第 40 页，50,191 字符。编号 100–320 在现有规则中被判为 non-clause numeric；baseline 也不能正确逐条切出这些编号，只是靠部分 `paragraph_title` 类型的 Chapter 标题再次建立边界，最长仍达 9,049 字符。114/113 是 baseline 行为，不是该合成 PDF 的条款真值（输入描述为每页 8 条，共 320 个编号条款）。

## 证据范围与可复现性

现存目录：

- [baseline](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/perf-phase2-20260921/native/fast/B-1-baseline/)
- [candidate](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/perf-phase2-20260921/native/fast/B-1-candidate/)
- [early-rejection.json](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/perf-phase2-20260921/native/fast/B-1-candidate/early-rejection.json)：baseline 114/113，candidate 100/100；最大条款 9,049 → 50,191；`pipeline_completed=false`，候选在昂贵 matching 之前停止。
- [cases.json](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/e2e-20260921/cases.json)：B 是 synthetic_text，40 页、每页 8 个编号条款、每页一处金额改动。

本次核验输入 SHA-256 与登记值一致：

```text
B-original.pdf 636e8000c2a7dcd71847de480b245873ab39eb3d6d74acb1b4bf9e326a66a599
B-compare.pdf  47dd40e090e20f7edf3b6a948add6c0c5be093a509f4040645981aaa2c754999
```

当前 `clause_splitter.py`、`clause_numbering.py`、`clause_heading.py`、`clause_paragraphs.py`、`extractors/pymupdf.py`、`native_heading_repair.py` 的 SHA-256 均与 baseline 保存的 `code-hashes.json` 一致。以下源码解释适用于该次已保存实验。

抽取 JSON 保存于 ExtractionStage 执行结束后，而非原始 `PyMuPDF` 调用后，包含该阶段已经做过的修复。见 [run_pipeline.py:57](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/experiments/performance_phase2/run_pipeline.py:57)。candidate 的 `native_heading_repair.json` 两侧均为 `repaired_count=0, decisions=[], warnings=[]`。

## 逐字段比较

统计以每侧为单位；原文/比对文只有个别标题分类数不同。

| 字段 | Baseline | Native | 对架构的含义 |
|---|---|---|---|
| `text` | 每侧 70,710 字符，去空白 61,230 | 每侧 71,670 字符，去空白 61,230 | 960 字符差全由 native 块内换行等空白差异解释；逐页去空白序列完全一致 |
| `bbox` | 1,360 个 OCR line bbox；另有 layout bbox | 400 个 PDF 原生 block bbox | 都在 PDF 页坐标；native block 可含标题+正文，不能把整个 block 强制赋同一种语义 |
| `page` | 40 页，595×842，1-based `page_no` | 相同 | 本样本不存在页号或页面尺寸错配 |
| `block_type` | original：960 text、335 paragraph_title、25 doc_title、40 number；compare：960/334/26/40 | 400 text | 两侧 baseline 都有 360 个 title 类型；native 全部丢失 |
| `title` | 类型提供 title 语义，`flow_role=heading` 等随之产生 | 没有独立标题语义 | `TextBlock` 本身无 `title` 字符串字段；下游 Clause 的 title 由文字和类型推断 |
| `paragraph` | 960 个正文 text line，后续 ParagraphBuilder 合并 | 320 个 4 行 block，每块为一条标题+3行正文；另 40 个 Chapter、40 页码 | 既有 baseline 也没有显式 paragraph tree；差别是语义边界+不同粒度 |
| `table` | 0 table / HTML / cell artifact | 相同 | B 没有表格，不能把 table 缺失认定为本次失败的已证实根因；原生抽取器确实没有表格结构生成 |
| `reading_order` | 1,360/1,360 非空 | 0/400 | native 走 geometry fallback；此单栏样本原始文本顺序正确，但缺少可传递的显式顺序 |
| `layout_order / layout_bbox` | 1,360/1,360 非空 | 0/400 | 下游失去 layout 分组、排序与几何参照 |
| `block_role / flow_role` | 1,360/1,360 非空 | 0/400 | margin/noise/title/body 等语义契约不完整 |
| font info | `TextBlock` 未承载字体样式 | 原始 PDF rawdict 有 font/size/flags，但 extractor 丢弃 | 字体是可用却未传播的 native 信息，不是 native PDF 提供不了 |
| line hierarchy | line 粒度 blocks；没有统一 line/span tree | rawdict block→line→span→char 被压成 text + char_boxes；320 个 multiline blocks | native 需要保留行/跨度层次，再按 layout region 分割与归属 |
| `char_boxes` | 每侧 70,710 个；每页+坐标去重后 21,200 个框 | 每侧 70,710 个；70,710 个不同框 | baseline 中词内多字符共用框；native 在本样本提供更精细字符框 |
| char index integrity | 每侧 0 个非法/字符不匹配索引 | 相同 | native 没有字符位置映射失效；换行没有独立 glyph box 符合源数据 |
| `source_evidence` | OCR + structure 双来源 | 空 | Challenger 需要明确 native/layout/fallback 来源与归属，不应冒用 OCR 来源 |

所有 bbox 的 `normalized` 字段在本样本均为空；这不等于 bbox 未归一化到统一坐标系。OCR client 通过 `_clamp_bbox` 将像素/相对坐标转换为 PDF page width/height，native 直接使用 PDF 坐标。见 [ppocrv5.py:879](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/ppocrv5.py:879)。B 的标题示例：baseline `(49.5,43,300.5,57)`；native `(50,39.9500,301.3140,59.1860)`。这是 detection 外框与字体 glyph 外框差异，未发现 DPI 倍数错位。

字体与行信息被丢弃的具体位置：[pymupdf.py:33](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/pymupdf.py:33) 取 rawdict，[pymupdf.py:44](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/pymupdf.py:44) 构造固定 `block_type="text"`，[pymupdf.py:76](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/pymupdf.py:76) 把 line/span flatten。统一模型 [models.py:242](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/models.py:242) 没有 font/line/span 字段。

项目已有另一段 native reader 保存 `NativeLineRecord.span_styles`（font/size/flags）与逐行 char boxes，可作为设计依据：[native_heading_repair.py:755](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/native_heading_repair.py:755)。它目前只服务局部修复，没有进入统一 artifact。

## ClauseSplitter 到底依赖什么

调用顺序是 `collect units → order → trim cover → ParagraphBuilder → detect heading / clause boundary → boundary repair`。见 [clause_splitter.py:100](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_splitter.py:100)。这里没有“必须调用某一 OCR 模型”的直接依赖，依赖的是 extraction artifact 的文字、语义类型、几何、顺序与来源契约。

1. `_collect_units` 使用 `enter_clause_compare`、`flow_role`、`block_role`、`block_type` 过滤页眉/噪声/表格等，并把 layout/read order 带入 ClauseUnit。拆出多行 piece 时仍继承整个 block bbox，未从对应 char_boxes 重建 line bbox。见 [clause_splitter.py:117](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_splitter.py:117)。
2. `_order_units` 优先完整 reading_order，其次可信 layout_order，否则 geometry。native 记录的 segmentation_reason 明确包含 `order:geometry`。见 [clause_splitter.py:364](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_splitter.py:364)。本 B 样本没有证据证明 geometry 导致文字错序。
3. `ParagraphBuilder` 将 `paragraph_title/doc_title/title` 视为强边界，防止标题与前正文续接；表格/页眉等也有边界。类型全变 text 后，这层保护消失。见 [clause_paragraphs.py:17](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_paragraphs.py:17)、[clause_paragraphs.py:68](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_paragraphs.py:68)。
4. `ClauseHeadingDetector` 的无编号普通正文标题必须是 `paragraph_title`（附件特例还接受其它 title 类型）；它不是靠相同文字自行恢复标题。编号标题还会获得 type bonus/strong title signal。见 [clause_heading.py:63](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_heading.py:63)、[clause_heading.py:119](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_heading.py:119)、[clause_heading.py:202](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_heading.py:202)。
5. 标题候选分数达阈值才启动新 ClauseItem，否则 append 到前项。见 [clause_splitter.py:614](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_splitter.py:614)。

### 50,191 字符的完整因果链

```text
100. Service obligation 100
  ↓ parse_line 得到 canonical_number="100"
is_non_clause_numeric 匹配三位及以上整数 → True
  ↓ _is_quantity_or_amount_marker → True
ClauseHeadingDetector.candidate → None
  ↓ 无法建立编号条款边界（baseline/native 都发生）
native 的后续 Chapter 14..40 也全部 block_type=text
  ↓ 无法成为 unnumbered section title
  ↓ 原正文继续 append
第99条吞入第100..320条及后续 Chapter，延伸到第40页
```

源码：[clause_numbering.py:136](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_numbering.py:136)、[clause_splitter.py:1122](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_splitter.py:1122)、[clause_heading.py:97](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/clause_heading.py:97)。

baseline 的最大项是 `OC106/NC106`，从第 21 页 Chapter 21 开始到第 25 页；native 最大项是 `OC100/NC100`、编号 99、第 13–40 页。对应 [baseline clauses_original](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/perf-phase2-20260921/native/fast/B-1-baseline/runtime/tasks/phase2-B/diagnostics/debug/clauses_original.json) 与 [candidate clauses_original](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/perf-phase2-20260921/native/fast/B-1-candidate/runtime/tasks/phase2-B/diagnostics/debug/clauses_original.json)。

360 个 title 类型都恢复，也不意味着当前 splitter 自动得到 320 个编号条款：三位编号在 title bonus 之前就被拒绝。这一事实应作为解释和未来验收设计约束；本次不修改规则、不扩展历史失败治理。

NativeFastPathEvaluator 的 gate 检查字符数量、char box coverage、乱码、图像、表格、签署特征、多栏等，但没有检查 artifact 是否满足 title/paragraph/read-order 契约。见 [native_fast_path.py:111](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/native_fast_path.py:111)。所以“文字质量 gate 通过”不能代替“下游语义兼容通过”。

## 项目已有可移植的融合设计

当前 hybrid 已经是“两种来源融合”，其耦合点不是识别网络本身：

```text
OCR text line + char_boxes ──────────────┐
                                       ├─ spatial match → attach layout semantics
Structure block + bbox/type/order ──────┘       ↓
                                     consolidate table/text
                                               ↓
                                    assign_page_reading_order
                                               ↓
                                        Document / Page
```

[ppstructure_ocr_hybrid.py:273](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/ppstructure_ocr_hybrid.py:273) 调用 merge，`_attach_structure` 保留文字对象并注入 type/layout bbox/order/roles/provenance：[ppstructure_ocr_hybrid.py:384](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/ppstructure_ocr_hybrid.py:384)。空间匹配使用 coverage、IoU、center；v3 模糊匹配还可使用 structure text similarity：[ppstructure_ocr_hybrid.py:454](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/ppstructure_ocr_hybrid.py:454)。

因此可以研究把左边换成 native line/span，而右边换成纯 Layout Detection 输出。但不能直接把当前 400 个粗 native block 放进去：同一个 native block 中标题行与正文行属于不同 semantic region，必须先保留 line/span hierarchy，按 layout 边界拆分与归属。Layout-only 不产 structure text，当前 text similarity fallback、structure text replacement 也不能照抄。表格 HTML/cells 需要明确独立来源。

## 最小验证设计（本次未执行）

第一步应是离线 **oracle layout replay**：用 B 已保存 structure region 的 bbox/type/order，配合同一 PDF native rawdict line/span；只拿 layout 字段，禁止把 structure text 填入 canonical text。证明可恢复 title/paragraph/order 契约与文字完整性。保留数据融合前后的 line/span 来源和 bbox 映射；不要复用粗 native block 的统一 title 标签。

选择少量已有页即可：第 1 页（标题+1–8 条）、第 13–14 页（99→100 与跨页边界）；另选已有真实合同的一页表格，单独验证 table region、行列/cells 的需求。正文非空白文本逐字符守恒、标题 bbox/type 覆盖、阅读顺序、字符框索引是直接验收项。ClauseSplitter 的输出作为兼容观察，不把 baseline 114/113 当金标准；100+ 抑制须单独标注为当前规则行为。

这只能证明“native + 已有 layout 语义可以融合”。第二步才需要只对相同少量页面运行真正 layout-only，确认其 label taxonomy 与 bbox/order 可提供上一步所需字段；表格结构需独立 Table Recognition 或 native cell reconstruction。不能将 oracle replay 宣称为 layout-only 模型效果已验证。

架构结论：本次缺口的优先顺序是 **layout semantics/title classification → native line/span 粒度与语义归属 → 显式 reading order/provenance**。表格结构是适用其它合同的必要能力，bbox 的坐标转换是融合前置条件，但本 B 实验没有证据把二者认定为已发生的主故障。保留 native 文字、字体与 glyph boxes，再补独立布局语义，具有明确的设计依据；是否达到生产兼容需要上述小型验证。
