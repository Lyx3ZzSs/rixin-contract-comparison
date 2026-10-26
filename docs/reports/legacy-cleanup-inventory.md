# Legacy Cleanup 全仓盘点

盘点日期：2026-09-24。范围包括生产代码、配置、测试、脚本、部署、文档、实验及本地历史数据。以下明细由清理前工作区扫描生成；未提交的 V3 切换改动视为现有基线。

## 判定规则与证据

- 生产调用链：`ExtractionStage → build_compare_document_extractor → ContractExtractionV3`。旧 extractor、旧 HTTP client、`/layout-parsing` 在 `backend/app/` 中引用数为 0；正式 V3 测试和 `test_no_ppstructure_extraction.py` 覆盖该入口。
- `backend/app/config.py` 与两份 `.env.example` 中，`PPSTRUCTURE_*`、`LAYOUT_PARSING_*`、`COMPARE_EXTRACTOR`、`HYBRID_EXTRACTOR`、`NATIVE_FAST_PATH`、`NATIVE_PAGE_MODE` 声明数为 0。本机 `.env` 的旧键名扫描结果也是 0；Settings 的 `extra="ignore"` 与测试确保外部遗留变量不进入运行配置。
- 本地 `storage/tasks.sqlite3` 的 `tasks` 表有 7 条记录，`details_json` 中含 `ppstructure` 的也是 7 条。此项是历史读取兼容的实证，不代表生产数据库计数。
- `output/`（31 GiB）与 `storage/`（464 MiB）是本地生成结果和任务数据；按文件名扫描有 618 个路径含 `ppstructure`/`layout-parsing`，其中包括冻结旧源码副本和 raw 响应。它们不作为源码删除对象，保留原始标签；不能把海量 raw 响应逐行命中误算为生产引用。数据库、任务目录和备份未改动。
- 当前生产版本尚未实际部署。旧 PP-Structure 容器、镜像、模型及 rollback 配置保留，只有部署后 `/layout-parsing=0` 的真实观察窗口结束才可停服。

## 分类明细

表中 `symbol` 是该路径命中的关键词或字段；`current callers` 表示现有调用或引用关系，不把历史文档链接误算为运行调用。`keep` 表示保留，`archive` 表示移出主执行目录，`delete` 表示删除。清理后状态见最终报告。

