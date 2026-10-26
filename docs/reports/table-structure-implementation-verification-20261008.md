# 表格结构接入与验证

日期：2026-10-08。代码接入和本地组件、集成验证已完成；独立准确率验收及生产灰度尚未完成。当前交付为有线表结构补充和金额证据定位，无线分支已实现、默认关闭。

## 实施结果

保留原生 PDF 与规则检查通过的结果，只对未解决区域调用独立 CPU 服务。服务使用 PP-LCNet_x1_0_table_cls 分类，UNet + TableRecover 处理有线表，SLANet_plus 处理无线表。复用现有 PDF / PP-OCRv6 字符，不新增 OCR、VLM、Torch 或 Paddle 运行时，不初始化 MinerU 完整文档链路。

后端新增客户端与坐标适配器，并通过已有 TableResolver 接入。支持 legacy / shadow / enabled、两个结构分支独立开关、超时、并发限制和局部降级。失败清理 HTML、格框和来源元数据，保留原始文字。已有表格的文字与非表区域字段经过冻结回放断言保持一致。

本次修复了三类会影响合同差异的问题：

1. 表格 HTML 首块与金额所在 OCR 块不同。现在限定同页同区域、同单元格，按 block_id 与原始字符下标查找实际证据；重复金额按位置消歧。无法唯一定位时使用格框并记录粒度，不均分格宽猜字符位置。新模型结果揭示旧 HTML 的文字顺序错误时，还需查原始来源：只有来源明确支持另一顺序、缺少格内顺序且字符集合一致，才记录来源冲突并保留待复核；不凭字符重排否定真实金额变化。
2. 模型虚构空白边列或把合并标题拆成多个格。现在分别检查真实边线和逐行内部线段；真实空格保留。只有内部无分隔、四个外边都得到清晰图像支持的同一行片段才能合并，随后重新检查，不靠合并填网格漏洞。
3. 旋转 PDF 的字符排序及网格重聚类。现在按转正裁图坐标组织文字，保留来源块内原始字符顺序；逻辑拓扑取转正输出，逆变换格框只作为页面几何，比较侧不再按其包围框重新聚类。这项处理只适用于标记为模型来源的正式结果，原生/规则结果和影子发布继续沿用原解析路径。同一行不同来源块按实际高度重叠分组、再从左到右排序，避免固定高度分箱把复选框顺序颠倒。

UNet 显式开启 rotated_fix。默认候选组件失败时最多一次 pad64；发布阶段的像素检查失败不触发额外 padding。SLANet_plus 对逻辑格、框数量及全零占位框严格检查，避免先丢框再错位 zip。单元格保存稳定 ID、原始几何索引和文字 token 来源，并可通过 Document / JSON 往返。

## 固定模型与服务

底层八个文件固定为 MinerU 4.0.10 commit `c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93`，只有两处 runtime 相对导入路径修改；上游与本地源码哈希清单、原许可证保留在 vendor 中。八份上游文件均已从固定 commit 校验。

权重仓库 `opendatalab/MinerU-4_models_onnx` 固定 revision `358310b4f64b95f9fefc372ad899356e4111f376`。三份文件合计 22,870,189 字节，启动校验 SHA-256，不自动拉取其它模型。

| 模型 | 文件字节数 | SHA-256 |
| --- | ---: | --- |
| PP-LCNet_x1_0_table_cls | 6,776,877 | c84bf1d79c1c74d534b5b12adb14dd12151c42f7ae3e4be4f1042b830f80b949 |
| UNet | 8,335,007 | 0ea48d3a17e35ef5c2e498a5e799566073234d39b1079ca21d9f4fafe73c6d20 |
| SLANet_plus | 7,758,305 | d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b |

本地独立 Python 环境的真实 HTTP 服务已启动于 `127.0.0.1:8091`。一个进程、一个活动推理、4 个 ONNX CPU 线程，忙时返回 503；健康检查确认未导入 OCR/VLM 运行时。提供 25 项固定依赖、Dockerfile 和 compose 的 4 CPU / 3 GiB 配置，Docker 构建和目标服务器部署本次未验证。

## 验证结果

| 检查 | 结果与范围 |
| --- | --- |
| 后端相关测试 | 244 passed，覆盖结构比较、接入、合同提取、配置及故障回退 |
| 额外证据、事件及观察状态回归 | 67 passed，1 skipped（未安装旧 OCR 审计语料）；该跳过项不计为通过 |
| SLANet 解码测试 | 3 passed，覆盖占位框错位、数量不一致、真实空格与缩放 |
| 新增核心与实验脚本 Ruff | 通过；固定 vendor 不做格式改写 |
| 真实表区域 HTTP 集成 | 115 个区域：58 个已见回归 + 57 个同合同库新增区域；冻结原图、文字和来源哈希 |
| 结构化输出 | 55 → 104 / 115，新增 49 个；原有 55 个全部保留，剩余 11 个保持待复核 |
| 真实 HTTP 影子验证 | 115 / 115 正式 Page 发布字段保持；60 个模型区域生成诊断；排除诊断与验证耗时 |
| 五个重点金额 | 5 / 5，文字、行列、跨度、格框和页面证据均通过既有参考；未新增 OCR |
| 八个行列检查 | 8 / 8；不代表其余所有单元格已有人工真值 |
| 真实无线组件推理 | SLANet_plus 对一张已知真实裁图输出 28 格，无额外 OCR；只是组件可运行验证 |
| 实际 Layout / OCR 链路 | 凯里合同第 3 页原件、对比件：原件原生路线 OCR 0 次，对比件扫描路线 OCR 1 次并使用 UNet；均有结构化结果 |
| 实际比较链路 smoke | 上述新提取结果执行 CompareService，5 项差异、执行错误 0；不等于整个 API 任务状态测试 |

