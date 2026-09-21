# 第二阶段：单变量性能实验

输入固定为 `output/analysis/e2e-20260921/cases.json` 的 A–E；运行前校验 SHA256，禁止重建输入。第一阶段报告已从原始保存数据通过原 report.py 恢复。

- Phase 0 只做兼容修复，不计速度收益。后续入口拒绝 `DIAG_OPENCV4_SHAPE`。
- 每项先提交测试和一个独立候选；远端相关耗时使用 baseline/candidate 交替三轮，每轮保留全部失败与超时。独立运行目录、固定端点/配置/依赖，前端使用 production build。
- E2E 沿用第一阶段真实点击→全部事件 DOM + 两个可见 PDF canvas + 两帧。独立后端实验明确标记 `pipeline-only`，不冒充 E2E。P50/P95(nearest-rank)/max 全部保留；n=3 的 P95 不是生产尾延迟估计。
- 比较绝对差与百分比：E2E、目标阶段、CPU、RSS、远端次数/区间并集、events 字节、diff/event 数，并逐字段比较页映射、条款配对、分类、证据、char_boxes、事件投影与审核标识。只排除明确列出的 task/path/timing 等运行身份字段，不忽略业务差异。
- Golden 固定 contract-golden-v3，15 份合同 / 255 项断言；不修改基线。不把单元测试、diff 数量相同或离线快照验证声称为 live Golden。
- 无收益或语义回退：REVERT/不启用，保留实验与差异证据。远端服务不提供内部时间则记 unknown，不归因 GPU。
- 每项独立 commit，使用显式文件列表，保留任务开始前用户暂存删除与无关未提交内容。
- C-pathological 每次硬超时 300 秒（另记超时阶段及下界），不得无限运行。普通 case 每次上限 600 秒。

输出目录：`output/analysis/perf-phase2-20260921/`。诊断脚本按阶段记录代码 SHA256 与输入 SHA256。缓存实验单列 cold miss/warm hit，首次上传与重复上传分开报告。

## 复现入口

从仓库根目录使用 `backend/.venv/bin/python`。实验模块只通过独立进程注入，不修改生产默认配置；禁止同时设置两个优化变量。

- `run_browser_pairs.py --space <已有Ego TaskSpace id>`：既有production baseline/candidate构建的A–E交替真实浏览器实验。端口4179/4180与8019/8020必须空闲；固定构建位于本轮output，不静默重新构建baseline。
- `run_native.py`：优先B的三个配置，出现block/section语义退化立即终止该配置。
- `run_cpu_pipelines.py`：页对齐B三组、评分B三组分别交替；病理单对及完整pipeline分别每进程300秒上限。输出目录必须尚不存在，避免覆盖证据。
- `run_alignment.py` / `run_alignment_matrix.py`：冻结提取结果重建原准备流程；先断言baseline与保存的page mapping一致，再做候选全字段比较。只计组件时间。
- `run_score_cutoff.py`：前模型组件隔离对照，两侧禁用provider；不作为真实E2E。
- `run_golden_candidate.py reference|alignment|scoring`：原样执行15合同/255断言，另保存完整pipeline语义快照。`summarize_golden.py`比较失败项及快照，不以失败总数代替逐字段比较。
- `run_cache.py`：A小范围私有跨进程缓存；`PHASE2_CACHE_EXPERIMENT_OUTPUT`可指定新输出目录。显式UNVERIFIED session版本仅用于试点，不是生产模型版本。
- `run_event_wire.py`、`browser_event_wire.mjs`：保存的A–E全响应序列化/解码等价及真实浏览器fetch/parse/hydrate；不替代UI审核/导出验收。
- `summarize_pipeline.py <目录>`、`summarize_final.py`：从原始spans与browser数据生成数值表，不改变计时边界。

实验测试：`PYTHONPATH=backend:experiments/performance_phase2 backend/.venv/bin/python -m pytest -q experiments/performance_phase2/test_*.py`。当前模型服务仍不提供内部时序，客户端等待只能记为远端请求墙钟时间。
