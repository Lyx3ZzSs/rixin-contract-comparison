| Case | Source | Root Cause | Fix | Regression Test |
|---|---|---|---|---|
| E001 | native_pdf | `TEXT_PROVENANCE_MISMATCH`：review 显示整段，证据只选子串 | 绑定 displayed_text 到原 canonical range；上下文单列；保留 native 原框 | fresh PyMuPDF chars + bbox 区域提取 + display binding + round-trip |
| E003 | native_pdf | `TEXT_PROVENANCE_MISMATCH`：review 显示整段，证据只选子串 | 绑定 displayed_text 到原 canonical range；上下文单列；保留 native 原框 | fresh PyMuPDF chars + bbox 区域提取 + display binding + round-trip |
| E012 | ocr_full_page | `TEXT_PROVENANCE_MISMATCH`：review 显示整段，证据只选子串；`TOKEN_BOX_MISMATCH`：原 OCR token 框已经是窄条，校正文本后仍沿用未验证的 glyph 范围 | 保留 raw tokens 与 3% → 3‰ 记录；使用原始行框，标注 LINE_LEVEL_APPROXIMATION | raw response token/line ID + crop → page → pixel + 显式 line 精度 |
| E015 | native_pdf | `TEXT_PROVENANCE_MISMATCH`：review 显示整段，证据只选子串 | 绑定 displayed_text 到原 canonical range；上下文单列；保留 native 原框 | fresh PyMuPDF chars + bbox 区域提取 + display binding + round-trip；真实目标页受控旋转 90° |
| E016 | native_pdf | `TEXT_PROVENANCE_MISMATCH`：review 显示整段，证据只选子串 | 绑定 displayed_text 到原 canonical range；上下文单列；保留 native 原框 | fresh PyMuPDF chars + bbox 区域提取 + display binding + round-trip；真实目标页受控旋转 180° |
| E017 | native_pdf | `TEXT_PROVENANCE_MISMATCH`：review 显示整段，证据只选子串 | 绑定 displayed_text 到原 canonical range；上下文单列；保留 native 原框 | fresh PyMuPDF chars + bbox 区域提取 + display binding + round-trip；真实目标页受控旋转 270° |

**Evidence Human QA = FAIL / PENDING_RECHECK**\
**READY_FOR_FULL_GOLDEN = false**

人工原始 FAIL 保留。修复后的 E001、E003、E012、E015、E016、E017 为 `PENDING_REVIEW`；其余 11 项为 `PENDING`。Codex 没有写入任何人工 PASS。只有独立人工重新确认全部 17 个 evidence cases 后，才允许更新 Release Gate。

[打开 before / after 人工复核包](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/index.html) · [复核 CSV](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/evidence-review.csv) · [6 例图像对照](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/before-after-contact-sheet.png) · [自动回归结果](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/regression.json)。

## 结论与实际范围

6 个 case 先冻结并输出完整 source trace，再改变 review 链路；未给任何 bbox 加经验 offset。E001/E003/E015/E016/E017 的 native char、artifact、原最终 evidence box 一致；首次偏差发生在 UI：`release_evidence_pack.py` 使用整段 `record["text"]` 作为可见原文，红框只绘制 `canonical_text_range` 对应字符，选中的 `selected_text` 没有作为明确的 evidence 文本展示。CSV 同样只导出整段 text。修复将文本与选区作为同一个契约输出，完整上下文折叠并明确不是红框范围。

E012 同时存在源几何质量问题。原 OCR 响应 word 31 = `3`、word 32 = `%`，给出的框分别只有 3 px、4 px 宽；原图可见它们只压住字形的一部分。坐标转换忠实保留了坏的源框。此前把 `%` 校正为 `‰` 时只保留等长 text index，却未验证新字形覆盖，review 又把 word 输出当成可用于 glyph 证据。当前不猜测或扩张 glyph 框，而退回返回的完整 OCR 行 polygon。**这修复了实验 evidence 的精度契约和可靠覆盖路径，不代表恢复了 OCR glyph 定位能力；行级近似是否可接受仍由人工判定。**

