# Complex Wired Table Recovery Benchmark — 2026-09-24

## 直接结论

1. **Paddle V2 baseline**：上一轮冻结的 `PP-LCNet_x1_0_table_cls → wired_table → SLANeXt_wired → RT-DETR-L_wired_table_cell_det → frozen PP-OCRv6`。本轮没有重跑。上一轮五张已知误判表恢复 `3/5`；复杂报价表失败；五个金额的 `text+row+col+rowspan+colspan+bbox` 同时正确为 `0/5`。
2. **MinerU wired 实际使用**：独立环境中 [MinerU 源码 commit `3e602918`](https://github.com/opendatalab/MinerU/tree/3e60291846cb7c3bf8fe7f4f16238f4fc6cce491) 的 `TSRUnet + TableRecover`，官方 `unet.onnx`，CPU ONNX Runtime 1.27.0。仅调用有线表几何和逻辑恢复；`need_ocr=False`、`ocr_engine=None`，再用冻结 PP-OCRv6 和上一轮统一的 `assign_ocr_to_cells()` 填文字。没有启动 MinerU 文档 pipeline 或二次 OCR。
3. **UniTable 实际使用**：未运行。用户本轮明确排除 UniTable；其下载已停止，未完成权重与实验文件已清理。以下表格均记为“排除”，不能把它算作失败模型。
4. **Stage 1 frozen crop**：`RETURN_CONFIRMATION/D009` 的 p3、p4、p5、p6；`KAILI_FOUR_PROJECTS/D007` 的 p3、p4；`NANJING_DINGYI_XUZHOU_BANGYU` 的 p3、p4，共 8 张。精确 region ID 见下表；没有重新裁图。
5. **五个关键金额**：MinerU 对 `64500×2 / 17100 / 12400 / 118000` 均达到 `TEXT/ROW/COL/ROWSPAN/COLSPAN/BBOX/EVIDENCE = PASS`，即 `5/5 OVERALL_PASS`。五个已标注金额合并格也均精确匹配。逐项原值及 PDF 框 IoU 见下文。
6. **Merged cell 与 Evidence**：金额局部 MinerU 明显优于 Paddle baseline；但第三份合同 p3 的 `11000` 合并格被扩为 `rowspan=8,colspan=2`，人工期望 `7,1`，且与下一格重叠。p4 的“备注”整行未检出，5 条 OCR 全部未分配。因此整表的结构和 Evidence **不可用**。坐标转换链正确不等于模型的格子几何正确。
7. **重复 OCR**：MinerU `false`；统一使用冻结 PP-OCRv6。UniTable 未运行。
8. **是否进入 16 crop**：**否**。虽然金额 `5/5`，第三份合同存在明显整行漏识别，未满足 Stage 2 的“无灾难性漏行”门槛；按预设停止。本轮仅运行 8 张。
9. **推荐**：`RECOMMENDED_CANDIDATE = NONE`。MinerU 的金额局部值得保留研究记录，但当前输出不能作为完整合同表格结构服务。
10. **下一阶段**：停止搜索类似 TSR 模型。若另立任务，可评估合同表格专用 fine-tuning 或 VLM fallback；本轮没有启动。

## 冻结输入与运行边界

[输入校验记录](../../output/analysis/complex-wired-table-benchmark-20260924/input-check.json)逐一验证了 `16/16` crop 像素 SHA256、全部 `957` 条 OCR 记录 SHA256、page/table bbox、裁图与页面像素一致性、旧 Paddle artifact 内的 bbox 与 `crop_to_pdf` 一致性。上一轮冻结的 `957/957` OCR 框 crop→page→PDF 往返最大误差为 `0 pt`。Stage 1 只读取其中 8 张、`752` 条 OCR，未重新 OCR、渲染 PDF 或修改上一轮产物。

源码和权重固定如下：[模型来源清单](../../output/analysis/complex-wired-table-benchmark-20260924/model-provenance.json)。MinerU 本地 wired 源码路径在该 commit 下干净；权重来自[官方 MinerU ONNX 仓库](https://huggingface.co/opendatalab/MinerU-4_models_onnx)，`Table/unet.onnx` SHA256 `0ea48d3a17e35ef5c2e498a5e799566073234d39b1079ca21d9f4fafe73c6d20`，8,335,007 字节；该文件与官方 PDF-Extract-Kit 的对应权重 SHA256 一致。Python 环境为 NumPy 2.2.6、OpenCV 4.13.0、Pillow 12.2.0、ONNX Runtime 1.27.0。MinerU 使用[Apache 2.0 加附加条款](https://github.com/opendatalab/MinerU/blob/3e60291846cb7c3bf8fe7f4f16238f4fc6cce491/LICENSE.md)；本轮未作服务部署。

`ORIENTATION_USED=false`：8 张冻结 crop 均没有在 adapter 中人为旋转。TSRUnet 内部可修正检测到的轻微倾斜，其输出框已在模型代码中逆回原 crop；本轮保存的 bbox 均使用 `TABLE_CROP_PIXELS`。坐标回投复用上一轮 `artifact.coordinates()`，同时保存 `PAGE_IMAGE_PIXELS` 和 `PDF_PAGE_COORDS`。没有跨页表合并。

## Stage 1 逐页结构与性能

人工 `expected rows/cols` 来自冻结图像可见的横纵带计数，包含空分隔带；没有伪造全量逐格标注。人工记录见 [manual-review.json](../../output/analysis/complex-wired-table-benchmark-20260924/manual-review.json)。`detected merged` 是模型数量，不代表正确数量。耗时单位秒，包含模型推理，不含加载。

| 合同 / 页 | region ID | 期望行×列 | MinerU 行×列 | cell / merged | OCR 分配 | 推理 | 观察 |
|---|---|---:|---:|---:|---:|---:|---|
| KAILI p3 | `e03a2a194fd2-p3:v3:3` | 14×9 | 14×9 | 97 / 10 | 100% | 0.444 | `17100` 正确 |
| KAILI p4 | `e03a2a194fd2-p4:v3:0` | 9×9 | 9×9 | 49 / 9 | 100% | 0.201 | `12400`,`118000` 正确，备注格检出 |
| NANJING p3 | `3d706111bdf7-p3:v3:3` | 16×9 | 16×9 | 103 / 7 | 99.2% | 0.375 | `11000` 合并格错误；slot 与 bbox 重叠 |
| NANJING p4 | `3d706111bdf7-p4:v3:0` | 5×9 | 4×9 | 18 / 6 | 61.5% | 0.098 | **“备注”整行漏识别，5 条 OCR 未入格** |
| RETURN p3 | `d8301f12f62e-p3:v3:5` | 5×9 | 5×9 | 45 / 0 | 100% | 0.279 | 本页局部结构可用；未做整表验收 |
| RETURN p4 | `d8301f12f62e-p4:v3:1` | 17×9 | 17×9 | 133 / 6 | 100% | 0.276 | 第一个 `64500` 正确 |
| RETURN p5 | `d8301f12f62e-p5:v3:0` | 15×9 | 15×9 | 105 / 10 | 100% | 0.247 | 本页局部结构可用；未做整表验收 |
| RETURN p6 | `d8301f12f62e-p6:v3:0` | 19×9 | 19×9 | 138 / 10 | 100% | 0.248 | 第二个 `64500` 正确 |

Stage 1 MinerU 总计检出 `58` 个 merged cells，`746/752` 条 OCR 归属；模型加载 `0.05s`，每表推理 P50 `0.262s`、P95 `0.444s`，设备为 CPU。逐表 OCR→cell 分配耗时 `0.00006–0.00134s`、artifact build `0.00008–0.00043s`，见 [8 个 TableArtifact](../../output/analysis/complex-wired-table-benchmark-20260924/artifacts/mineru_wired/)；未记录峰值内存。历史 Paddle 16 crop 推理 P50 `1.937s`、P95 `2.594s`，本轮未重跑，速度不能替代结构正确性。

## 五个关键金额的硬检查

规则为八个检查全部通过才是 `OVERALL_PASS`。`BBOX_PASS` 使用已冻结人工目标框，记录原始 IoU 和覆盖率；本次人工复核同时查看了[红框期望、绿框预测叠加图](../../output/analysis/complex-wired-table-benchmark-20260924/overlays/mineru_wired/)。PDF IoU 是两个框按同一冻结仿射链回投后的重叠，反映模型几何与人工目标的一致性；坐标链自身另行校验。逐项布尔值保存在 [stage1-mineru_wired-evaluation.json](../../output/analysis/complex-wired-table-benchmark-20260924/stage1-mineru_wired-evaluation.json)。

| 金额 / region | 期望与预测 r,c,rs,cs | 文字 | bbox IoU | 目标框覆盖 | PDF IoU | 8 项 overall |
|---|---|---|---:|---:|---:|---|
| `64500` / RETURN p4 | `7,6,1,2` = `7,6,1,2` | `64500` | 0.858 | 0.868 | 0.858 | PASS |
| `64500` / RETURN p6 | `5,6,1,2` = `5,6,1,2` | `64500` | 0.794 | 0.794 | 0.794 | PASS |
| `17100` / KAILI p3 | `8,6,1,2` = `8,6,1,2` | `17100` | 0.855 | 0.855 | 0.855 | PASS |
| `12400` / KAILI p4 | `5,6,1,2` = `5,6,1,2` | `12400` | 0.804 | 0.824 | 0.804 | PASS |
| `118000` / KAILI p4 | `7,6,1,2` = `7,6,1,2` | `118000` | 0.772 | 0.828 | 0.772 | PASS |

其中 `TEXT_PASS/ROW_PASS/COL_PASS/ROWSPAN_PASS/COLSPAN_PASS/BBOX_PASS/EVIDENCE_PASS` 对五条均为 `true`。`BBOX_PASS` 的人工标注框容差是本次评价口径，**不是**生产阈值。金额 `5/5` 只证明这五个格子；不证明整表完整。

## Merged cell 与 Evidence 失败类型

本次人工点验 7 个 merged cells：五个金额格精确；NANJING p3 的 `11000` 金额格逻辑跨度错误；NANJING p4 的整行备注格缺失。因此在**已点验集合**中：`merged_cells_expected=7`、`merged_cells_detected=6`、`merged_cells_exact_match=5`、`merged_cells_wrong_span=1`、`merged_cells_missing=1`。p3 的错误合并格与后续格产生一个实际 slot overlap 和一个严重 bbox overlap，额外误合并数量在全表未逐格标注前不能可靠报出。模型总检测数 `58` 只作原始指标。

NANJING p3 的两个 `11000` 纵向金额格应止于第 14 行；预测中右侧一个成为 `r8 c7 rowspan8 colspan2`，覆盖到第 15 行及备注列，并与 cell `102` 重叠。见[该页全部格叠加图](../../output/analysis/complex-wired-table-benchmark-20260924/overlays/mineru_wired/all_cells/3d706111bdf7-p3-v3-3.png)。NANJING p4 的第 5 行是横跨 9 列的“备注”格；模型只输出前 4 行，冻结 OCR 中的“备注”和三条说明及一条续行共 5 条全部未分配。见[原图与预测框](../../output/analysis/complex-wired-table-benchmark-20260924/overlays/mineru_wired/all_cells/3d706111bdf7-p4-v3-0.png)。这不是 OCR 缺字，而是模型没有提供可分配的 cell bbox。

## 比较与停止决策

| Metric | Paddle V2 frozen baseline | MinerU wired Stage 1 | UniTable |
|---|---:|---:|---|
| 简单表恢复 | 3/3（历史结果） | 未进入 Stage 2 | 用户排除 |
| 已知 5 表完整恢复 | 3/5（历史结果） | 未做全片段完整验收 | 用户排除 |
| 5 关键金额 exact | 0/5（历史结果） | **5/5** | 用户排除 |
| merged exact | 关键金额 0/5（历史结果） | 已点验 5/7；另 1 错跨度、1 缺失 | 用户排除 |
| Evidence usable | 部分简单表 | 五个金额局部可用；整表不可用 | 用户排除 |
| OCR duplicate | no | no | 未运行 |
| 推理 P50 | 1.937s（历史 16 crop） | 0.262s（本轮 8 crop） | 未运行 |

Stage 1 出现整行漏识别，因此不满足“关键金额优于 baseline + 无明显灾难性漏行 + Evidence 可用”的并列条件。没有运行全部 16 crop，也没有重验三张简单表 `RETURN D007/D008、KAILI D006`；这些不是零分，属于未运行。五张已知 false DELETE 表的业务差异状态亦未重新执行或宣称消失。本轮没有修改 Compare、Golden expectation、正式 app、PP-DocLayoutV3、PP-OCRv6 或生产服务。

```text
STAGE1_RESULT = NO_CANDIDATE
STAGE2_16_CROP_RUN = false
UNITABLE_EXCLUDED_BY_USER = true
PADDLE_V2_RETESTED = false
TATR_RETESTED = false
FULL_GOLDEN_RUN = false
PRODUCTION_CODE_CHANGED = false
PRODUCTION_SERVICE_CHANGED = false

TABLE_STRUCTURE_BENCHMARK = INSUFFICIENT
RECOMMENDED_CANDIDATE = NONE
READY_FOR_SERVICE_POC = false
READY_FOR_APP_INTEGRATION = false
READY_FOR_PRODUCTION = false
```
