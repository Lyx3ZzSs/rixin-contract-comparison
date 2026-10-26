# MinerU UNet 空白列检查与合同差异回放（2026-10-05）

**已补上候选表格的空白边列检查：PO 表格虚构的第 13 列被拦住，真正的空白列仍保留。15 份合同、45 次离线比较已完成；这次检查没有给上一版 UNet 增加 Golden 失败项，但整个业务回归仍未通过，生产流程没有切换。**

本次处理的是上轮 UNet 实验结果，不重新调用模型或 OCR。结构模型仍只有 UNet，行列还原依靠 TableRecover 规则算法，文字来自冻结的 PP-OCRv6。当前 MinerU 4.0.10 安装包另外包含 PP-LCNet 表格分类器、SLANet-plus 无线表格模型，并提供注入 OCR 引擎的有线/无线路线；这几项未参与我们的有线 UNet 组件实验，也没有调用 MinerU VLM。

新增可复用检查位于 `backend/app/services/extractors/contract_v3/table_raster_evidence.py`。对最左、最右列，先查看只占这一列的单元格是否有独立 OCR 文字；若整列这些格都为空，则用原始裁图的竖向线证据验证左右两侧边界。跨多列合并格中的文字不能替这一列提供文字支持。左右边界的高度加权覆盖率均需达到 60%；考虑 UNet 框通常位于线内侧，边缘允许至少 8 个像素的偏移。检查失败时，整张候选表降级为原文与页面证据，不删除列、不猜测重编号，也不输出结构化 HTML。

“没有 OCR 文字”本身不导致拒绝。有真实两侧竖线的空白列能够通过，华北第 25 页的空白名称格也作为真实裁图对照保留。最初的逐格四边完整检查会误伤续表与淡线，已经改为整列的两侧竖向证据。这个检查针对扫描有线表的空白边列，不能证明所有内部列、合并格或无线表格都正确。

PO 错列格 `c0012` 位于 `x1101..1126`，占原模型第 12 列、跨 3 行。它的左侧有原表右边框，右侧没有独立竖线，覆盖率为 0，因此被拒绝。该样本的旧正式发布函数确实放行 13 列结果；回归测试重现旧放行，再验证新拒绝、HTML 清除及原文不变。白边坐标问题不是这例的原因，因为该表使用的是未加白边的默认候选。

| 扫描表集合 | 上轮 UNet + 原发布检查 | 新空白列检查后 |
|---|---:|---:|
| 旧 16 张 | 16 张可发布 | 16 张可发布 |
| 后补的 42 张 | 38 张可发布 | 37 张可发布 |
| 全部 58 张 | 54 张可发布 | 53 张可发布 |

唯一新增的发布拒绝是已确认的 PO 虚构列表；6 张当前规则识别成功结果保持不变，其余 4 张原有失败仍保留原文。旧 16 张的备注行、`11000` 合并格修复结果保持。这里的“可发布”均为离线发布函数结果，不是准确率；这 58 张已经用于开发和回归，不再称为独立测试集。

合同回放使用冻结的完整文档，只替换上述 58 个扫描表区域的结构字段（HTML、单元格框、表 ID、结构状态、校验诊断）。原文、字符证据、阅读顺序及其他字段均逐项断言不变；原生表和未涉及区域保持所有字段不变。通过真实 CompareService 执行文档理解、表格比较、条款比较、证据生成、质量判定及事件输出，结果经过 JSON 往返。

三个对照分别为当前规则结果 `baseline`、上一版 UNet `v1`、新增边列检查 `v2`。15 份合同各执行 3 次，共 45 次，执行错误为 0。相关 pytest 共 133 项通过，包含真实虚构列拒绝、真实空白列保留、正式发布失败清理及既有结构比较测试。

