OLD_PPSTRUCTURE_CODE = removed
OLD_PPSTRUCTURE_CONFIG = removed
OLD_PPSTRUCTURE_CLIENT = removed
OLD_PPSTRUCTURE_TESTS = removed/migrated
OLD_PPSTRUCTURE_SCRIPTS = removed
HISTORICAL_DATA_COMPAT = retained
OLD_RUNTIME_IMAGE = retained_for_rollback

# Legacy Cleanup 最终报告

记录日期：2026-09-24（Asia/Shanghai）。本次只清理旧提取实现、无消费者配置、失效脚本和历史实验入口；Golden expectation、比较业务规则、任务数据库与回滚镜像未修改。全仓路径级盘点与去留依据见 [legacy-cleanup-inventory.md](legacy-cleanup-inventory.md)。当前正式比较入口是 `ExtractionStage → ContractExtractionV3`，不再通过 named registry 或 auto extractor 选择。

## 删除与迁移

| 类型 | 处理 | 引用、替代与覆盖证据 |
| --- | --- | --- |
| 旧 extractor / 模型编排 | 删除 `backend/app/services/extractors/{ppstructure.py,ppstructure_ocr_hybrid.py,page_routing.py}` 和 `backend/app/services/models/{batch_processor.py,layout_detector.py,orchestrator.py,setup.py,table_recognizer.py,base.py,device.py,registry.py,remote_model.py}`。前八个模块的删除已在进入本次清理前的 V3 切换工作树中存在；后四个为本轮零调用审计新增删除。 | `backend/app/` 对旧类、旧 endpoint 的调用为 0；V3 提取测试覆盖 Native、Mixed、Scanned、表格、旋转和来源证据。四个旧模型基础模块没有生产或正式测试外部调用。 |
| Factory / Registry | 删除 `backend/app/services/extractors/registry.py`；将 `factory.py` 缩为单一 `build_compare_document_extractor()`；移除未被生产或正式测试调用的 `AutoDocumentExtractor`/named selector。 | 全仓唯一正式比较调用来自 `pipeline_stages.py`；`test_no_ppstructure_extraction.py` 检查只创建 `ContractExtractionV3`。 |
| HTTP client / API | 旧 PP-Structure client 已在前置切换中删除；`backend/app/` 没有 `/layout-parsing` request builder、response parser 或 proxy route。 | `test_no_ppstructure_extraction.py` 和 `test_legacy_config_audit.py` 扫描 `app/`；OCR 与通用 HTML/table parser 继续保留。 |
| 配置 | 前置切换已移除 `PPSTRUCTURE_*`、`LAYOUT_PARSING_*`、`COMPARE_EXTRACTOR`、`HYBRID_EXTRACTOR`、`NATIVE_FAST_PATH`、`NATIVE_PAGE_MODE` 等旧键；本轮再删 `DOCUMENT_EXTRACTOR`、`PYMUPDF_MIN_TEXT_CHARS`、`MODEL_REGISTRY_PRELOAD`，并统一 `PPDOCLAYOUT_LAYOUT_NMS` 为 `PPDOCLAYOUT_NMS`。 | `dead_config_keys=[]`，旧环境键对 `Settings` 的序列化结果无影响；两份 `.env.example` 与本机 `.env` 已使用新 NMS 键。 |
| 旧脚本 | 删除 `backend/scripts/replay_reviewed_tasks.py`；两个 benchmark 脚本删除依赖其旧类的 saved extraction 选项，保留 V3 live/HTTP replay。 | `replay_reviewed_tasks.py` 直接导入已删除类；正式测试不依赖它。`test_benchmark_support.py` 继续覆盖并发 driver。 |
| 实验/探针 | 将 11 份旧探针/一次性 trace 脚本与 10 个旧候选实验目录移至 `archive/experiments/2026-09-24/`；将 10 份历史诊断报告移至 `docs/archive/reports/`。 | 归档项无生产入口；最终 V3/Golden 证据、当前部署验证和复现工具保留在主目录。归档脚本中的旧路径只解释历史，不能作为当前命令执行。 |
| 测试 | 前置切换已删除只测旧 native fast path 的 `test_native_fast_path.py`；本轮把 `test_pipeline.py` 的假 extractor 标签迁为 V3，新增环境变量、死配置、旧实现禁入、新旧表格来源测试。 | 标题、表格、签署、OCR 来源、证据、ClauseSplitter、diff 和历史来源业务测试继续保留。 |

