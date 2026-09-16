# TextIn 配对与差异合并受控实验

本实验直接调用 TextIn 官方合同比对 API；不调用本地兼容接口，不写入业务数据库。合成输入、预先记录的编辑操作和完整响应共同构成证据。

## 实验设计

每组使用同一对 PDF，分别提交 `merge_diff=0` 和 `merge_diff=1`，首轮共 12 组、24 个任务。全部保留印章、符号、页眉页脚、批注和水印，启用 PDF 解析。固定随机种子交错执行开关条件。

| 编号 | 输入变化 | 主要观察 |
|---|---|---|
| C00 | 完全相同字节 | 阴性对照是否出现变化 |
| M01 | 同一行金额与期限两处变化 | 一个顶层事件还是两个；未改文字是否被包含 |
| M02 | 连续新增两个视觉段落 | 是否跨段合并；是否保留两段位置 |
| M03 | 两个新增段落间隔一段未改文字 | 未改锚点是否阻断合并 |
| M04 | 同一视觉段落的两行各改一个数字 | 行边界是否限制合并 |
| M05 | 同一表格行的两个单元格变化 | cell、row、table 的事件归属和内层差异 |
| P01 | 两个独特段落换序 | 跟随内容、固定槽位，或拆成新增删除 |
| P02 | 换序后再修改一个段落 | 原段落身份能否恢复；修改是否被吞入整段增删 |
| P03 | 三个完全相同段落，仅中间改值 | 修改是否落在两侧中间段落；有无错配和重复 |
| P04 | 主体标签固定，两个金额互换 | 金额是否仍归到同一主体 |
| P05 | 文字和顺序不变，将一段移到下一页 | 跨页是否保留一致性 |
| P06 | 表格文字与顺序不变，将末行放到下一页 | 跨页续表是否被拆成增删 |

M02/M03 的新增文字相同，但未改锚点位置不同，不能把它们的差异归结为纯空白距离阈值。P02 相对 P01 只追加一处“两年→三年”变更。所有 PDF 均无合同真实数据。

## 执行

在仓库根目录使用现有 `.venv/bin/python`。凭据从现有环境变量或指定 `.env` 文件读入：`TEXTIN_APP_ID`、`TEXTIN_SECRET_CODE`。不要提交凭据文件。

```bash
.venv/bin/python experiments/textin_pairing_merge/probe.py verify-inputs
.venv/bin/python experiments/textin_pairing_merge/probe.py run --cases C00_identical,M01_same_line
.venv/bin/python experiments/textin_pairing_merge/probe.py run
.venv/bin/python experiments/textin_pairing_merge/probe.py analyze
```

使用其他现有凭据文件时传 `--env-file /absolute/path/to/file`。`run` 会消耗现有 TextIn 服务额度，默认首轮最多 24 个任务，已成功的条件会跳过。可针对值得复核的条件使用 `--cases P01_swap --repeats 3`，补齐该条件第 2、3 次。

创建请求前持久化发送意图；已取得任务 ID 时只继续轮询。创建结果不确定时停止，避免自动重复提交。明确拒绝也停止整个批次；先解决对应原因再处理该次记录。此脚本是单进程实验工具，不应同时运行两个 `run` 进程。

`build` 仅用于尚无外部运行记录时重新生成输入。已有结果后禁止覆盖原输入。绘制字体为 macOS 自带 Arial Unicode，字体哈希和每份文件哈希记录在 manifest。

## 产物

- 输入 PDF：`output/pdf/textin-pairing-merge-20260915/`，共 24 份、28 页。
- 证据：`output/analysis/textin-pairing-merge-20260915/`。
- `manifest.json`：输入、预期编辑、文字对象位置、文件及字体哈希。
- `input-verification.json`：文字层与预期文字一致、阴性对照字节一致、分页样本全文一致等检查。
- `contact-sheet-*.png`：完整输入页面的视觉检查索引。
- `runs/<case>-m<0|1>-r<repeat>/`：参数、输入哈希、创建响应、轮询轨迹、完整结果。
- `analysis.json`：保留类型、状态、内层文本和位置的结构化整理。
- `merge-pairs.json`：同输入开关对照，包括顶层变化数和细节叶节点多重集差别。
- `observations.md`：逐条可读的供应商原始观察。

## 判读规则

1. 先通过阴性对照及关键数值识读核验，再解释后续实验。服务忽略参数、缺失文字或表格未被识别时，应单列限制。
2. `diff_info.total`、顶层数组长度、INSERT/DELETE/CHANGE 总和、内层差异数分别保存。MATCH 不计为变化。
3. 将两侧原始坐标映射回样本的位置；不依赖 `element_id` 唯一或稳定。`doc_index=1` 是标准侧，`2` 是比较侧；页码从 0 起。
4. 叶节点相同而顶层数量不同，支持输出重新分组；叶节点不同必须检查文字覆盖和区域，不能只凭节点数量推断算法变化。
5. 换序被拆成增删只说明该输入的外部行为，不证明内部没有块匹配。零差异也不暴露实际采用的内部对应关系。
6. 开关对照会创建两个独立任务；若出现解析波动，应重复同条件，避免把波动误认为 `merge_diff` 的因果效果。
7. 初轮每条件一次，属于行为筛查，不能推算准确率、算法复杂度、合并阈值或生产稳定性。

官方协议核验于 2026-09-15：[创建任务](https://www.textin.com/document/doc-compare-create)、[获取差异信息](https://www.textin.com/document/doc-compare-diff-info)。`token_mode=1` 沿用本项目已成功的外部评测参数；其他参数以官方文档为准。