本轮没有运行 Full Golden、A–E benchmark、完整 Release Gate、提取流水线或任何新 OCR 请求。只重新渲染 6 个失败选区，其他 11 项复制原 page/overlay/detail 并自动检查。202 个生产文件 hash 与冻结锁一致；未修改生产 extractor、ClauseSplitter、前端生产应用或表格模型。前端修复限定为实验 review HTML 与 CSV。

## 可复现失败与假设验证

修复前执行 `archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python -m pytest experiments/extraction_challenger/test_evidence_coordinates.py -q`：**7 failed**（6 个展示文本绑定失败，1 个 OCR 精度声明失败）。随后逐层验证四类假设：展示字段取错成立；canonical/source ID 漂移未发现；OCR 原始 token 几何问题在 E012 成立；DPI/旋转/CropBox 变换错误未发现。修复前 trace 位于 `traces/E*-before.json`，全部原始材料及 SHA256 在 `before/`、`before-lock.json`。

## 逐例文本与来源

所有范围均为 Python Unicode code point、左闭右开，作用域明确为 block-local。另存 page canonical range；页面序列由完整 block 按当前 reading order 连接，换行分隔符不冒充源字符。页码为 PDF 第几页，从 1 开始；`pdf_page_index` 从 0 开始，与 PDF 内印刷页码区分。

### E001

```json
{
  "case_id": "E001",
  "document": "SHENNENG_NAIMAN",
  "page": 3,
  "displayed_text": "1、乙方提供的货物为原厂生产的全新产品，符合合同的技术规格所述的标准及相",
  "canonical_text_range": [
    0,
    28
  ],
  "canonical_page_range": [
    43,
    71
  ],
  "source": "native_pdf",
  "block_id": "SHENNENG_NAIMAN-p3:native:5",
  "source_text": "1、乙方提供的货物为原厂生产的全新产品，符合合同的技术规",
  "selected_text": "1、乙方提供的货物为原厂生产的全新产品，符合合同的技术规",
  "displayed_equals_source": false,
  "selected_equals_source": true,
  "bbox_region_extracted_text": "1、乙方提供的货物为原厂生产的全新产品，符合合同的技术规",
  "after_displayed_text": "1、乙方提供的货物为原厂生产的全新产品，符合合同的技术规",
  "evidence_precision": "glyph",
  "precision_warning": null
}
```

[完整逐字符数值 trace](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/E001.json) · [Debug overlay A/B/C/D](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E001/debug-overlay.png) · [Before](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/before/E001/case.json) · [After](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E001/case.json)。

### E003

```json
{
  "case_id": "E003",
  "document": "SHENNENG_NAIMAN",
  "page": 2,
  "displayed_text": "合同总价为人民币62100.00 元（大写：陆万贰仟壹佰元整），其中技术服务费人",
  "canonical_text_range": [
    8,
    16
  ],
  "canonical_page_range": [
    429,
    437
  ],
  "source": "native_pdf",
  "block_id": "SHENNENG_NAIMAN-p2:native:32",
  "source_text": "62100.00",
  "selected_text": "62100.00",
  "displayed_equals_source": false,
  "selected_equals_source": true,
  "bbox_region_extracted_text": "62100.00",
  "after_displayed_text": "62100.00",
  "evidence_precision": "glyph",
  "precision_warning": null
}
```

[完整逐字符数值 trace](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/E003.json) · [Debug overlay A/B/C/D](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E003/debug-overlay.png) · [Before](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/before/E003/case.json) · [After](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E003/case.json)。

### E012

```json
{
  "case_id": "E012",
  "document": "NANJING_DINGYI_XUZHOU_BANGYU",
  "page": 6,
  "displayed_text": "9.4 如甲方未按合同约定如期付款，每逾期一日，甲方应支付合同总额3‰的违约金",
  "canonical_text_range": [
    33,
    35
  ],
  "canonical_page_range": [
    391,
    393
  ],
  "source": "ocr_full_page",
  "block_id": "NANJING_DINGYI_XUZHOU_BANGYU-p6:ocr:0:line:12",
  "source_text": "3%",
  "selected_text": "3‰",
  "displayed_equals_source": false,
  "selected_equals_source": false,
  "bbox_region_extracted_text": null,
  "after_displayed_text": "3‰",
  "evidence_precision": "line",
  "precision_warning": "LINE_LEVEL_APPROXIMATION"
}
```