| 类别 | path | symbol | current callers | replacement | 处理 | reason |
| --- | --- | --- | --- | --- | --- | --- |
| A production runtime | `backend/app/services/pipeline_stages.py`、`backend/app/services/extractors/factory.py`、`backend/app/services/extractors/registry.py` | extraction factory / named registry | compare pipeline；registry 在生产和正式测试的调用数为 0 | `ContractExtractionV3` | migrate/delete | 删除无消费者的 named registry/auto 选择器；比较入口只创建 V3。 |
| A production runtime | `backend/app/services/models/{base.py,device.py,registry.py,remote_model.py}` | `ModelProtocol`、`detect_device`、`ModelRegistry`、`RemoteModel` | 0（四模块互相引用，但无外部调用） | V3 远端 Layout client / 现有 OCR client | delete | 旧模型编排基类与设备/HTTP 包装器；正式测试无依赖。 |
| A production runtime | `backend/app/services/ocr_routing.py` | `page_routing`（注释） | V3 OCR router | `contract_v3` 路由 | migrate | 注释指向已删除模块，应纠正。 |
| B production config | `backend/app/config.py`、`backend/app/config_models.py`、`backend/.env.example`、`.env.example` | legacy env keys、`document_extractor`、`pymupdf_min_text_chars`、`model_registry_preload` | 后三项生产消费者为 0 | `PPDOCLAYOUT_*` | delete | 旧字段已不存在；移除无效 selector/registry 配置，新增死配置与旧环境变量无效测试。 |
| C tests | `backend/tests/test_no_ppstructure_extraction.py` | old extractor names、`/layout-parsing` | pytest | V3 入口 | keep/migrate | 回归守卫，扩展禁止旧配置消费。 |
| D scripts | `backend/scripts/replay_reviewed_tasks.py` | `PPStructureExtractor`、`PPStructureOCRHybridExtractor` | `benchmark_compare_*` 的 saved 模式 | V3 live benchmark | delete | 已删类使重建模式不可执行；无正式测试直接调用。 |
| D scripts | `backend/scripts/benchmark_compare_performance.py`、`backend/scripts/benchmark_compare_concurrency.py` | `_ComponentReplayExtractor` | 手工 benchmark、`test_benchmark_support.py` 检查 CLI | V3 live benchmark | migrate | 移除不可执行的 saved 模式，保留 live/replay HTTP 能力。 |
| D scripts | `backend/scripts/audit_ocr_routing_waste.py` | `page_routing` | 0 | `benchmark_ocr_routing.py` | archive | 已删模块导入导致不可执行。 |
| D scripts | `backend/scripts/ppstructure_layout_only_equivalence.py`、`backend/scripts/ppstructure_layout_probe.py`、`backend/scripts/harvest_layout_ground_truth.py`、`backend/scripts/perf_trace_service_probe.py` | old extractor/config/endpoint | 0 | V3 benchmark/smoke | archive | 旧模型诊断，有历史解释价值；不应留在当前脚本目录。 |
| D scripts | `backend/scripts/replay_candidate_golden_v2.py`、`backend/scripts/verify_textin_alignment.py` | historical raw path | `test_candidate_golden_v2.py`、旧基准回放 | Golden V3 | keep | 读取冻结历史结果，仍有正式测试依赖；不构成生产写入。 |
| D scripts | `backend/scripts/perf_trace.py`、`backend/scripts/perf_trace_ledger.py` 及同组脚本 | old timing labels | 0（旧 tracer 自用） | V3 metrics | archive | 一次性性能追踪；旧标签与计算脚本一起归档用于解释既有结果。 |
| D scripts | `backend/scripts/smoke_ppdoclayoutv3_cutover.py` | `/layout-parsing` | V3 smoke | request guard | keep | 负向断言，命中旧字符串是禁止调用证据。 |
| E deployment | `docker-compose.yml`、`deployment/ppdoclayoutv3/`、运行中 PP-Structure 资产 | old service mention | rollback | PP-DocLayoutV3 image | keep | 生产观察窗口未完成；只标 deprecated，不删镜像或 compose。 |
| G historical data compatibility | `backend/app/models.py` | `ppstructure` raw path/source discriminator | task JSON reads | migration after historical data conversion | keep | 本地 7/7 历史记录含旧值。 |
| G historical data compatibility | `backend/app/services/{source_consensus,document_role_repair,document_preparation,diff_quality,header_footer_compare}.py` | `source == "ppstructure"`、历史 source tag | downstream historical task reads | future data migration | keep | 旧数据解释与来源证据，不能由新 V3 写入。 |
| G historical data compatibility | `backend/app/services/signing_region/extractor.py`、`backend/app/services/table_compare/parser.py` | old HTML/source label | historical document/table processing | V3 table adapter for new data | keep | 保留旧任务的 HTML 和签署证据解释。 |
| C tests | `backend/tests/test_*` 中含 `ppstructure` 的业务测试 | historical source fixture | pytest | V3 contract tests + historical read fixtures | keep | 检查标题、表格、签署、证据、diff 和旧数据读取，不是旧 extractor 实现测试。 |

以下 F/H 路径逐文件列出，避免把文档证据、实验脚本误认成运行依赖。