当前工作树相对 `HEAD`，上述 15 个已删除文件共 **2,856 LOC**；其中本次新增物理删除为 extractor `registry.py` **113 LOC**、`replay_reviewed_tasks.py` **174 LOC**、四个旧模型基础模块 **355 LOC**，其余为前置 V3 切换已有删除。另有 **3,165 LOC** 旧脚本移出 `backend/scripts/` 并归档，未算作代码销毁。配置/Factory 的行级删除另计于修改文件，不混入已删除文件统计。未删除任何 Golden expectation。

`Settings` 相对 `HEAD` 删除了 **27 个扁平配置键**：`PPSTRUCTURE_URL`、`PPSTRUCTURE_ACCESS_TOKEN`、`PPSTRUCTURE_TIMEOUT_SECONDS`、`PPSTRUCTURE_FORMAT_BLOCK_CONTENT`、`PPSTRUCTURE_USE_DOC_ORIENTATION_CLASSIFY`、`PPSTRUCTURE_USE_DOC_UNWARPING`、`PPSTRUCTURE_USE_REGION_DETECTION`、`PPSTRUCTURE_USE_SEAL_RECOGNITION`、`PPSTRUCTURE_USE_TABLE_RECOGNITION`、`PPSTRUCTURE_USE_TEXTLINE_ORIENTATION`；`HYBRID_COMPONENT_PARALLEL_ENABLED`、`HYBRID_LAYOUT_CENTER_FALLBACK`、`HYBRID_LAYOUT_OVERLAP_THRESHOLD`、`HYBRID_SAVE_MERGED_RAW`；`COMPARE_DOCUMENT_EXTRACTOR`、`COMPARE_REQUIRE_STRUCTURED_OCR`、`COMPARE_NATIVE_FAST_PATH_MODE`、`COMPARE_NATIVE_FAST_PATH_MAX_SUSPICIOUS_CHAR_RATIO`、`COMPARE_NATIVE_FAST_PATH_MIN_CHAR_BOX_COVERAGE`、`COMPARE_NATIVE_FAST_PATH_MIN_CHARS_PER_PAGE`、`COMPARE_NATIVE_PAGE_MODE`、`COMPARE_NATIVE_PAGE_MAX_OCR_SEGMENTS`；以及 `ALIGN_STRUCTURED_EXTRACTION`、`DOCUMENT_EXTRACTOR`、`LAYOUT_ANALYSIS_MODE`、`MODEL_REGISTRY_PRELOAD`、`PYMUPDF_MIN_TEXT_CHARS`。同时删去无消费者的 `ppstructure`、`hybrid`、`registry` 嵌套配置模型；`LAYOUT_PARSING_*` 在清理前配置声明中已为 0。`PPDOCLAYOUT_LAYOUT_NMS` 是前置切换中的临时键名，本轮改为 `PPDOCLAYOUT_NMS`。

## 保留引用与理由

