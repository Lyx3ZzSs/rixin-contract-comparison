# 合同比对性能第二阶段实验报告

本报告随实验更新。原始证据保存在 `output/analysis/perf-phase2-20260921/`；未完成的验收明确标记，不将组件测量换算成实际 E2E。

## 实验约束与当前质量门槛

固定第一阶段 `cases.json` 的 A–E 文件及 SHA256，保持点击开始到全部事件 DOM、两个可见 PDF canvas 和两帧的 E2E 口径。远端实验按 baseline/candidate 交替三轮；P50 比较全部成功轮次，超时和失败保留。每个变量独立 commit。用户开始任务前已有的大量暂存删除和无关工作区改动未纳入提交。

Golden 原样执行 15 份合同 / 255 项断言，耗时 576.07 秒，得到 **55 项失败：36 FALSE_NEGATIVE_REGRESSION + 19 DECISION_REGRESSION**。记录位于 `golden-phase0/summary.json` 和逐合同报告。原提交 `c9862535` 的历史报告 `docs/reports/comparison-optimization-implementation-20260916.md` 已记录相同失败总数与分类；这说明不能将本轮 55 项失败直接归因于性能候选，但尚不能以总数相同证明每一项历史失败完全一致。

因此本轮不能声称“完整 Golden 已通过”，也不能将仅通过差分检查的候选标记为已满足上线条件。未修改 Golden、业务阈值或签章分类逻辑。

## Optimization 0：OpenCV 兼容修复

- Root cause：`HoughLinesP` 的 OpenCV 5 返回 `(N,4)`，旧代码按 `(N,1,4)` 索引。
- Code changed：`backend/app/services/ruled_table_recovery.py` 将遍历视图改为 `segments.reshape(-1, 4)`；独立测试覆盖两种 shape，检查坐标、顺序、dtype 与完整恢复结果。
- Semantics：只调整数组视图，不排序、不转换 dtype，不修改 ruled-table 规则。
- Baseline：新 shape 的测试在修复前报 TypeError；旧 shape 通过。
- Candidate：相关 61 项测试通过；真实 D 在未设置 `DIAG_OPENCV4_SHAPE` 时完成，79.167 秒、12 diff / 12 events；完整 diff 对象与第一阶段成功 D 完全一致。
- Golden：见全局质量门槛，55 项失败；不能称为通过。
- Risk / recommendation：KEEP 兼容修复，不计入性能收益。commit `09539e81`。

## Optimization 1：完成动画与结果数据解耦

- Root cause：终态确认后等待 `RESULT_COMPLETION_ANIMATION_MS=1200` 才发布 task，阻止 ResultPage 请求 events。
- Code changed：`frontend/src/lib/hooks.ts` 立即发布经 revision 确认的 terminal task，另维护可取消的 `completionAnimating`；ResultPage 保留进度动画，完成时作为不占布局的提示独立显示。
- Semantics：终态确认、SSE/polling fallback、revision 检查保留。task 切换或卸载会取消旧动画。事件请求不再依赖动画。
- Tests：30 项相关前端测试和 production build 通过；新增 SSE、polling、取消、单次 GET、动画结束不重建结果/PDF 节点检查。
- Benchmark：`animation/comparison.json` 汇总完整交替实验；当前 A/B 各三组均将 completed→events 从约 1.204/1.205 秒降至 0.003 秒。A E2E P50 16.151→15.160 秒，B 101.094→98.945 秒。其他 E2E 差值不能全部归因于动画，远端等待有波动。
- Evidence：逐字段比较完整 diff、事件快照、页映射、证据与 char_boxes。事件 ID 的生成包含 task_id；跨任务原始 ID 差异单独保留，只对任务命名空间建立双射比较，不归一化 diff/audit 标识、内容或坐标。
- Golden：前端候选未修改后端算法，完整 Golden 门槛仍未通过。
- Status：A–E 浏览器矩阵正在完成，尚未给出最终 KEEP 结论。

## Optimization 2：现有 Native 路径配置实验

实际 key 为 `COMPARE_NATIVE_FAST_PATH_MODE` 与 `COMPARE_NATIVE_PAGE_MODE`，支持 off/shadow/enabled。整文件 fast path 在 ExtractionStage 优先执行；命中后不走 OCR/layout。未命中才进入 extractor，包括 page routing。因此“两者 ON”是优先整文件、再尝试逐页的组合，不是两个独立加速倍率。

整文件 gate 检查每页最少字符、字符坐标覆盖、可疑字符、多栏、表格、签章线索、图片、旋转、表单、注释及复杂矢量。保留 native 字符坐标，但 block/layout 来源改变。默认关闭属于保守试点；不能从名字推断其等价。

本地 gate 实测：A/B 可接受；C 两侧被复杂矢量、表格/图片、低文本、多栏等条件拒绝；D 扫描侧拒绝；E 也未通过。故不能预先声称整文件 fast path 会加速 C。配置矩阵仅改变开关；首次发现业务字段或证据差异就停止该配置，保留差异路径，不启用生产默认。

## Optimization 3：远端服务分项计时

现有客户端 `remote_metrics.py` 已解析 Server-Timing；实际响应无可用分项。当前仓库未找到所调用的 Paddle OCR/layout 服务实现或部署入口。字段与时序定义见 `experiments/performance_phase2/remote-timing-contract.md`。服务端 queue / inference / GPU 占比均为 **未测得**，没有增加并发或 worker。