| F docs | `docs/reports/challenger-ae-gpu-benchmark-20260924.md` | `layout-parsing`, `PP-Structure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/challenger-d-scan-performance.md` | `PP-Structure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/contract-comparison-performance-diagnosis-20260921.md` | `layout-parsing`, `PP-Structure`, `ppstructure`, `PPStructure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/contract-comparison-performance-phase2-20260921.md` | `NATIVE_FAST_PATH` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/extraction-architecture-analysis-20260921.md` | `layout-parsing`, `PP-Structure`, `ppstructure`, `PPStructure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/extraction-challenger-business-equivalence.md` | `hybrid extractor`, `PP-Structure` | historical citation only | V3/Golden evidence | keep | 当前 Golden/业务证据及其复现脚本仍依赖。 |
| F docs | `docs/reports/extraction-challenger-full-golden-hard-fix.md` | `layout-parsing`, `PP-Structure` | historical citation only | V3/Golden evidence | keep | 当前 Golden/业务证据及其复现脚本仍依赖。 |
| F docs | `docs/reports/extraction-challenger-full-golden.md` | `layout-parsing`, `PP-Structure` | historical citation only | V3/Golden evidence | keep | 当前 Golden/业务证据及其复现脚本仍依赖。 |
| F docs | `docs/reports/extraction-challenger-minimal-proof-20260921.md` | `layout-parsing`, `PP-Structure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/extraction-challenger-v3-20260923.md` | `layout-parsing`, `PP-Structure`, `PPStructure` | historical citation only | V3/Golden evidence | keep | 最终 V3 benchmark 或当前部署验证，仍可复现。 |
| F docs | `docs/reports/layout-model-selection-20260923.md` | `PP-Structure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/layout-server-batch-parallel-20260924.md` | `PP-Structure`, `ppstructure` | historical citation only | V3/Golden evidence | keep | 最终 V3 benchmark 或当前部署验证，仍可复现。 |
| F docs | `docs/reports/native-extraction-gap-20260921.md` | `native_fast_path`, `ppstructure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/ocr-model-identity-impact-20260921.md` | `layout-parsing`, `PP-Structure`, `ppstructure`, `PPStructure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/ppdoclayoutv3-batch-deployment-20260924.md` | `PP-Structure` | release/rollback record | V3/Golden evidence | keep | 现行切换、部署或回滚证据。 |
| F docs | `docs/reports/ppdoclayoutv3-batch-sdk-research-20260924.md` | `PP-Structure` | historical citation only | V3/Golden evidence | archive | 选型、性能或旧模型诊断；不是当前运维说明。 |
| F docs | `docs/reports/ppdoclayoutv3-gpu-deployment-20260923.md` | `PP-Structure`, `ppstructure` | historical citation only | V3/Golden evidence | keep | 最终 V3 benchmark 或当前部署验证，仍可复现。 |
| F docs | `docs/reports/ppdoclayoutv3-production-cutover-manifest.md` | `PP-Structure`, `ppstructure`, `PPSTRUCTURE` | release/rollback record | V3/Golden evidence | keep | 现行切换、部署或回滚证据。 |
| F docs | `docs/reports/ppdoclayoutv3-production-cutover.md` | `hybrid extractor`, `layout-parsing`, `page_routing`, `PP-Structure`, `PPStructure`, `ppstructure`, `PPSTRUCTURE` | release/rollback record | V3/Golden evidence | keep | 现行切换、部署或回滚证据。 |
| F docs | `docs/reports/ppdoclayoutv3-vs-ppstructurev3-20260924.md` | `PP-Structure`, `ppstructure` | historical citation only | V3/Golden evidence | keep | 最终 V3 benchmark 或当前部署验证，仍可复现。 |
| H experiments | `experiments/challenger_ae_gpu_benchmark/report.py` | `layout-parsing`, `PP-Structure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/challenger_ae_gpu_benchmark/run_one.py` | `layout-parsing`, `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/challenger_d_scan_performance/controller.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/challenger_d_scan_performance/report.py` | `PP-Structure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/challenger_d_scan_performance/run_one.py` | `layout-parsing`, `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/challenger_d_scan_revalidation_20260924/run_one.py` | `layout-parsing`, `ppstructure` | historical experiment only | V3/Golden evidence | keep | 当前部署重验或最终 V3/旧服务对照 benchmark 的复现工具。 |
| H experiments | `experiments/contract_ab/run_offline_benchmark.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/contract_ab/run_paddleocr_vl.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/e2e_diagnosis/export_timeline.py` | `layout-parsing` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/e2e_diagnosis/report.py` | `layout-parsing`, `PP-Structure`, `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/extraction_challenger/README.md` | `layout-parsing`, `PP-Structure` | historical experiment only | V3/Golden evidence | keep | 当前 V3/Golden 或性能证据复现工具；无生产入口。 |
| H experiments | `experiments/extraction_challenger/audit_baseline.py` | `PP-Structure`, `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧性能/基准诊断，不再在主执行目录运行；冻结证据保留。 |
| H experiments | `experiments/extraction_minimal_proof/finalize.py` | `layout-parsing` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/extraction_minimal_proof/probe_identity.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/extraction_minimal_proof/run_layout_only.py` | `PP-Structure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/extraction_minimal_proof/run_oracle.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/extraction_minimal_proof/run_routes.py` | `PP-Structure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/full_golden/analyze.py` | `layout-parsing`, `PP-Structure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/full_golden/collect.py` | `PP-Structure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/full_golden/extract.py` | `layout-parsing` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/full_golden/report.py` | `layout-parsing`, `PP-Structure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/full_golden/request_audit.py` | `layout-parsing`, `ppstructure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/full_golden_hard_fix/finalize.py` | `layout-parsing`, `PP-Structure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/layout_decoupling/README.md` | `PP-Structure`, `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_decoupling/baseline.py` | `ppstructure`, `PPStructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_decoupling/candidates.py` | `ppstructure`, `PPStructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_decoupling/common.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_decoupling/delivery.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_decoupling/finalize_evidence.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_decoupling/secondary.py` | `ppstructure`, `PPStructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_decoupling/tests/test_contract.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧候选或诊断实验，无生产入口。 |
| H experiments | `experiments/layout_server_batch_parallel/run.py` | `layout-parsing` | historical experiment only | V3/Golden evidence | archive | 最终报告与冻结结果保留；旧服务对照调用只在归档脚本中。 |
| H experiments | `experiments/layout_service_comparison/run.py` | `layout-parsing`, `ppstructure` | historical experiment only | V3/Golden evidence | archive | 最终报告与冻结结果保留；旧服务对照调用只在归档脚本中。 |
| H experiments | `experiments/layout_v3/README.md` | `PP-Structure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/layout_v3/contract.py` | `ppstructure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/layout_v3/infer.py` | `PPStructure` | current V3/Golden reproduction | V3/Golden evidence | keep | 最终 V3 或 Golden 证据复现。 |
| H experiments | `experiments/performance_phase2/remote-timing-contract.md` | `layout-parsing` | historical experiment only | V3/Golden evidence | archive | 旧性能/基准诊断，不再在主执行目录运行；冻结证据保留。 |
| H experiments | `experiments/performance_phase2/run_alignment.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧性能/基准诊断，不再在主执行目录运行；冻结证据保留。 |
| H experiments | `experiments/performance_phase2/run_native.py` | `NATIVE_FAST_PATH` | historical experiment only | V3/Golden evidence | archive | 旧性能/基准诊断，不再在主执行目录运行；冻结证据保留。 |
| H experiments | `experiments/performance_phase2/run_pipeline.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧性能/基准诊断，不再在主执行目录运行；冻结证据保留。 |
| H experiments | `experiments/performance_phase2/run_score_cutoff.py` | `ppstructure` | historical experiment only | V3/Golden evidence | archive | 旧性能/基准诊断，不再在主执行目录运行；冻结证据保留。 |