115 区域集成中模型区域总耗时 P50 为 0.490 秒，P95 为 0.869 秒，包括后端请求、适配与检查。10 张最大已知裁图的服务 RSS 每 20ms 采样，观测峰值 2,245,787,648 字节（约 2.09 GiB）；3 个重叠请求返回 503 / 200 / 503。权重文件小不等于运行内存小。该采样不包含后端、完整进程生命周期峰值或目标 Linux 机器性能。健康检查的 0.092 秒仅为模型构造时间，不包含 Python 导入或完整进程启动。

### 合同比较回放

最终结果：baseline / shadow 的 Golden 失败数均为 **174**，enabled 为 **167**；45 次回放执行 errors 均为 0。已知八项华北金额事实及位置失败得到修复，其中六项第 7 页定位检查在实际差异输出中全部通过，证据粒度均为 source_text，IoU 为 0.811～1.000。另两项为第 19、20 页的 150943.4 金额事实及位置。

唯一新增 Golden 失败事实是华北第 29 页分管领导栏的“年月日”：助手已查看两份原图，原件没有底部日期占位，对比件有，因此保留真实差异，不修改 oracle 消除该项。开发中发现的旋转词序、复选框词序及“类C / C 类”误报已消除；来源顺序冲突的必要保护仍保留。最终 15 份回放未发现新增已确认误报，这不代表未知合同错误率为零。

回放固定 15 份合同文字，只替换本次 115 个区域的结构字段；每份 baseline / shadow / enabled 三种模式。关闭语义远程匹配和 rerank，拦截所有 HTTP 补读。shadow 业务回放使用旧发布结果加候选诊断；另对全部 115 区域执行真实 shadow Resolver / HTTP，60 个未解决区域生成模型诊断，115 个 Page 正式发布字段全部与 baseline 相同，仅排除模型诊断和验证耗时。24 次封面/签署补读尝试被拦截，真实补读分支不在此次离线覆盖内。

回放调用 CompareService 主流程，未调用应用 TaskRunner 的任务终态收尾，返回任务 status=PROCESSING 不视为 API 完成态。只按实际差异、执行 errors 与 Golden 语义检查评价，不把执行无错误称为全 Golden 通过。

## 启用状态与尚缺验收

初次工程验证结束时，Settings 缺省仍为 legacy，真实 `.env` 未修改。2026-10-08 后续根据用户启用指令，已将本机根目录 `.env` 切换为 enabled、地址 `http://127.0.0.1:8091`；有线开启，无线关闭，允许一次有线替代。切回 legacy 仅影响后续任务，不改历史结果。

57 个新增区域仍来自同一批合同与模板，不能充当至少 60 张独立未知真实表、约 20 张真实无线表的人工确认评估。115 个区域的 90.4% 结构化覆盖率是开发集成指标，不能据此宣称准确率 90.4% 或错误放行率低于 5%。本次未更改 Golden 真值/阈值，也未清零其它模块的历史失败。

P0 / P1 / P2 的工程实现和已知失败验证已交付；P3 的独立准确率评估、目标机器性能及生产灰度仍待完成。无线模型保持关闭，不把有线收益作为无线验收结论。

## 证据与复现

- 服务及配置：[deployment/table_structure/README.md](../../deployment/table_structure/README.md)
- 集成脚本：[experiments/table_structure_integration_20261008](../../experiments/table_structure_integration_20261008)
- 冻结输入、115 个候选/发布结果：`output/analysis/table-structure-integration-20261008/input-manifest.json`、`tables/`
- 结构、金额、行列及健康状态：同目录 `component-summary.json`、`component-code-manifest.json`
- 八份上游文件校验：`upstream-source-verification.json`
- 实际业务与六项金额位置：`business/`、`business-code-manifest.json`、`business-differences.json`、`amount-business-verification.json`、`new-fact-review.json`
- 实际无线推理、内存、在线提取/比较：`wireless-component-smoke.json`、`shadow-live-summary.json`、`cost-probe.json`、`test-results.json`、`online/`

先按服务 README 启动，再从仓库根目录依次运行 `verify.py`、`shadow_verify.py`、`business_replay.py`、`final_checks.py`。`online_smoke.py` 请求真实 Layout/OCR，之后运行 `online_compare.py`。`cost_probe.py` 接收本次表格服务进程 PID。所有结果保存在上述专用目录，不重写历史任务。

## 本机启用与服务重启验证（2026-10-08）

本机表格服务已重启，前后端从停止状态启动；三个服务健康检查均为 HTTP 200。工作台地址为 `http://127.0.0.1:5173/contract/`，后端为 `http://127.0.0.1:8002`，表格服务为 `http://127.0.0.1:8091`。8000 被其它项目占用，因此本项目后端改用 8002，前端运行时已确认读取对应 API 地址。

通过真实 API 提交两张已知凯里合同第 3 页，任务 `becc30d9-903d-4422-9676-a4a684611c83` 经 TaskRunner 完成，状态 COMPLETED、进度 100%、差异 5 条、errors 为空。该任务执行期间表格服务收到一次 `/table-structure` 请求并返回 HTTP 200。当前未保留该任务模型候选诊断，因此这次验证确认真实服务链路与任务终态，不新增结构准确率结论。SLANet_plus 权重已加载，但后端无线分支仍关闭。

运行进程、日志、配置及验证结果记录在 `output/runtime/local-enabled-20261008/services.json` 与 `restart-verification.json`。这次操作仅完成本机启用，不代表目标服务器部署或 P3 独立评估完成。