[完整逐字符数值 trace](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/E012.json) · [Debug overlay A/B/C/D](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E012/debug-overlay.png) · [Before](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/before/E012/case.json) · [After](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E012/case.json)。

### E015

```json
{
  "case_id": "E015",
  "document": "SHENNENG_NAIMAN",
  "page": 2,
  "displayed_text": "合同总价为人民币62100.00 元（大写：陆万贰仟壹佰元整），其中技术服务费人",
  "canonical_text_range": [
    8,
    16
  ],
  "canonical_page_range": [
    429,
    437
  ],
  "source": "native_pdf",
  "block_id": "SHENNENG_NAIMAN-p2:native:32",
  "source_text": "62100.00",
  "selected_text": "62100.00",
  "displayed_equals_source": false,
  "selected_equals_source": true,
  "bbox_region_extracted_text": "62100.00",
  "after_displayed_text": "62100.00",
  "evidence_precision": "glyph",
  "precision_warning": null
}
```

[完整逐字符数值 trace](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/E015.json) · [Debug overlay A/B/C/D](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E015/debug-overlay.png) · [Before](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/before/E015/case.json) · [After](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E015/case.json)。

### E016

```json
{
  "case_id": "E016",
  "document": "SHENNENG_NAIMAN",
  "page": 2,
  "displayed_text": "合同总价为人民币62100.00 元（大写：陆万贰仟壹佰元整），其中技术服务费人",
  "canonical_text_range": [
    8,
    16
  ],
  "canonical_page_range": [
    429,
    437
  ],
  "source": "native_pdf",
  "block_id": "SHENNENG_NAIMAN-p2:native:32",
  "source_text": "62100.00",
  "selected_text": "62100.00",
  "displayed_equals_source": false,
  "selected_equals_source": true,
  "bbox_region_extracted_text": "62100.00",
  "after_displayed_text": "62100.00",
  "evidence_precision": "glyph",
  "precision_warning": null
}
```

[完整逐字符数值 trace](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/E016.json) · [Debug overlay A/B/C/D](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E016/debug-overlay.png) · [Before](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/before/E016/case.json) · [After](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E016/case.json)。

### E017

```json
{
  "case_id": "E017",
  "document": "SHENNENG_NAIMAN",
  "page": 2,
  "displayed_text": "合同总价为人民币62100.00 元（大写：陆万贰仟壹佰元整），其中技术服务费人",
  "canonical_text_range": [
    8,
    16
  ],
  "canonical_page_range": [
    429,
    437
  ],
  "source": "native_pdf",
  "block_id": "SHENNENG_NAIMAN-p2:native:32",
  "source_text": "62100.00",
  "selected_text": "62100.00",
  "displayed_equals_source": false,
  "selected_equals_source": true,
  "bbox_region_extracted_text": "62100.00",
  "after_displayed_text": "62100.00",
  "evidence_precision": "glyph",
  "precision_warning": null
}
```

[完整逐字符数值 trace](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/E017.json) · [Debug overlay A/B/C/D](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E017/debug-overlay.png) · [Before](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/before/E017/case.json) · [After](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/after/E017/case.json)。

native 的 `bbox_region_extracted_text` 来自本轮重新打开 PDF 的 `page.get_textbox`，并非把 artifact 的 char 字段拼回去。5 个 native case 均等于 selected_text，但不等于旧页面显示的整段 text。E012 的 `source_text=3%` 是冻结原 OCR selection，不是对窄 bbox crop 重新识别的结果；本轮没有局部再 OCR，故独立 bbox 识别值为 null。原页视觉可见 `3‰`，已存在的文本修复记录继续保留，未把原始 `3%` 改写成“原 OCR 就是 3‰”。

## 数值链路与首次偏差

每份 trace 的 `chain[]` 保存：canonical block/page index → native location 或 request/line/word ID → raw source polygon → crop coordinates → full rendered page pixels → page points → artifact polygon/bbox → rendered pixel coordinates → 原 red overlay coordinates。`after` 保存最终 page polygon、render pixels、red overlay；after case 对重新生成的 detail 还保存 crop origin 和 detail overlay coordinates。native 的 crop 层为 null（无 crop），不是省略来源。

