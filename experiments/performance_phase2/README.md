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
