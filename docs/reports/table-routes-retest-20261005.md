# PP-StructureV3 与 MinerU 4 表格路线重测

日期：2026-10-05。结论：**在本项目这批扫描合同表格上，优先继续 MinerU 4。默认大模型的表格内容与结构明显优于 PP-StructureV3；接入正式链路前仍需解决单元格坐标。** 本次没有改生产代码、Golden 或验收阈值。

## 实测结果

使用同一批 16 张冻结表格图片，来自 `RETURN_CONFIRMATION`、`KAILI_FOUR_PROJECTS`、`NANJING_DINGYI_XUZHOU_BANGYU`。其中 8 张复杂表有人工行列数参考，5 个金额单元格有文字、行列、合并跨度和位置参考。

| 检查项目 | PP-StructureV3 表格组件 | MinerU 4 有线小模型组件 | MinerU 4 默认档位的表格大模型组件 |
|---|---:|---:|---:|
| 新推理完成 | 16/16 | 16/16 | 16/16 |
| 5 个金额的文字、行列与合并跨度全部正确 | 0/5 | 5/5 | 5/5 |
| 8 张复杂表的行列数正确 | 4/8 | 7/8 | 8/8 |
| 南京第 3 页 `11000` 的目标合并跨度 | HTML 跨度正确，但整表多一列且框异常 | 错误，7 行 × 1 列被识别成 8 行 × 2 列 | 正确，7 行 × 1 列 |
| 南京第 4 页的备注整行 | 生成了空备注行，备注文字放进上一行 | 漏掉，5 条 OCR 未入格 | 正确保留在跨 9 列的备注行 |
| 5 个金额连同图片框、PDF 坐标全部正确 | 0/5 | 5/5 | 无法满足，公开输出不含单元格坐标 |

这些是有限人工参考的通过数，**不是完整表格准确率**。没有给全部单元格做新真值标注，也没有跑完整合同差异 Golden。

### 具体错误与改善

Paddle 仍把“小计 64500”和上一行的“500”装进同一个格子；`17100`、`12400` 的行列或合并跨度错误，`118000` 与 `29500` 混入同格。直接检查 Paddle 原始 HTML，5 个金额的文字与逻辑全对仍是 0/5，因此问题并非只由实验的 HTML/框适配产生。另有 5/16 张的 HTML 单元格数与物理框数量不一致，不能靠按序配对就认定坐标可靠。

MinerU 4 的 UNet + TableRecover 对这 5 个金额同时提供了正确的逻辑结构和位置，但南京两处旧问题仍在。它使用的官方 UNet 权重与上次实验逐字节相同；升级到 4.0.10 并没有自动消除这些结构错误。

通过 MinerU 4 官方运行时实际调用默认 `standard` 使用的 VLM 表格组件后，8 张复杂表的行列数全部对上，5 个金额的文字及逻辑结构全部对上；南京两处旧问题均得到改善。不过结果是 HTML，没有单元格 bbox。本次没有用猜测的框把它包装成可用于证据高亮的结果。

## 测试范围与版本

- **Paddle**：PaddleOCR 3.7.0、PaddleX 3.7.2、PaddlePaddle 3.2.2。此次读取的是官方 `PP-StructureV3.yaml` 的 `TableRecognition` 子配置，而非上次独立 `table_recognition_v2.yaml` 的默认配置。前者使用有线 `SLANeXt_wired`、无线 `SLANet_plus` 和对应 RT-DETR 单元格检测器。16 张均被分类为有线表，因此本次没有验证无线表的能力。
- **MinerU 小模型**：MinerU 4.0.10 的有线 UNet + TableRecover。这是有线组件测试，不代表完整 `basic` 档位的分类、无线识别、候选选择和文档流程。
- **MinerU 原生优先**：DocVortex 0.5.9 对原始 PDF 的指定区域运行 `prepare_table_page` / `recover_table_region`，16/16 调用正常，原生恢复接受 0/16。这些扫描页的原生字符数均为 0，走模型回退符合预期。
- **MinerU 大模型**：MinerU 4.0.10 官方 `ModelSingleton` 运行时，`mineru-vl-utils` 2.0.5、`mineru-llama-cpp` 0.1.2，`MinerU2.5-Pro-2605-1.2B` 的 Q8 GGUF，Mac Metal。模型名称仍带 2.5，是安装的 MinerU 4.0.10 默认使用的模型。使用官方表格 prompt 与默认采样参数；不是自行写提示词调用通用模型。