| Case | 原 page bbox (pt) | 原 red overlay envelope (px) | 修复后 red overlay envelope (px) | 原源框→artifact 最大误差 (pt) | PDF→pixel→PDF 最大误差 (pt) |
|---|---|---|---|---:|---:|
| E001 | `[96.0, 120.411674, 426.95636, 132.411682]` | `[192.0, 240.823349, 853.91272, 264.823364]` | `[192.0, 240.823349, 853.91272, 264.823364]` | 0.0 | 0.0 |
| E003 | `[195.240005, 564.171692, 243.240005, 576.171692]` | `[390.480011, 1128.343384, 486.480011, 1152.343384]` | `[390.480011, 1128.343384, 486.480011, 1152.343384]` | 0.0 | 0.0 |
| E012 | `[444.0, 328.0, 452.5, 338.0]` | `[888.0, 656.0, 905.0, 676.0]` | `[200.0, 656.0, 1001.0, 676.0]` | 0.0 | 0.0 |
| E015 | `[195.240005, 564.171692, 243.240005, 576.171692]` | `[531.456665, 390.480011, 555.456665, 486.480011]` | `[531.456665, 390.480011, 555.456665, 486.480011]` | 0.0 | 0.0 |
| E016 | `[195.240005, 564.171692, 243.240005, 576.171692]` | `[704.119965, 531.456665, 800.119965, 555.456665]` | `[704.119965, 531.456665, 800.119965, 555.456665]` | 0.0 | 0.0 |
| E017 | `[195.240005, 564.171692, 243.240005, 576.171692]` | `[1128.343384, 704.119965, 1152.343384, 800.119965]` | `[1128.343384, 704.119965, 1152.343384, 800.119965]` | 0.0 | 0.0 |

native 框没有改坐标，A/B/C 完全重合是期望结果。Debug 图用蓝色 A（raw source/native bbox，宽线）、绿色 B（artifact source token/char，次宽线）、红色 C（最终 evidence，细线）和紫色 D（有 OCR 时的 crop boundary）；宽度仅用于可见性，不改变记录的坐标。每图旁边标出修复前后 displayed text、原 source selection、最终框来源文本、canonical indices、page、source 与精度。

### E012 原始 OCR 链

```text
source = ocr_full_page
request = NANJING_DINGYI_XUZHOU_BANGYU-p6:ocr:0
line_index = 12
canonical [33,35) = 3‰
raw source selection = 3%
word 31: 3  crop bbox [888,656,891,676] -> page bbox [444,328,445.5,338]
word 32: %  crop bbox [901,656,905,676] -> page bbox [450.5,328,452.5,338]
crop offset = [0,0]; crop size = [1190,1684]
render_scale_x = 2; render_scale_y = 2; DPI = [144,144]
original line crop polygon = [[200,656],[1001,656],[1001,676],[200,676]]
final line page polygon = [[100,328],[500.5,328],[500.5,338],[100,338]]
final red pixel polygon = original line crop polygon
evidence_precision = line
precision_warning = LINE_LEVEL_APPROXIMATION
```

### MediaBox / CropBox / rotation / DPI

| Case | MediaBox | CropBox | page.rect（实际 review 旋转后） | rotation | render matrix → native_to_pixel |
|---|---|---|---|---:|---|
| E001 | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | 0 | `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` → `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` |
| E003 | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | 0 | `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` → `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` |
| E012 | `[0.0, 0.0, 595.0, 842.0]` | `[0.0, 0.0, 595.0, 842.0]` | `[0.0, 0.0, 595.0, 842.0]` | 0 | `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` → `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` |
| E015 | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 841.900024, 595.299988]` | 90 | `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` → `[0.0, 2.0, -2.0, 0.0, 1683.800049, 0.0]` |
| E016 | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | 180 | `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` → `[-2.0, 0.0, 0.0, -2.0, 1190.599976, 1683.800049]` |
| E017 | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 595.299988, 841.900024]` | `[0.0, 0.0, 841.900024, 595.299988]` | 270 | `[2.0, 0.0, 0.0, 2.0, 0.0, 0.0]` → `[0.0, -2.0, 2.0, 0.0, 0.0, 1190.599976]` |

