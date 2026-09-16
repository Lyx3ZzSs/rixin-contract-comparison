# Golden v2.1：更新汉能、随州实际任务来源

依据用户明确指定的两份任务更新：

| Golden | 实际完成任务 | 断言验收 |
|---|---|---|
| HANENG_GONGHE | 0e05d520-bfac-4a16-9a5d-8f7de028b8c0 | 7/7 通过 |
| SUIZHOU_CHANGYUAN | 34f791e2-7ae5-4c52-9304-391abbe87d77 | 83/83 通过 |

本次直接读取数据库中 COMPLETED 的最终结果，并核对任务输入 PDF 与 Golden 的 SHA-256。没有使用先前关闭语义匹配的简化离线回放替代实际结果，也未重新执行在线 OCR。

## 更新内容

- manifest 的两份 `source_run_id` 更新为上述任务；`previous_source_run_id` 和 `source_run_history` 保留来源链。
- 汉能：原件空白、扫描存在手写填写。允许确认“新增填写”，同时要求候选值为“手写填写（姓名未可靠识别）”、内容状态 NEEDS_REVIEW，以及说明“新增手写填写；内容识别待确认”。原定位框和姓名不可错误确认的负例均保留。确认填写存在不等于确认姓名。
- 随州：第33页“彭丹→黄科”的真实内容不变，审核状态采用实际任务的 NEEDS_REVIEW，保留明确姓名替换正例。第4页“双方义务”标题新增已被实际任务过滤，继续保留 MUST_NOT_EXIST 防回归；第11页一加下划线的误报负例也保留。
- 未将额外待复核候选自动写成真值；未修改业务算法、matcher 或位置阈值。

基准版本 `contract-golden-v2.1`，仍为10份合同、199条断言、20个相同PDF。其余八份 oracle 不变；本次不能描述成十份实际任务已重新运行并全部通过。

## 验证

两份实际任务共90条断言通过；全部20个Golden PDF的哈希及页数检查通过；13项基础设施和失败注入测试通过。

可审计产物：`output/analysis/golden-v2.1-update/actual-task-validation.json`、两份以任务ID命名的实际结果快照、`before-fixture/`、`baseline-change.log`。快照保留任务完成状态、时间、revision、输入哈希和实际差异，仅作为本次验收证据，不是在线OCR重跑。

v2 的 `output/analysis/eight-task-fix/release/` 为旧配置、旧任务来源的历史回放，不能直接作为 v2.1 当前任务的结果；相关历史说明保留在 UPDATE-v2.md。
