# PP-DocLayoutV3 生产代码替换报告

记录日期：2026-09-24（Asia/Shanghai）。**代码切换及本机联调完成；生产部署和旧服务下线尚未完成。**

## 十项验收问题

1. **删除了哪些旧代码？** 删除 `PPStructureExtractor`、`PPStructureOCRHybridExtractor`、旧 page router、`PPStructureLayoutAdapter` 和仅供旧模型编排的 detector、recognizer、orchestrator、batch processor、setup；移除旧 factory、client、配置和 ExtractionStage 分支。保留通用 `Document`、`Page`、`TextBlock`、OCR 解析器，以及读取历史任务所需的旧来源字段。
2. **哪些业务逻辑迁入 V3？** Native 字符保全与版面归属、标题语义、阅读顺序、覆盖分析、混合页选择性 OCR、扫描页一次全页 OCR、OCR 字词证据、表格区域结构与安全回退、旋转坐标变换，以及必要的文本修复进入 `app/services/extractors/contract_v3/`。未把实验计时、对比 harness 或 raw dump 接入生产。
3. **生产调用链是什么？** `ExtractionStage → ContractExtractionV3 → NativeReader → PPDocLayoutV3Client → geometry merge → SemanticRoleResolver → coverage_plan → SelectiveOCRRouter → TableResolver → UnifiedArtifactAdapter → Page/TextBlock →` 原有下游 pipeline。Layout 固定单页调用、`dynamicBatch=false`、最多 4 个并发请求；扫描页 Layout 和一次 OCR 可并行。
4. **`/layout-parsing` 是否为 0？** 六次本机真实服务 smoke 的拦截计数均为 **0**；尚无生产主机日志访问，不能宣称生产观察窗口内为 0。
5. **Native / Mixed / Scanned 是否完成？** 是，B、D 的六次本机完整 pipeline 均运行到 `SummaryStage`，无错误；D 每次有 39 页 Native、1 页 Mixed、40 页 Scanned。直接调用的 task 对象状态仍显示 `PROCESSING`，因此尚未验证异步任务协调器将持久状态改为 `COMPLETE`。
6. **B / D 性能如何？** B warm 59.571 s，正式 59.617 / 58.278 s；D warm 80.543 s，正式 67.862 / 67.457 s。B Extraction 约 19 s，D 正式约 39 s；没有出现 150 s 级退化。
7. **测试多少项全部通过？** 定向 **139 passed**；覆盖本次代码的扩展集合 **1755 passed、9 skipped、1 deselected**。完整 `pytest` 在未设置 Golden 数据目录时为 **1774 passed、28 failed、9 skipped**；补充设置本地 Golden 数据目录后，15 份合同全部执行，正式 Golden 门槛 **1 failed，含 175 条业务断言失败**。因此“所有正式测试 PASS”**未满足**。
8. **如何 rollback？** 切换前已建立 tag `pre-ppdoclayoutv3-production-cutover`，指向 `48a23804c1273ef59deea775707047efc60ae339`；部署回退应使用原 backend 镜像和 release，旧 PP-Structure 镜像及配置需保留。当前缺少生产镜像 ID / release ID，详见[回滚清单](ppdoclayoutv3-production-cutover-manifest.md)。tag 不包含切换前已有的 154 项未提交改动。
9. **PP-Structure 服务是否下线？** **否，未执行停服**。生产代码尚未部署，生产 `/layout-parsing` 持续观察尚未进行；不能提前停旧服务。
10. **`PPDOCLAYOUTV3_PRODUCTION_PATH`？** **false（已接入本地生产代码入口，但尚未部署到生产运行时）**。

## 入口与删除清单

代码搜索覆盖 `PPStructure`、`PP-Structure`、`ppstructure`、`layout-parsing`、`PPStructureExtractor`、`PPStructureLayoutAdapter`、hybrid extractor。删除清单按引用关系核对后执行：

| 类别 | 处理 |
| --- | --- |
| 生产调用 / factory / client | `ExtractionStage`、extractor factory / registry、`clients.py` 和 app 生命周期只创建 V3；旧 `ppstructure.py`、`ppstructure_ocr_hybrid.py`、`page_routing.py` 删除。 |
| 旧模型实现与 adapter | `layout_detector.py`、`table_recognizer.py`、`orchestrator.py`、`batch_processor.py`、`setup.py` 删除；`layout_analysis.py` 仅保留通用区域类型与 flow role。 |
| 配置 | 删除 `PPSTRUCTURE_*`、旧 compare extractor 选择、旧 hybrid/native fast path 开关；加入 `PPDOCLAYOUT_ENDPOINT`、`PPDOCLAYOUT_TIMEOUT`、`PPDOCLAYOUT_MAX_INFLIGHT`、标题/默认阈值与 NMS。endpoint 来自环境，配置类不硬编码主机。 |
| 依赖 | Pillow 作为直接依赖声明；Paddle/OpenCV/PyMuPDF 仍由 OCR、表格、PDF 的现有消费者使用。没有确认可安全删除的 PP-Structure 专用第三方包。 |
| 测试 | 删除仅验证旧 extractor、旧 merge、旧 native fast path 的测试；将 Native、Mixed、Scanned、table、evidence、batch 禁用和旧接口禁用写入正式 V3 测试；业务 pipeline、标题和比较测试保留。 |
| 历史数据 / 实验 | `models.py` 的旧 raw path 字段和若干 `source == "ppstructure"` 判断只供历史任务/独立来源佐证，保留以避免改变旧任务解释；它们不能发起 `/layout-parsing`。`backend/scripts/replay_reviewed_tasks.py` 等历史重放/实验脚本仍引用已删除类，**旧重放命令已不能使用**，不属于生产调用链。 |