真实失败案例的 round-trip 容差为 **1e-4 pt**，测得最大误差 **0 pt**；源 polygon 至 artifact 误差也为 **0 pt**。pixel bounds 以实际 pixmap width/height 检查，不把整数 PNG 宽高四舍五入误差误认为 DPI。PyMuPDF rawdict 使用未旋转页面坐标，不是 PDF 底部原点 user space；trace 另存 `pdf_user_space_to_native` 和 native char 的 PDF user-space bbox。没有额外翻转 y-axis。

补充 [8 组受控数值测试](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/controlled-transforms.json)：0/90/180/270° × (scale_x,scale_y)=(2,2)/(2,3)，MediaBox=[0,0,340,450]、CropBox=[20,30,300,400]。在页上画已知红色矩形，对照实际 raster 红像素位置，最大 pixel 误差 **0 px**（容差 1 px）；round-trip 容差 1e-4 pt。这些测试不替代上面 6 个真实 case trace。

## OCR region / mixed / table 的另外 11 项回归

[mixed 全页 canonical sequence](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/traces/mixed-page-canonical-sequence.json) 保存每个字符的 canonical index、source、source local index、word ID/native location 与 page coordinate。native 字符恰好使用一次，source IDs 唯一，所有非空白字符有来源；排序移动整个 block 及 provenance，没有只 reorder text。review 直接选择 artifact block，不使用 ClauseSplitter 的归一化/合并后字符串索引；本轮未改变 clause ownership。

E006/E013 是真正的 `ocr_region`，实际 request crop_x0=195、crop_y0=219、crop_width=850、crop_height=376。raw crop polygon 加一次 `[195,219]` 得整页像素，再用保存的 inverse matrix 得 page points，再正向转换回 review pixels。逐点误差为 0；反例测试注入重复 offset 会被拒绝。E007 邻接上下文继续使用蓝框；主 evidence 的 canonical/source 绑定没有被相邻 OCR block 替换。

E004 的底层文字来源是 native_pdf，E009/E014 是 ocr_full_page；table 是容器，而非替代这些原始 token 的来源。其 trace 的 `table_mapping` 保存 canonical table text、selected cell、token refs、cell polygon/bbox、page table polygon 及 HTML。E004 的 vector cell、E009 的 cell:7、E014 的 cell:4 均包含正确 token，唯一分配与几何中心检查通过。E009 的历史 raw structure topology 与 geometry topology 不同，但现有发布 HTML 来自同一 geometry graph 的 cell IDs，未按序 zip；E014 仍为 TABLE_STRUCTURE_UNRESOLVED，HTML=null，不能用 evidence 回归替代结构验收。

| Case | text provenance unchanged | page unchanged | coordinate round-trip valid | overlay bounds valid | Status |
|---|---|---|---|---|---|
| E002 | true | true | true | true | PENDING |
| E004 | true | true | true | true | PENDING |
| E005 | true | true | true | true | PENDING |
| E006 | true | true | true | true | PENDING |
| E007 | true | true | true | true | PENDING |
| E008 | true | true | true | true | PENDING |
| E009 | true | true | true | true | PENDING |
| E010 | true | true | true | true | PENDING |
| E011 | true | true | true | true | PENDING |
| E013 | true | true | true | true | PENDING |
| E014 | true | true | true | true | PENDING |

这 11 项的原 overlay、detail 文件 SHA256 不变；page 图像像素不变。共享展示修复同样作用于这些 case 的 HTML/CSV，但 canonical text、选区与原来源不变。原先未报告失败不等于自动通过。所有 OCR word 输出都明确使用 `WORD_LEVEL_APPROXIMATION`，即使 token 只有一个字符也不声称 glyph 精度；相同 polygon 只有在 request+line+word ID 相同才合并绘制，不能跨文字复用框。

## 代码、回归与状态保护

