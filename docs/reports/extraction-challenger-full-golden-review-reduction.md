# Full Golden — 确定性风险分层 review gate

已取消“全部 528 个 cluster 默认逐组人工裁决”的规则。新 gate 对所有 ledger 和事件先做确定性检查，保留原始 528 个观察 cluster 供审计，另生成实际审核队列。业务算法、Round 2 原始结果、sealed Golden expectation 均未修改。

[打开 dashboard](http://127.0.0.1:8766)。默认显示高风险代表事件；可切换无法分类和低风险抽样队列。自动关闭项不进入人工队列，证明保存在事件 JSON 中。

## 本轮实际结果

| 结果 | 原始 cluster 数 | 业务事件数 |
| --- | ---: | ---: |
| 自动关闭 `PASS_EQUIVALENT` | 2 | 2 |
| 自动关闭 `EXPECTED_CHANGE` | 0 | 0 |
| 人工抽样后关闭 | 0 | 0 |
| 低风险待抽样 | 0 | 0 |
| 必须人工判断 | 498 | 515 |
| 无法分类，保留阻断 | 28 | 29 |
| 合计 | 528 | 546 |

以上 cluster 分类互斥：混合组优先按高风险、无法分类、抽样、自动关闭归类。一个多事件观察组可以包含不同风险等级，不能用组内一个结论覆盖所有事件。因此剩余有 **544 个事件代表 case**（515 高风险、29 无法分类），覆盖全部未关闭 ledger。1,112 条原始 ledger 唯一归属，无遗漏或重复计数。

**这次没有实现大幅人工量压缩。** 当前数据中，没有组同时满足业务不变量相等、物理证据来源相等、且只剩同一严格根因的低风险差异。零抽样关闭是真实结果，未代填人工结论，也没有把语义不确定项判 PASS。

根据用户后续明确授权，当前为 **条件通过，仅用于性能 Benchmark**：

```text
FULL_GOLDEN_STATUS = CONDITIONAL_PASS_BENCHMARK_ONLY
BUSINESS_REVIEW_STATUS = DEFERRED_BUSINESS_REVIEW
READY_FOR_A_E_BENCHMARK = true
READY_FOR_PRODUCTION = false
```

544 个未裁决业务事件逐项保存在 `deferred-business-review.json`，`business_verdict=null`、`production_blocking=true`。上表风险分类保持不变；延期不等于 PASS。Golden expectation 没有修改。性能准入授权独立保存在 `benchmark-only-authorization.json`，不写入人工 disposition。

A–E 对照已启动：固定原始输入，baseline / Challenger 交替三轮，共 30 次完整后端 pipeline 观察（`pipeline-only`，不是浏览器 E2E）。[运行约定](../../archive/experiments/2026-09-24/challenger_ae_benchmark/README.md)。实时状态位于 `output/analysis/challenger-ae-benchmark-20260923/run-status.json`。

**A–E 结果不得用于生产放行。生产上线仍要求后续业务 review 或明确的风险接受。**

## 自动关闭的两个事件

| 原始 cluster | case | 证明 |
| --- | --- | --- |
| `RC-f16bc5e3110aa651` | `NUOYANG-EVIDENCE-D006` | 完整 baseline 中恢复唯一精确事件；业务文本、decision、匹配、diff、PDF 页码与 evidence 坐标／字符映射一致，仅输出 ID 不同 |
| `RC-1b95a369789faf14` | `NUOYANG-EVIDENCE-D065` | 同上，包含质量保证期义务原文和条款匹配的严格相等核验 |

这两个 PASS 表示本次变化等价，不把旧结果当成绝对正确的业务 oracle。任何关联的独立 Golden 约束冲突仍阻断整个事件。例如 `SUIZHOU_CHANGYUAN_P27_HEADER_FOOTER_ADD_BB6FA87D00` 虽然 baseline 与 challenger 几乎完全相同，但与 Golden 的 content status 冲突，仍需人工。

## 确定性规则

每个 ledger 记录先验证 review pack 的业务快照确实存在于完整 baseline / challenger 结果中。缺少 pack 绑定时，仅在两侧完整结果内都存在唯一精确语义身份时恢复配对；候选不唯一保持 `UNCLASSIFIED`，不按相似度或 matcher 标签猜测。

自动关闭必须同时满足：

1. 原文与比较文相等。只允许 NFC 和空白折叠；不删除金额、数字、日期、比例、主体、义务、符号或标点。
2. 业务 decision、风险 flags、观察状态、content candidates、置信度相等。flags 只排序，不删除风险项。
3. 条款 ID、匹配方法、分数、置信度与候选相等。条款 ID 变化不能假设为重编号。
4. diff 类型、文本、片段、可读描述和 change ranges 相等。
5. evidence 指向相同文件 hash、页码、原文、bbox 和字符映射。不同坐标即使只移动很小距离，也不按 IoU 或“看起来相近”自动通过。缺失来源不等同于来源相同。
6. 独立 sealed Golden 约束没有冲突，所有字段差异都被白名单或明确批准的边界传播覆盖。未知字段保持阻断。

只有业务不变量与物理来源全部相等、仅存在来源 metadata / ID 等差异时自动 `PASS_EQUIVALENT`。只有精确绑定前后快照 SHA256、允许字段、裁决人及证据引用的已有批准，才能将边界传播标记 `EXPECTED_CHANGE`。批准不能绕过金额、日期、主体、条款匹配、decision 或证据变化。本轮没有适用的批准记录，未把历史 B Chapter 裁决扩展到新的合同。

`boundary-approvals.json` 是可选的已有批准输入，顶层为 `approvals` 数组。每条需要 `human_attestation: true`、`reviewer`、`evidence_references`、`baseline_sha256`、`challenger_sha256` 和精确 `allowed_fields`；仅支持 `section_type` / `section_path`。不能通过粗粒度 rule 名称、单独的 matcher 标签或自由文本批准跨合同传播。

## 抽样与高风险审核

低风险仅接受“物理来源完全相同，业务不变量全部相等，仅 evidence method / precision 展示 metadata 变化”的可证明机制。严格 fingerprint 保留 gate 版本、机制、完整变换、方向、source type 和 diff type。每个成员仍保留自身完整证明与 hash，不掩盖其业务值。

同一指纹组确定性选取 3～5 个事件代表，优先覆盖不同合同、页码跨度和 ledger 数量差异；不足 3 个时全部审核。API 要求显式确认所有指定代表、整组适用范围、裁决人、理由和证据引用，才批量裁决整组。少审一个样本、`AMBIGUOUS`、`NEEDS_SPLIT` 或证明过期均不关闭。未实际提交的抽样只能统计为待抽样。

风险判断、事件存在／消失、文本、条款匹配、evidence 来源变化全部保留逐事件人工审核。旧的多事件观察 fingerprint 不构成同根因证明，不能用一个样本批量裁决高风险事件。每个事件代表下保留所有关联 ledger、Golden 规则、原始 PDF crop、前后结果及自动检查原因。

无法分类包括候选配对不唯一、双方业务快照未绑定、证据不足或未知字段。它们单独展示并继续阻断，不混进等价项。

## 数据与 gate 完整性

原始 freeze ID 继续沿用 `36ab030e44799620428d2d4d34f001663d1e07312a0a239c7bae4d344ec277b3`。构建前校验原始 7,373 个冻结文件／输入文件；另将完整 baseline、源 PDF、gate 源码、生成的证明／审核数据绑定 SHA256。启动服务、写裁决和最终重算都会校验相应绑定。重建 dataset 后旧裁决不能复用，旧服务需要重新启动。

人工裁决绑定 dataset、严格 fingerprint、成员集合、证明 hash 与 revision，并保留历史。业务 gate 仍汇总自动裁决与人工裁决；任何未决、regression、error、timeout 或禁止请求都不允许业务 PASS。性能条件准入是独立的显式用户授权；即使业务 gate 后续 PASS，当前工具也不会自动设置生产准入为 true。

生成数据位于 `output/analysis/full-golden-review-reduction/`：`dashboard-data.json`、`reduction-summary.json`、`review-units/`、`clusters/`、`events/`。原始 Round 2 数据保留不变。

## 验证与运行

46 项测试覆盖性能授权不能关闭业务事件、业务 PASS 不自动授予生产准入，以及关键文本变化、风险状态、条款匹配、不同原文坐标、缺失字段、未知字段、精确批准与批准失效、Golden 独立冲突、严格分组、代表选择、样本覆盖、批量关闭、陈旧证明和全量唯一覆盖。HTTP 检查确认未决 gate 重算返回 409；浏览器 DOM 检查默认高风险队列、无法分类／空抽样筛选、证据图片加载、未预选裁决、重算禁用和 390px 无横向溢出。截图 API 超时，本次没有新的视觉截图验收。条件准入更新后，9,050 个源文件与生成产物的 hash 校验通过。检查记录见 `tests-v2.log`、`http-smoke-v2.json`、`browser-qa-v2.json`、`final-verification-v2.json`。

```sh
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python -m pytest experiments/full_golden_review_reduction -q
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/full_golden_review_reduction/build.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/full_golden_review_reduction/server.py --port 8766
```

修改 gate 或重新生成数据后应重启服务。正常继续审核无需重建。