历史来源佐证依赖独立的 PP-Structure 读数。V3 不生成这种读数，因此相关修复规则会保守地跳过，不能把它们视为已完成业务等价迁移。尤其表格当前以 PDF 向量格线或 OpenCV 栅格 cell graph 判定，未接入 Challenger 的 SLANet_plus 冻结模型；无法确认结构时仅保留文字和页证据，不发布推测 HTML。这是业务验收尚未通过的具体限制。

## 本机正式 smoke

独立运行脚本对 HTTP 调用加守卫，遇到 `/layout-parsing` 即失败；使用真实 Layout/OCR 服务及现有后续比较 pipeline。原始逐次数据位于 `output/analysis/ppdoclayoutv3-production-cutover-smoke/*/smoke.json`。

| 合同 | Run | Pipeline s | Extraction s | 页路由 | Layout | OCR | `/layout-parsing` | 结果 |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |
| B | warm | 59.571 | 19.236 | Native 80 | 80 | 0 | 0 | COMPLETE |
| B | 1 | 59.617 | 19.512 | Native 80 | 80 | 0 | 0 | COMPLETE |
| B | 2 | 58.278 | 19.283 | Native 80 | 80 | 0 | 0 | COMPLETE |
| D | warm | 80.543 | 51.268 | Native 39 / Mixed 1 / Scanned 40 | 80 | 41 | 0 | COMPLETE |
| D | 1 | 67.862 | 39.703 | 同上 | 80 | 41 | 0 | COMPLETE |
| D | 2 | 67.457 | 39.386 | 同上 | 80 | 41 | 0 | COMPLETE |

D 的 40 页扫描侧提取曾单独测得 1 次真实 table region 结构处理；表格算法正确性仍以正式用例和业务 review 为限。`smoke.json` 中的 `COMPLETE` 指所有 stage 到达 SummaryStage，无 ERROR/timeout；它不是持久任务终态。

## 测试与未完成验收

定向测试：`tests/test_contract_extraction_v3.py`、`tests/test_no_ppstructure_extraction.py`、`test_pipeline.py`、`test_compare_integration.py`、`test_config.py`，共 139 passed。仓库禁用回归测试扫描生产 `app/`，禁止重新引入 `/layout-parsing` 或旧 extractor 类。Ruff 检查通过。

完整测试的 28 个失败：Golden 测试在未设置 `GOLDEN_CONTRACT_DATA_ROOT` 时失败（1）；当前工作区另有 matching feature（5）、matrix job/pipeline（7）、task observability（14）和既有缓存读数 fixture（1）失败。对应其他模块含切换前已有的未提交/未跟踪改动；本报告不擅自把它们归因于 V3，也不把它们隐去。扩展集合排除这些未就绪模块后为 1755 passed、9 skipped、1 deselected。

随后以 `storage/golden-intake/contract-golden-v3` 设置 `GOLDEN_CONTRACT_DATA_ROOT`，实际运行阻断性 Golden 测试。15 份合同的 PDF 与 manifest 校验通过，15/15 均执行；2 份无断言失败，13 份共 **175** 条失败：`FALSE_NEGATIVE_REGRESSION=80`、`DECISION_REGRESSION=43`、`UNEXPECTED_CONFIRMED_DIFF=51`、`BBOX_REGRESSION=1`。其中 `SUIZHOU_CHANGYUAN` 有 113 条。汇总保存在 `output/analysis/ppdoclayoutv3-production-cutover-smoke/golden-summary.json`。这是当前生产代码业务输出与冻结 Golden 不符的直接证据，**生产代码门槛未通过，须停止上线**；不能通过改写 Golden 预期或把 544 项待审自动判 PASS 来消除失败。

代码发布仍缺少可复现的独立提交/新镜像、旧 backend 与 PP-Structure 镜像和配置备份、生产 DB schema version、部署 release ID、生产日志观察及旧服务下线。连接 `sprixin@10.10.12.64` 时 SSH 密钥认证失败；只有 Layout/OCR 健康检查和本机 smoke 可验证。取得访问后应先补齐回滚清单，再发布固定镜像，观察明确窗口内 `/layout-parsing=0`，最后停旧服务并核对日志/结果。未执行任何生产部署或停服。

业务状态继续为 **544 `DEFERRED_BUSINESS_REVIEW`**，没有自动改为 PASS。`dynamicBatch` 和 `/layout-detection/batch` 仍未作为生产默认开启。

```text
PPSTRUCTURE_CODE_REMOVED = true          # 生产 app 的旧 Extraction 实现与客户端
PPSTRUCTURE_RUNTIME_DISABLED = false     # 未部署、未停服、无生产日志验证
PPDOCLAYOUTV3_PRODUCTION_PATH = false    # 本地入口已接线，生产尚未部署
READY_FOR_PRODUCTION_CODE_CUTOVER = false # 完整测试与可回滚发布点未就绪
READY_FOR_BUSINESS_ACCEPTANCE = false
```