实验修改：`evidence_coordinates.py`（逐层 trace、绑定与精度契约、共享 review UI）、`trace_evidence_coordinates.py`（不可变修复前快照）、`recheck_evidence_coordinates.py`（仅 6 例渲染及 11 例回归）、`release_evidence_pack.py`（绑定展示字段并禁止覆盖已失败原包）、`release_verify.py`（已知人工 FAIL 时在任何 Gate 执行前拒绝运行）。历史 pack 的六项状态已按用户报告记录 FAIL，并指向新 pack；原生成字节在 before 内保留。

定向测试覆盖实际 native 字符、错误 source ID、字符索引漂移、重复插入、OCR region 重复 crop offset、词子串共享 polygon、跨 word 同 bbox 不错误 dedupe、表格 cell 分配、mixed sequence、真实 case 回环、异向 scale/四方向旋转/CropBox raster 对照、HTML 的可见选区、未报告 11 项守恒，以及 Gate/人工材料不被自动覆盖。

仅运行以下定向检查；没有运行完整 Release Gate：

```bash
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/trace_evidence_coordinates.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/recheck_evidence_coordinates.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python -m pytest experiments/extraction_challenger/test_evidence_coordinates.py experiments/extraction_challenger/test_release_gate.py::test_human_evidence_is_pending_and_cannot_unlock_golden experiments/extraction_challenger/test_release_gate.py::test_review_pack_refuses_to_replace_already_reviewed_evidence -q
```

末次实际结果：**30 项定向测试通过**（0.55 s）。测试中对 release_verify 的调用只验证入口 fail-closed guard，guard 之后的完整 Gate 未执行。before hash、202 个生产文件 hash 仍不变。

人工待决事项：对全部 17 项重新复核；尤其确认 E012 行级近似是否满足业务要求。若仍要求 E012 精确 glyph 框，该项继续 FAIL，不能把本次 fallback 当作 glyph 修复或独立人工 PASS。

## Native 逐字符真值摘录

以下数值来自本轮新读 PyMuPDF rawdict；全精度与四角 polygon 以对应 trace JSON 为准。

