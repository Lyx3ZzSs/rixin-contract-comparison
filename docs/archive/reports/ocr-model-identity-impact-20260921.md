# OCR Model Identity Impact Analysis

日期：2026-09-21。范围：当前工作区 `backend/app`、`backend/scripts`、`experiments`、`frontend/src`、配置示例、README，以及既有 B 样本 raw artifacts。仅源码与已落盘结果审查；未调用识别服务、未运行 benchmark、未改生产代码、未重命名或撤销既有实验。

## 结论

`PPOCRV5Extractor` / `ppocrv5` 是当前客户端的历史名称，**不能证明部署模型是 PP-OCRv5**。用户已确认当前 `/ocr` 实际为 **PP-OCRv6**；本地客户端没有选择模型版本的参数，也没有把远端模型 identity 保存到统一 ExtractionResult。精确 Detection / Recognition 子模型名称、权重和 `/layout-parsing` 内部版本，必须来自远端运行配置与实现，不能从客户端类名推断。

命名错位不只影响显示：它进入配置入口、注册表、raw 文件名、API artifact 字段、block ID、source evidence、metrics 和 trace 聚合键。直接全仓替换会改变兼容接口及历史回放定位。当前阶段应保留历史接口，先独立记录真实 runtime identity。

## 1. Identity 必须拆开的层次