## 当前工作区逐路径引用补充

归档和删除后的路径单独列出；旧路径与处置仍以上方清理前快照为准。

| 类别 | path | symbol | current callers | replacement | 处理 | reason |
| --- | --- | --- | --- | --- | --- | --- |
| A/G runtime/compat | `backend/app/models.py` | `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| A/G runtime/compat | `backend/app/services/diff_quality.py` | `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| A/G runtime/compat | `backend/app/services/document_preparation.py` | `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| A/G runtime/compat | `backend/app/services/document_role_repair.py` | `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| A/G runtime/compat | `backend/app/services/header_footer_compare.py` | `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| A/G runtime/compat | `backend/app/services/signing_region/extractor.py` | `PP-Structure`, `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| A/G runtime/compat | `backend/app/services/source_consensus.py` | `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| A/G runtime/compat | `backend/app/services/table_compare/parser.py` | `ppstructure` | 历史任务读取/下游处理 | V3 新写入 | keep | 历史来源和 raw path 兼容；不发旧 HTTP。 |
| D scripts | `backend/scripts/replay_candidate_golden_v2.py` | `ppstructure` | 手工工具 | V3 工具或历史结果回放 | keep | 负向调用守卫或历史结果只读；不在生产入口。 |
| D scripts | `backend/scripts/smoke_ppdoclayoutv3_cutover.py` | `layout-parsing`, `PP-Structure` | 手工工具 | V3 工具或历史结果回放 | keep | 负向调用守卫或历史结果只读；不在生产入口。 |
| D scripts | `backend/scripts/verify_textin_alignment.py` | `ppstructure` | 手工工具 | V3 工具或历史结果回放 | keep | 负向调用守卫或历史结果只读；不在生产入口。 |
| C tests | `backend/tests/test_contract_extraction_v3.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_document_identity_regressions.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_eight_task_body_ownership.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_golden_v3_root_repairs.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_header_footer_compare.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_holdout_root_regressions.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_legacy_config_audit.py` | `layout-parsing`, `NATIVE_FAST_PATH`, `PPSTRUCTURE` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_no_ppstructure_extraction.py` | `layout-parsing`, `PPStructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_report_generator.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_representation_root_repairs.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_signing_region_comparison.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_signing_region_extractor.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_signing_region_value_ocr.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_six_task_root_repairs.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_table_compare_structured.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_table_html_parser.py` | `PPStructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_text_evidence.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| C tests | `backend/tests/test_three_task_root_cause_repairs.py` | `ppstructure` | pytest | 业务契约/V3 守卫 | keep | 业务或历史读取测试；非已删实现单测。 |
| E deployment | `deployment/ppdoclayoutv3/README.md` | `layout-parsing`, `PP-Structure` | 运维文档 | V3 部署 | keep | 旧服务部署资产保留用于回滚。 |