| 位置 | 剩余内容 | 理由 |
| --- | --- | --- |
| `backend/app/models.py` | `ppstructure` raw path 与历史来源判别 | 本地 `storage/tasks.sqlite3` 的 7 条任务中 7 条含旧值；读旧任务所需。 |
| `backend/app/services/{source_consensus,document_role_repair,document_preparation,diff_quality,header_footer_compare,signing_region}.py` | `source == "ppstructure"` 或旧 source tag 判断 | 只处理读入的历史证据，不调用旧端点。业务规则未借清理修改。 |
| `backend/app/services/table_compare/parser.py` | `ppstructure_html` | 仅对历史 PP-Structure 来源块保留；新 V3 表格写入通用 `table_html`。正式测试覆盖两种读取。 |
| `backend/tests/` | 含旧来源值的 fixture/业务断言，旧实现禁入守卫 | 保持历史任务、标题、表格、签署及 diff 契约；不是旧 extractor 实现测试。 |
| `backend/scripts/replay_candidate_golden_v2.py`、`verify_textin_alignment.py` | 冻结旧 raw 路径读取 | 前者受 `test_candidate_golden_v2.py` 依赖，属历史结果复现；不会创建 PP-Structure 请求。 |
| `backend/scripts/smoke_ppdoclayoutv3_cutover.py`、`experiments/full_golden/` | `/layout-parsing` 字符串 | 仅作禁止旧请求的负向断言与请求审计。 |
| `docs/reports/`、`docs/archive/`、`archive/experiments/`、`output/`、`storage/` | 历史报告、实验与任务原始标签；生成数据中 618 个文件名含旧标识 | 证据、Golden 和回滚解释所需；不在生产调用链。 |
| 现有 PP-Structure service/image/model/compose | 回滚资产 | V3 尚未生产部署，尚无真实 `/layout-parsing=0` 观察窗口；只标 deprecated、禁止新环境部署，不停服删镜像。 |

本机任务库只用于证明旧值确实存在；生产数据库尚未核查。后续若完成数据迁移，再评估删除只读兼容。旧服务的生产 image ID/tag/config 仍须按 [回滚清单](ppdoclayoutv3-production-cutover-manifest.md) 核实；本次没有登录生产主机，也没有修改部署运行态。

## 验证

- 清理前 Golden：15 份合同，175 条失败；失败类型为 `FALSE_NEGATIVE_REGRESSION=80`、`UNEXPECTED_CONFIRMED_DIFF=51`、`DECISION_REGRESSION=43`、`BBOX_REGRESSION=1`。按合同、case ID、断言、失败类型、原因排序的 SHA-256 为 `2ef49b9d57c16ea2825fbd1903755becf38715fd26db7a7df31c88785dba5bb6`，记录于 `output/analysis/legacy-cleanup/golden-before.json`。
- 清理后定向：表格/V3/配置集合 **155 passed**；再删除四个孤立模型模块后，生产入口、配置、V3、pipeline、比较和 benchmark 集合 **133 passed**。两组均覆盖本轮改动；全量结果见下。
- 最终删除状态下完整 pytest：**1779 passed、9 skipped、28 failed**。28 个失败仍落在 Golden 阻断、matching feature（5）、matrix job/pipeline（7）、task observability（14）及既有缓存读数 fixture（1）；与本轮前次全量执行的失败测试标识逐项相同，新增失败为 0。未修改这些业务失败。
- 清理后 Golden：15/15 合同执行，仍为 **175** 条失败，四类数量不变。按合同、case ID、断言、失败类型和原因逐项比较，清理前后列表完全相同，SHA-256 均为 `2ef49b9d57c16ea2825fbd1903755becf38715fd26db7a7df31c88785dba5bb6`；清理后明细在 `output/analysis/legacy-cleanup/golden-after.json`。Golden 数据校验 15/15 HASH/PAGES OK，`check_golden_baseline_change.py` 返回 `GOLDEN_BASELINE_UNCHANGED`。
- 最终删除状态下 B 真实服务 smoke：**COMPLETE**，56.911 s；`NATIVE_GOOD=80`、Layout 80、OCR 0、`/layout-parsing=0`、diff 0，和清理前记录相同。D：**COMPLETE**，60.656 s；`NATIVE_GOOD=39`、`MIXED=1`、`SCANNED=40`、Layout 80、OCR 41、`/layout-parsing=0`、diff 24，和清理前记录相同。逐次结果在 `output/analysis/legacy-cleanup/{B-smoke-final,D-smoke-final}/smoke.json`。
- `ruff check` 改动文件：通过；`compileall app tests scripts`：通过；`git diff --check`：通过。完整仓库 `ruff check .` 报 94 项问题，均不在本次改动文件的定向检查范围内，本轮未进行无关业务格式化。

Golden 的 175 条业务失败仍属于 Production Migration Parity；本次没有修改 FALSE_NEGATIVE、DECISION、UNEXPECTED_DIFF 或 BBOX 的业务修复逻辑。生产部署/下线门槛未变化。