Paddle 和 UNet 输入同一份 957 条冻结 OCR，通过同一生产回填逻辑配字。VLM 看同样的图片，自行识字，不接收这些冻结文字。因此前两者是控制识字因素的结构测试，VLM 是表格内容与结构联合识别的补测。

固定表格区域意味着本次没有测试页面布局检出、完整文档预处理、跨页拼表或最终差异匹配。Q8/llama.cpp 的结果也不能自动视为所有 VLM 引擎与精度的结果。

## 防错检查与正式接入

| 检查 | Paddle | MinerU 有线组件 | MinerU VLM |
|---|---:|---:|---:|
| 现有生产 validator 未触发拒绝 | 10/16 | 14/16 | 0/16，缺少单元格框 |
| 原实验更严格的结构与全部 OCR 证据检查通过 | 7/16 | 14/16 | 不适用其现有 OCR 回填方式 |
| 冻结 OCR 入格 | 946/957 | 951/957 | 不使用冻结 OCR 配字 |

validator 通过只表示没有命中已有防错规则，不能当成准确率。Paddle 有表通过生产 validator，人工检查金额却失败，正好说明这个区别。VLM 的 0/16 是当前证据契约不满足，不能解释成识别内容全错。

建议保留当前正式链路，优先推进 MinerU 4 的候选接入实验：有线组件能提供较好的格子位置，大模型能处理本次小模型失败的复杂结构。下一步需要验证如何将正确的 HTML 结构可靠地关联到原图文字和单元格位置，并在关联失败时保留待复核文字。**本次没有实现或验证这种组合，更没有证明可以全量替换生产提取器。** Paddle 暂不作为这批复杂表的优先方向。

## 证据与复现

- 汇总：`output/analysis/table-routes-retest-20261005/evaluation.json`。
- 分路线结果：同目录的 `paddle-evaluation.json`、`mineru4_wired-evaluation.json`、`mineru4_vlm-evaluation.json`、`mineru4-native-evaluation.json`。
- 原始输出与逐表检查：同目录 `raw/`、`artifacts/`；金额和错误合并格核对图在 `overlays/`。
- 输入校验、版本、源码与权重 SHA-256：同目录 `input-check.json`、各 `*-runtime.json`、`vlm-model-manifest.json`、`evaluation-code-manifest.json`。
- 可复现脚本与命令：`experiments/table_routes_retest_20261005/README.md`。

GGUF 固定 Hugging Face revision `9185688a0495e1577d521a757c7c0b62dd38ca48`，下载使用经过大小与 SHA-256 核对的 ModelScope 镜像。主模型 SHA-256 为 `3f3523429c880d675c0330cb3de78240452bc40a961d037e24f91ab8488f03e7`；视觉投影为 `b6c08ab50352677f7396af45ee60682f353f55488e26911d8154bfc37514d3f8`。UNet SHA-256 为 `0ea48d3a17e35ef5c2e498a5e799566073234d39b1079ca21d9f4fafe73c6d20`。

两个新环境独立安装候选版本，只读复用历史实验的大型依赖。原输入及旧实验结果未覆盖；生产 validator、OCR 回填与复用金额门槛源码的校验值保持不变。全部候选无推理异常。部分 CPU 组件曾同时运行，记录耗时仅作探索性记录，本次不做速度排名。失败下载的临时缓存和下载分片已清理，复现所需的校验后权重保留在忽略提交的 `weights/`。