| 层次 | 当前能确认的值 | 证据 / 边界 |
|---|---|---|
| 客户端适配器 | `PPOCRV5Extractor`，`name="ppocrv5"` | [ppocrv5.py:25](../../backend/app/services/extractors/ppocrv5.py#L25)；纯 Python 客户端类，不是模型加载器 |
| 本地组合 pipeline | `PPStructureOCRHybridExtractor`，`ppstructure_ocr_hybrid` | [ppstructure_ocr_hybrid.py:50](../../backend/app/services/extractors/ppstructure_ocr_hybrid.py#L50) |
| `/ocr` 模型 family / version | PP-OCR / v6 | 本轮用户确认；当前本地 artifacts 无运行时版本字段，不能补造精确子模型名 |
| `/layout-parsing` 服务 pipeline | 客户端称 PP-Structure，真实 pipeline/version 由远端审计确定 | [ppstructure.py:136](../../backend/app/services/extractors/ppstructure.py#L136) 只决定 endpoint |
| Detection / Recognition 子模型 | 本地未记录 | 应分别记录，不能用 `ppocrv5` 或一个 `model_name` 代替两者 |
| Layout / Table 子模型 | 本地未记录 | 同上，pipeline 名不等于 Layout Detection / Table Recognition 模型名 |

`/ocr` 请求体仅含文件、fileType、方向/去弯曲开关、识别阈值、word box 与 visualize；无 `model_name`、`model_version`、`ocr_version`，见 [ppocrv5.py:156](../../backend/app/services/extractors/ppocrv5.py#L156)。`/layout-parsing` 同样只传模块开关，见 [ppstructure.py:142](../../backend/app/services/extractors/ppstructure.py#L142)。因此把类名改为 v6 不会升级/切换远端模型。

## 2. 影响分类与具体消费点

| 类别 | 位置与实际用途 | 影响 |
|---|---|---|
| 显示 / 注释 | README:137 将主文本称为 PP-OCRv5；`config_models.py:18,26` 的文档字符串；`seal_ocr.py:8,33` | 描述过时，本身不选择模型 |
| 错误与告警 | `ppocrv5.py:131–144,153,181–184,241`；`ppstructure_ocr_hybrid.py:120` | 运维会把 PP-OCRv6 故障归到 v5；改文案不改变推理 |
| Python 导入 / 别名 | `extractors/ppocrv5.py:25`；`extractors/paddleocr.py:1–5`；`extractors/__init__.py:8,19` | 导入兼容性，不是纯显示 |
| 配置选择 | `config.py:21–42`、`registry.py:92–107` 的 `ppocrv5`, `pp_ocrv5`, `ppstructure_ppocrv5` | 这些是合法 extractor 名；删除或改名会影响配置校验与 factory 路由 |
| 服务配置 | `config.py:90–100,379–390`、`clients.py:36`，环境变量 `PPOCRV5_*` | URL/token/timeout/推理参数真实生效；不是可随意删除的旧标签。URL 决定实际服务，名称不约束其版本 |
| 本地性能指标 | `ppstructure_ocr_hybrid.py:124,150,169,196` 的 `components.ppocrv5` 与 `ppocrv5_component` | 指标描述的是 `/ocr` 组件耗时；按 v5 解读不成立，改键影响消费者及历史对齐 |
| Trace endpoint 分类 | `perf_trace.py:91–110` 将 `/ocr` 固定映射 `ppocrv5_ocr`，并用历史类名识别通用调用栈 | endpoint 标签被误当真实模型；重命名类但不更新栈规则会影响调用原因追踪 |
| Trace 聚合 | `perf_trace_ledger.py:162,206,265,406,417` 固定消费/输出 `ppocrv5_ocr`、`ppocrv5`、`ocr_ppocrv5_seconds` | 单改 producer 会漏聚合；当前数字仍是相应 endpoint 的工作量，不能称 v5 模型性能 |
| Block / provenance | `ppocrv5.py:615,707` 的 `p{page}_ppocrv5_b{index}`；`ppstructure_ocr_hybrid.py:367,409,420,521` 的 source 标签 | 身份进入持久化结果与证据。改名可能产生大量与文本无关的 diff，破坏历史 ID 对齐 |
| 补读 provenance | `signing_region/value_ocr.py:87,291,459`；`signing_region/extractor.py:1086,1297` | ROI 补读与 evidence 仍标 v5；不能凭标签认为这是另一个模型 |
| API / raw artifacts | `models.py:375–426` 的 `OcrRawResultSidePaths.ppocrv5` 与 `_ppocrv5_raw` 文件名解析 | 字段是序列化契约；直接改 raw 后缀会让 legacy parser 无法归类已有文件 |
| 对外 extractor metadata | `ExtractionResult.extractor_used` → `pipeline_stages.py:343` → `CompareTask.extractor_used` → `api_presenters.py:53`；前端 `types.ts:113,143,368` | 报告的是 adapter/pipeline identity，不是实际模型 version |
| 数据回放 | `replay_reviewed_tasks.py:34`、`ppstructure_layout_only_equivalence.py:135`、`experiments/layout_decoupling/common.py:173` 等硬编码 raw 名 | 直接重命名会影响旧结果读取；历史结果应保留原始标签并补充真实 identity 说明 |

另一个需要避免的混淆：`ExtractionStage` 对 structured 结果的接受条件使用 `extractor_used`，见 [pipeline_stages.py:591](../../backend/app/services/pipeline_stages.py#L591)；结构化判定通过 `"ppstructure" in name`，见 [pipeline_stages.py:719](../../backend/app/services/pipeline_stages.py#L719)。pipeline 名称不是单纯展示字段，不能统一改成某个 OCR 模型名。

## 3. Cache：发现的是 identity 缺口，不在本阶段继续优化

### 3.1 实验 ExtractionCache

[extraction_cache.py:22](../../experiments/performance_phase2/extraction_cache.py#L22) 要求 scope、extractor version、OCR model version、layout model version、pipeline version；[同文件:31](../../experiments/performance_phase2/extraction_cache.py#L31) 把版本及配置 hash 写入 identity，再于 [40–41](../../experiments/performance_phase2/extraction_cache.py#L40) 加 PDF SHA256 算 key。

`self.name = extractor.name` 不直接进入 key，因此历史 `ppocrv5` 类名本身不是这里的唯一缓存版本。问题在于 [run_cache.py:27](../../experiments/performance_phase2/run_cache.py#L27) 为 OCR 和 layout 都传入 `UNVERIFIED-fixed-session-20260921`。这是未核实的单次实验固定标记，不是远端版本证明。远端部署若在同一标记下改变，同 PDF/配置仍可能命中之前结果。

[run_pipeline.py:38](../../experiments/performance_phase2/run_pipeline.py#L38) 用 `ppocrv5_`, `ppstructure_`, `layout_`, `compare_native_` 前缀收集相关配置并排除 token；重命名配置但不更新该列表会改变或遗漏缓存配置 identity。它另对 `backend/app/*.py` 内容计算代码版本（39 行），所以源码更名一般也会使实验 key 失效。本缓存仍是 `experiments/` pilot，不可把这个问题描述成已证实的生产持久 OCR cache 污染。

### 3.2 Benchmark HTTP replay

[benchmark_support.py:97](../../backend/scripts/benchmark_support.py#L97) 的 key 是 `SHA256(method + URL + body)`；scope 可选 task，**不含远端部署版本或权重 hash**。同一 endpoint 原地换模型不会改变请求 key。已有 replay 用于复现当时响应，不能证明重新执行了当前 PP-OCRv6，也不能据其耗时推导真实远端推理耗时。

缓存保存 status/content-type/body，回放仅重建 content-type header，见 [benchmark_support.py:109](../../backend/scripts/benchmark_support.py#L109) 与 [118](../../backend/scripts/benchmark_support.py#L118)；原始 Server-Timing 等 header 不保留。因此 replay 中缺少 timing/identity header 不等于 live 服务未提供它们。

### 3.3 不受 OCR 名称直接影响的缓存

例如生产 [page_render_cache.py:29](../../backend/app/services/page_render_cache.py#L29) 使用 PDF hash、页号、页面几何、旋转、scale、colorspace、alpha、annots、clip，缓存的是渲染图像，不应塞入 OCR version。未发现生产源码定义 `model_version` / `ocr_version` 字段；不能泛化为“所有 cache 都需改”。

## 4. Response metadata 与证据保存

[ExtractionResult](../../backend/app/services/extractors/base.py#L15) 只有 document、extractor_used、raw_result_path、warnings、profile、layout_quality、performance，无独立 runtime model identity。

[remote_metrics.py:12](../../backend/app/services/remote_metrics.py#L12) 只捕获数值型 Server-Timing 和合法 `logId`；其他 header、文本与模型 metadata 未记录。`/ocr` 的 [predict:145](../../backend/app/services/extractors/ppocrv5.py#L145) 返回归一化页列表，[normalize:178](../../backend/app/services/extractors/ppocrv5.py#L178) 从 `result.ocrResults` 解包、附 page_index 与图像尺寸；[extract:79](../../backend/app/services/extractors/ppocrv5.py#L79) 保存的就是该列表。因此顶层 runtime metadata 即使存在，也不会随 `/ocr` raw artifact 保存；页内已有字段会随复制保留。相比之下 `/layout-parsing` 的 [request:129](../../backend/app/services/extractors/ppstructure.py#L129) 返回完整 payload。

只读检查既有 `output/analysis/e2e-20260921/profile-B/runtime/tasks/profile-B/diagnostics/ocr/` 中 B-original/B-compare 的 `/ocr` 页与 `/layout-parsing` raw：没有发现 `model_name`、`model_version`、`pipeline_name`、`ocr_version`。`/ocr` 页包含 `model_settings`, `dt_polys`, `rec_texts`, `rec_scores`, `rec_polys`, `rec_boxes`, `text_word_boxes` 等；`model_settings` 是运行开关，不能据字段名当作模型版本证明。此检查不读取或披露合同正文。

## 5. Benchmark 与其他 model_name 的边界

现有 profiler/service probe/layout probe 使用 `ppocrv5` 标签，但该标签只指请求 `/ocr` 的客户端组件。应在解释历史结果时标注“历史 adapter 标签 ppocrv5；当前实际模型用户确认为 PP-OCRv6；历史部署版本未从 artifacts 核实”，不能把全部历史运行倒填 v6。

[benchmark_ocr_routing.py:65](../../backend/scripts/benchmark_ocr_routing.py#L65) 在 routing 不活跃时把 `ocr_pages=page_count`；这是文档页经过该 extraction route 的计数，**不是 Text Detection 次数，也不是 Text Recognition 次数**。不能靠字段名回答本次重复 recognition 问题。

全仓 `model_name` 还包括与本任务不同的含义：`matching/providers.py:44,241,348,380` 是 embedding/rerank 请求模型；`signing_region/visual.py:70,76` 是签署区域视觉检测器；`signing_region/models.py:74,134,140` 和 `pipeline_stages.py:1910,1929,1941,2051` 搬运该 detector identity；`models/batch_processor.py:51` 使用 registry 名进行区域模型分组；`experiments/layout_decoupling/adapters/models.py:53,59,72` 是实验 Paddle 子模型加载参数。它们不能一起替换为 PP-OCRv6。签署视觉 `model_name` 还参与无文本、无 hash 元素的 bbox 签名（`signing_region/comparator.py:1724`），不是通用 OCR 元数据。

## 6. 建议的 identity 契约（设计，不实施）

保留 `adapter_id="ppocrv5"`、当前配置别名与 artifact schema，另加 `runtime_identity`，至少分开：`pipeline_name/version`、`text_detection.model_name/version/weights_sha256`、`text_recognition.model_name/version/weights_sha256`、`layout_detection`、`table_structure_recognition`、`service_build/config_hash`，并记录 `identity_source`（远端自报/部署源码核验/用户确认/unknown）。所有不能证实的字段明确 unknown。

真实 identity 应伴随统一 Page Artifact、每个服务请求与 benchmark manifest，而不是藏进可变展示字符串。后续 cache 设计可以引用这些事实；本阶段不改 cache。文案可以显示“PP-OCRv6（历史适配器 ppocrv5）”，历史 artifacts 保留原貌。

## 7. 搜索范围补充

搜索覆盖 `PP-OCRv5`、`ppocr_v5`、`ocr_v5`、`ppocrv5`，并额外覆盖注册表真实别名 `pp_ocrv5`；当前生产源码主要使用 `ppocrv5`，无 `model_version`/`ocr_version` 声明。除上述核心文件，直接引用适配器的生产消费者还包括 `text_evidence.py:12,289`、`counterpart_text_recovery.py:11,17`、`seal_ocr.py:104–126`。`backend/tests` 中的 v5 fixtures 是既有接口/证据测试，不构成运行模型证据。

实验与脚本额外依赖点：`perf_trace_service_probe.py:89,91`、`ppstructure_layout_probe.py:134,145,219,220`；`experiments/layout_decoupling/baseline.py:46,57`、`secondary.py:36,42`、`candidates.py:61,226`、`finalize_evidence.py:83`、`reporting.py:96`；`experiments/e2e_diagnosis/report.py` 的指标展示。全部保留，未扩展实验。