| Case | native char | native char index | raw PDF/native bbox (pt) | rendered pixel bbox |
|---|---|---:|---|---|
| E001 | `1` | 43 | `[96.0, 120.411674, 102.0, 132.411682]` | `[192.0, 240.823349, 204.0, 264.823364]` |
| E001 | `、` | 44 | `[102.0, 120.411674, 114.0, 132.411682]` | `[204.0, 240.823349, 228.0, 264.823364]` |
| E001 | `乙` | 45 | `[114.0, 120.411674, 126.0, 132.411682]` | `[228.0, 240.823349, 252.0, 264.823364]` |
| E001 | `方` | 46 | `[126.119553, 120.411674, 138.119553, 132.411682]` | `[252.239105, 240.823349, 276.239105, 264.823364]` |
| E001 | `提` | 47 | `[138.119553, 120.411674, 150.119553, 132.411682]` | `[276.239105, 240.823349, 300.239105, 264.823364]` |
| E001 | `供` | 48 | `[150.119553, 120.411674, 162.119553, 132.411682]` | `[300.239105, 240.823349, 324.239105, 264.823364]` |
| E001 | `的` | 49 | `[162.239105, 120.411674, 174.239105, 132.411682]` | `[324.47821, 240.823349, 348.47821, 264.823364]` |
| E001 | `货` | 50 | `[174.239105, 120.411674, 186.239105, 132.411682]` | `[348.47821, 240.823349, 372.47821, 264.823364]` |
| E001 | `物` | 51 | `[186.239105, 120.411674, 198.239105, 132.411682]` | `[372.47821, 240.823349, 396.47821, 264.823364]` |
| E001 | `为` | 52 | `[198.239105, 120.411674, 210.239105, 132.411682]` | `[396.47821, 240.823349, 420.47821, 264.823364]` |
| E001 | `原` | 53 | `[210.358658, 120.411674, 222.358658, 132.411682]` | `[420.717316, 240.823349, 444.717316, 264.823364]` |
| E001 | `厂` | 54 | `[222.358658, 120.411674, 234.358658, 132.411682]` | `[444.717316, 240.823349, 468.717316, 264.823364]` |
| E001 | `生` | 55 | `[234.358658, 120.411674, 246.358658, 132.411682]` | `[468.717316, 240.823349, 492.717316, 264.823364]` |
| E001 | `产` | 56 | `[246.47821, 120.411674, 258.47821, 132.411682]` | `[492.956421, 240.823349, 516.956421, 264.823364]` |
| E001 | `的` | 57 | `[258.47821, 120.411674, 270.47821, 132.411682]` | `[516.956421, 240.823349, 540.956421, 264.823364]` |
| E001 | `全` | 58 | `[270.47821, 120.411674, 282.47821, 132.411682]` | `[540.956421, 240.823349, 564.956421, 264.823364]` |
| E001 | `新` | 59 | `[282.597748, 120.411674, 294.597748, 132.411682]` | `[565.195496, 240.823349, 589.195496, 264.823364]` |
| E001 | `产` | 60 | `[294.597748, 120.411674, 306.597748, 132.411682]` | `[589.195496, 240.823349, 613.195496, 264.823364]` |
| E001 | `品` | 61 | `[306.597748, 120.411674, 318.597748, 132.411682]` | `[613.195496, 240.823349, 637.195496, 264.823364]` |
| E001 | `，` | 62 | `[318.717285, 120.411674, 330.717285, 132.411682]` | `[637.43457, 240.823349, 661.43457, 264.823364]` |
| E001 | `符` | 63 | `[330.717285, 120.411674, 342.717285, 132.411682]` | `[661.43457, 240.823349, 685.43457, 264.823364]` |
| E001 | `合` | 64 | `[342.717285, 120.411674, 354.717285, 132.411682]` | `[685.43457, 240.823349, 709.43457, 264.823364]` |
| E001 | `合` | 65 | `[354.717285, 120.411674, 366.717285, 132.411682]` | `[709.43457, 240.823349, 733.43457, 264.823364]` |
| E001 | `同` | 66 | `[366.836823, 120.411674, 378.836823, 132.411682]` | `[733.673645, 240.823349, 757.673645, 264.823364]` |
| E001 | `的` | 67 | `[378.836823, 120.411674, 390.836823, 132.411682]` | `[757.673645, 240.823349, 781.673645, 264.823364]` |
| E001 | `技` | 68 | `[390.836823, 120.411674, 402.836823, 132.411682]` | `[781.673645, 240.823349, 805.673645, 264.823364]` |
| E001 | `术` | 69 | `[402.95636, 120.411674, 414.95636, 132.411682]` | `[805.91272, 240.823349, 829.91272, 264.823364]` |
| E001 | `规` | 70 | `[414.95636, 120.411674, 426.95636, 132.411682]` | `[829.91272, 240.823349, 853.91272, 264.823364]` |
| E003 | `6` | 402 | `[195.240005, 564.171692, 201.240005, 576.171692]` | `[390.480011, 1128.343384, 402.480011, 1152.343384]` |
| E003 | `2` | 403 | `[201.240005, 564.171692, 207.240005, 576.171692]` | `[402.480011, 1128.343384, 414.480011, 1152.343384]` |
| E003 | `1` | 404 | `[207.240005, 564.171692, 213.240005, 576.171692]` | `[414.480011, 1128.343384, 426.480011, 1152.343384]` |
| E003 | `0` | 405 | `[213.240005, 564.171692, 219.240005, 576.171692]` | `[426.480011, 1128.343384, 438.480011, 1152.343384]` |
| E003 | `0` | 406 | `[219.240005, 564.171692, 225.240005, 576.171692]` | `[438.480011, 1128.343384, 450.480011, 1152.343384]` |
| E003 | `.` | 407 | `[225.240005, 564.171692, 231.240005, 576.171692]` | `[450.480011, 1128.343384, 462.480011, 1152.343384]` |
| E003 | `0` | 408 | `[231.240005, 564.171692, 237.240005, 576.171692]` | `[462.480011, 1128.343384, 474.480011, 1152.343384]` |
| E003 | `0` | 409 | `[237.240005, 564.171692, 243.240005, 576.171692]` | `[474.480011, 1128.343384, 486.480011, 1152.343384]` |
| E015 | `6` | 402 | `[195.240005, 564.171692, 201.240005, 576.171692]` | `[531.456665, 390.480011, 555.456665, 402.480011]` |
| E015 | `2` | 403 | `[201.240005, 564.171692, 207.240005, 576.171692]` | `[531.456665, 402.480011, 555.456665, 414.480011]` |
| E015 | `1` | 404 | `[207.240005, 564.171692, 213.240005, 576.171692]` | `[531.456665, 414.480011, 555.456665, 426.480011]` |
| E015 | `0` | 405 | `[213.240005, 564.171692, 219.240005, 576.171692]` | `[531.456665, 426.480011, 555.456665, 438.480011]` |
| E015 | `0` | 406 | `[219.240005, 564.171692, 225.240005, 576.171692]` | `[531.456665, 438.480011, 555.456665, 450.480011]` |
| E015 | `.` | 407 | `[225.240005, 564.171692, 231.240005, 576.171692]` | `[531.456665, 450.480011, 555.456665, 462.480011]` |
| E015 | `0` | 408 | `[231.240005, 564.171692, 237.240005, 576.171692]` | `[531.456665, 462.480011, 555.456665, 474.480011]` |
| E015 | `0` | 409 | `[237.240005, 564.171692, 243.240005, 576.171692]` | `[531.456665, 474.480011, 555.456665, 486.480011]` |
| E016 | `6` | 402 | `[195.240005, 564.171692, 201.240005, 576.171692]` | `[788.119965, 531.456665, 800.119965, 555.456665]` |
| E016 | `2` | 403 | `[201.240005, 564.171692, 207.240005, 576.171692]` | `[776.119965, 531.456665, 788.119965, 555.456665]` |
| E016 | `1` | 404 | `[207.240005, 564.171692, 213.240005, 576.171692]` | `[764.119965, 531.456665, 776.119965, 555.456665]` |
| E016 | `0` | 405 | `[213.240005, 564.171692, 219.240005, 576.171692]` | `[752.119965, 531.456665, 764.119965, 555.456665]` |
| E016 | `0` | 406 | `[219.240005, 564.171692, 225.240005, 576.171692]` | `[740.119965, 531.456665, 752.119965, 555.456665]` |
| E016 | `.` | 407 | `[225.240005, 564.171692, 231.240005, 576.171692]` | `[728.119965, 531.456665, 740.119965, 555.456665]` |
| E016 | `0` | 408 | `[231.240005, 564.171692, 237.240005, 576.171692]` | `[716.119965, 531.456665, 728.119965, 555.456665]` |
| E016 | `0` | 409 | `[237.240005, 564.171692, 243.240005, 576.171692]` | `[704.119965, 531.456665, 716.119965, 555.456665]` |
| E017 | `6` | 402 | `[195.240005, 564.171692, 201.240005, 576.171692]` | `[1128.343384, 788.119965, 1152.343384, 800.119965]` |
| E017 | `2` | 403 | `[201.240005, 564.171692, 207.240005, 576.171692]` | `[1128.343384, 776.119965, 1152.343384, 788.119965]` |
| E017 | `1` | 404 | `[207.240005, 564.171692, 213.240005, 576.171692]` | `[1128.343384, 764.119965, 1152.343384, 776.119965]` |
| E017 | `0` | 405 | `[213.240005, 564.171692, 219.240005, 576.171692]` | `[1128.343384, 752.119965, 1152.343384, 764.119965]` |
| E017 | `0` | 406 | `[219.240005, 564.171692, 225.240005, 576.171692]` | `[1128.343384, 740.119965, 1152.343384, 752.119965]` |
| E017 | `.` | 407 | `[225.240005, 564.171692, 231.240005, 576.171692]` | `[1128.343384, 728.119965, 1152.343384, 740.119965]` |
| E017 | `0` | 408 | `[231.240005, 564.171692, 237.240005, 576.171692]` | `[1128.343384, 716.119965, 1152.343384, 728.119965]` |
| E017 | `0` | 409 | `[237.240005, 564.171692, 243.240005, 576.171692]` | `[1128.343384, 704.119965, 1152.343384, 716.119965]` |