| 合同 | baseline Golden 失败项 | v1 失败项 | v2 失败项 |
|---|---:|---:|---:|
| RETURN_CONFIRMATION | 6 | 6 | 6 |
| KAILI_FOUR_PROJECTS | 0 | 0 | 0 |
| HUANGHE_HAIXI | 6 | 6 | 6 |
| PO202604090100 | 7 | 7 | 7 |
| STATE_GRID_NORTH_CHINA | 3 | 3 | 3 |
| HUANENG_DINGBIAN | 8 | 8 | 8 |
| SUIZHOU_CHANGYUAN | 113 | 113 | 113 |
| HANENG_GONGHE | 3 | 3 | 3 |
| TIELING_SERVICE | 3 | 3 | 3 |
| LIANJIANG_PINGPO | 4 | 4 | 4 |
| TIANJIN_XINKOU | 1 | 1 | 1 |
| HUABEI_FIVE_STATIONS | 12 | 12 | 12 |
| SHENNENG_NAIMAN | 5 | 5 | 5 |
| NUOYANG | 3 | 3 | 3 |
| NANJING_DINGYI_XUZHOU_BANGYU | 0 | 0 | 0 |
| 总计 | 174 | 174 | 174 |

失败项包含未匹配事实、证据框不符和未被 Golden 收录的确认差异，不能用这个总数代替 255 个封存断言的通过率。更不能把“数目没有增加”理解为“内容完全相同”：逐项比较失败事实后，`v2` 相对 `v1` 没有新增或消除任何失败事实；相对 baseline，华北 7 项失败事实发生变化。其他合同的失败事实保持不变。

华北 6 项金额相关差异由“未找到”变成“已找到、但证据框不符合 Golden”，仍不能判通过。另一个已收录的金额补填事实得以匹配，同时新增一条未被 Golden 收录的确认修改：第 29 页末行 `□同意送审计□不同意送审计` 变成带 `年月日` 的版本。已用 Poppler 将两份 PDF 的完整第 29 页渲染并核对：对比版确实新增这三个模板字，原版末行没有，属于真实模板文字差异，而非 UNet 凭空拼字。此前过程更新中将其称为误报，已经更正；不能仅凭 Golden 未收录就判为模型回归。源 PDF 为 [原版](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/storage/golden-intake/contract-golden-v3/HUABEI_FIVE_STATIONS/original.pdf) 与 [对比版](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/storage/golden-intake/contract-golden-v3/HUABEI_FIVE_STATIONS/compare.pdf)。

PO 错表在 `v2` 中成为 `TABLE_STRUCTURE_UNRESOLVED` 的待复核差异，没有继续作为已认可的 13 列表参与比较。所有模式中，表格确认 ADD/DELETE 均为 0，确认状态按既有 Golden matcher 判定，包含 `change_decision=CONFIRMED`，没有误把质量枚举当作确认条件。RETURN 的 `2026年4月2日`、KAILI 的 `2026年4月21日` 真实日期 MODIFY 保持，且 UNet 减少了部分重复的日期候选和结构待复核差异。

回放关闭语义匹配与 rerank，并在 HTTPX 发送边界阻止新请求。下游封面/签署字段补读曾尝试 24 次请求，全部在发送前被拦截，并由原业务代码保留不确定性；成功外发为 0。这些补读没有冻结响应，因此本次没有验证它们的线上成功分支。这也是离线业务回放的明确范围，不宣称完整线上 Golden 已通过。最初 runner 将“零请求尝试”作为结束断言，因正常补读尝试而失败；后来保留全部拦截计数和不确定性结果，以“零外发、零执行错误”核查冻结回放，没有伪造补读响应。

检查已在 UNet 实验适配器的真实发布边界生效。没有激活生产 MinerU provider，没有更改既有比较器、Golden oracle、结构阈值或已冻结 OCR。继续接入前仍需解决金额证据框和其他未通过的业务事实，使用新的样本验证空白边列检查；当前适合保留为受控候选。

复现命令见实验 README 的后续部分。结果位于 `output/analysis/mineru-unet-boundary-replay-20261005/`：`table-summary.json` 为 58 张结构回归，`tables/` 保存真实发布结果及像素诊断，`business-summary.json` 为 45 次回放，`business-differences.json` 为失败事实逐项对照，`business-coverage.json` 为外部补读覆盖范围，`business/<合同>/<模式>/` 保存任务、事件及完整 Golden 报告。