## Optimization 4：强锚点上界与精确 DP 剪枝实验

- Root cause：B 原 DP 的实际 `_text_ratio` cache miss 为 236,720。
- Experiment：唯一且有序的稳定 prefix/suffix 锚点，加原 DP 求 gap，先构造可行路径的代价上界；锚点不强制成为最终配对。原 DP 转移、relax 次序和回溯保留。无锚点、重复、冲突、低置信时 FULL_DP_FALLBACK。
- Proof：match 代价至少 0.05、消耗两页；group 至少 0.22、消耗三页；gap 至少 0.42、消耗一页。所以剩余页数乘 0.025 是可采纳下界。只有 current+lower 严格超过可行上界并留出 tie 浮点余量时跳过状态。直接强制 exact anchor 存在反例，已加入测试。
- Baseline / candidate：B 交替三轮阶段耗时 15.111/15.149/15.136 秒，对应 0.420/0.419/0.441 秒；P50 15.136→0.420 秒，减少约 97.2%。CPU 也随之下降。
- Calls：similarity miss 6320→235，anchor miss 1600→79，text_ratio miss 236720→11611，强锚点40、剪枝状态1640、fallback 0。
- Semantics：基线重建的完整 debug mapping 与第一阶段保存值一致；候选三轮映射一致。测试包含 same/insert/delete/split/merge/reorder/header-footer/near-duplicate 和80组固定种子编辑序列。
- Risk / status：目前只在 experiments 中使用，未修改生产 DP。上述数字仅为 B 阶段测量，其他 A–E、完整 pipeline 和 Golden 仍需验收，不能据此宣布上线。

## Optimization 5：Candidate Scoring 精确 cutoff

评分并非单一线性公式。`body=max(ratio,capped_token,capped_partial)` 后还包含阈值分支、身份加分、风险封顶、semantic/rerank；原始 partial 又被接受规则、匹配类型与公开候选诊断读取。仅因当前总分不会改变就填0，会改变业务结果。

实验从 body 公式导出 cutoff=max(ratio,capped_token)，调用原 RapidFuzz；cutoff 未命中时必须回到无 cutoff 的原始分数。保持候选域、所有原始评分、排序与配对，不降低 rerank 数量。探索性预模型域的4,158候选三轮P50为11.837→6.241秒，原始评分、完整排序和配对一致，416次cutoff miss均重新物化原分数。该初版构造器未带当前默认 exact-match 策略，不能作为第一阶段1,331次调用的正式收益。入口已改为读取现有配置；真实 pipeline 对照另行进行，原始探索结果保留在 scoring/。

条款长度：Golden两侧30份split诊断共2,969条，P50=53、P90=164、P95=257、P99=759、max=3,675字符（尾部分位nearest-rank）。74,209字符是合成病理样本，未据此设定正文截断阈值。

## Optimization 6：私有持久 extraction cache 试点

缓存 key 包含 file SHA256、extractor/OCR/layout/pipeline 版本、相关配置 hash、schema 版本；用户/租户 scope 对目录隔离。0700目录、0600文件、原子写入、TTL，读取后重新构造完整类型对象。raw artifact 缺失时 miss，防止返回已删除证据路径。

测试覆盖 cold miss、warm hit、修改文件、配置/模型/代码版本变化、scope变化、过期、对象互不污染，以及相同内容改名后 path/filename 重绑定。序列化 extraction 全字段相等。

部署阻断：真实服务模型版本尚无可验证来源；本地试点显式标为 UNVERIFIED session，不得作为生产版本。还需确定任务删除后 artifact 保留策略、加密/清理和多进程 stampede 控制。只讨论重复文件收益，不声称首次上传提速。

## Optimization 7：events evidence 字典试点

兼容方向为新 wire version，旧 API 不删除字段。仅将事件 diff/children 内的 original/compare evidence 改为完整内容 SHA256 引用，保留顺序、重复引用、空数组；解码按引用深复制，还原 v1 的独立对象语义。碰撞和悬空引用报错。

消费者映射：backend event_response→DiffItem/EvidenceBox→ResultPage 的事件投影/children→PdfDocumentViewer 的定位和高亮；event_id/audit_item_ids→审核；backend event_response/report generator→导出。试点不改变这些消费者看到的 v1 内容。

真实保存的 A–E 响应各做三轮交替 encode，所有候选还原后与完整 v1 对象相等。E 15,500,259→9,840,018 bytes（−36.5%）；后端 build+pack+model encode+JSON encode 的 P50 2.027→1.919秒。A/C 无事件时多37bytes，B/D 分别减少25,349/5,338bytes。E 新增去重约167ms，model encode 从673→320ms，JSON encode 从127→72ms；不能忽略解引用成本。

状态：尚未接入生产 API/前端。浏览器 fetch/parse/heap 与逐事件定位、高亮、审核、导出仍需验收；暂不建议默认上线。

## 最终验收状态

### A. 已证实可以上线的优化

当前没有候选满足“完整 Golden 必须通过”的全局门槛。OpenCV 修复可独立审查，且不计性能收益。待 A–E 矩阵结束后补充最终表格与 commit。

### B. 有性能收益但存在语义差异、或验收尚未完成的实验

页对齐 B 组件和 events E 组件已测得收益，但全链路与 Golden 验收未完成；不可混入 A 类。Native 以实际逐字段差异决定停止；缓存只为私有试点，真实模型版本未知。C-pathological 将独立设300秒硬超时，超时只报告下界，不冒充完成耗时。
