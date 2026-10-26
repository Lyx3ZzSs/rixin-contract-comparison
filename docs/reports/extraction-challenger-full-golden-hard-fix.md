```text
Fix A Permille Provenance     PASS
Fix B Ownership Float         PASS
Fix C Rotated Table            PASS

Targeted Replay               PASS

Full Golden Round 2           FAIL

READY_FOR_A_E_BENCHMARK       false
```

FULL_GOLDEN_STATUS = PENDING_HUMAN_REVIEW

三项已证明的根因已关闭。第二轮全部 **15 对 / 30 PDF / 571 页 / 255 条 sealed Golden** 已重新执行，不能将硬失败关闭等同于业务 Golden 全部通过。当前 CHALLENGER_REGRESSION=0、ERROR=0、TIMEOUT=0；剩余 **546 个 Ambiguous 业务事件簇**，覆盖 1112 条 ledger，均未作人工 disposition。Full Golden Round 2 的 FAIL 表示完整业务 gate 尚未放行，机器状态为 PENDING_HUMAN_REVIEW。

```text
Total validation cases: 1243
PASS: 131
CHALLENGER_REGRESSION: 0
BASELINE_DEFECT_EXPOSED: 0
EXPECTED_CHANGE: 0
AMBIGUOUS: 1112
ERROR: 0
TIMEOUT: 0
```

原有 255 条 sealed expectation 保持 hash 不变：PASS=131、待裁决 FAIL=124、ERROR=0、TIMEOUT=0。matcher 的 `*_REGRESSION` failure_type 是匹配器标签，尚未完成业务裁决的条目仍为 AMBIGUOUS，没有直接升级为已证明的 Challenger regression，也没有改 expectation。

## 1. Frozen Configuration

Git HEAD：`48a23804c1273ef59deea775707047efc60ae339`。本阶段开始时完整 working tree/index 已保存，结束时 index 字节一致。未重置用户改动。[初始冻结](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/frozen-before-manifest.json)、[当前状态原始记录](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/working-tree-status.txt)、[Round 2 冻结](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/golden-run-manifest.json)、[最终冻结验证](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/freeze-verification.json)。

初始 Challenger aggregate：`13f8f2fe41498494d88bfab744698ab470695d932a956bb09a3cba863a6b2a4d`。
修复后 Challenger aggregate：`9e3c7b950fc828b1dd18922147f9f8859e8b74284f515dad6c9283fb9aba1e7b`。
Golden aggregate：`a0e2fd325d946c514f79c2a2e1f750ab31a35fb8bd11e5c65caccae7efa05d5b`。

PP-DocLayoutV3 权重：`70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137`；doc_title / paragraph_title=0.4，其余=0.5。模型、配置、SemanticRoleResolver、NativeHeadingRepair、OCR routing、ClauseSplitter、PageAlignment、Matching 和 sealed expectation 均无改动。远端 PP-OCRv6 权重仍无可核实 hash，本轮用输入 hash 绑定并重放原始响应，不声称锁定远端权重。

全部 571 页 V3/OCR 原始响应均逐文件绑定 hash，全部 canonical 和业务输出重新计算。[输入重放证明](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/frozen-input-replay.json)。这不是一次新的 Layout/OCR 模型准确率采样，也没有将旧最终业务结果当作本轮结果。

三个修复提供独立 patch，基于本阶段 frozen working tree；另有验证器 patch。[Patch manifest](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/patches/manifest.json)。未创建混合提交，也未将原有大量未跟踪实验文件和暂存删除一并提交。

## 2. Fix A — Permille Provenance

根因：既有 verifier 已认可 `%o→‰`，但 adapter 拒绝长度变化，原始 OCR 错误进入 canonical。首次 divergence：EXTRACTION。修复只给已经批准的替换建立 source→canonical 映射，不新增千分号判定规则。

`CanonicalChar` 保留 `text_index`、`source_refs`、`source_range`、原始 polygons / word precision、反向 `source_to_canonical`、`NORMALIZED_MANY_TO_ONE` 和 `OCR_PERMILLE_NORMALIZATION`。bbox 是原始 source polygons 的 envelope，overlay 使用原始 polygons 的并集；未等分、伪造 glyph 框或删除 source。后续 canonical 索引随字符缩短调整，原始 OCR 索引保持不变。

失败测试先复现 p6 两处 `3%o`；修复后检查转换、两个 source 反查、overlay、非千分号上下文不修改，以及 0/0/0 ownership。[RED](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/fix-a-red.log)、[GREEN](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/fix-a-green.log)、[before/after 与独立坐标 trace](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/targeted-a/permille-proof.json)。

KAILI targeted 完整业务管线 COMPLETE、14 条 sealed 通过；compare p6 的 9.2、9.4 均包含 `3‰`，Round 2 再次验证 canonical、clause、source polygons 和最终 source linkage。[两处 overlay](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/targeted-a/line-16-overlay.png)、[9.4 overlay](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/targeted-a/line-23-overlay.png)、[Round 2 closure](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/hard-failure-closure.json)。

## 3. Fix B — Ownership Float

根因：rounded grouping key 和 raw x 混合比较，使左右分组为空，触发 max/min 异常。首次 divergence：OWNERSHIP。函数内部的 source/signing x 坐标统一量化到 3 位，之后分组、排序、比较和 extrema 都使用同一表示。

空输入、单列、近邻、重复 x、只有左/右列均返回 `None`（NO_TWO_COLUMN_SPLIT）。区域中点仅在独立来源列实际分处两侧时可作为有依据的分界。调用方保留既有显式 BOTH_PARTIES 区域的 ownership partition，并记录 `column_split_status` / `ownership_boundary_basis`，不把它伪称为已检出的双列；未知区域不会获得猜测归属。

覆盖 `56.59` / `56.59000015258789`、`0.1+0.2` / `0.3` 和三个真实 prepared-source fixture。[RED](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/fix-b-red.log)、[GREEN：88 passed](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/fix-b-green.log)。中间过宽的 None 处理曾破坏已有签署字段测试，已停止该 replay 并保留 attempt；最终局部回归全部通过。

HUANGHE_HAIXI、SUIZHOU_CHANGYUAN、LIANJIANG_PINGPO 的 SigningRegionStage / Matching / Diff / Evidence / Summary 均真实执行 COMPLETE。[Targeted stage traces](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/targeted-b/replay-status.json)。本轮原先被阻断的 106 条 sealed 断言全部产生真实结果，待裁决差异保留在新 ledger。

## 4. Fix C — Rotated Table

根因：入口只接受 `[2,0,0,2,0,0]`，实际 270° affine 为 `[0,-2,2,0,0,1684]`，页面在模型前被 assert 拦截。首次 divergence：TABLE。修复将 page polygon 经真实 PDF display affine 投影到已正向渲染的图，再平移到 crop-local；明确保存 `page_to_model` 与 `model_to_page`。PDF renderer 已应用 /Rotate，不再重复旋转或伪装 identity。

0° / 90° / 180° / 270° 均验证点、polygon、非零 cropbox、不同 x/y raster scale、实际像素与 CellGraph inverse。模型返回的 NumPy cells 也纳入测试。HTML 只取同一个 TableCellGraph；未通过的图保留 issues、tokens、source geometry 和安全 fallback，不再经过独立 HTML 的旧解码流程。

[初始 RED](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/fix-c-red.log)、[实际 NumPy RED](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/fix-c-numpy-red.log)、[GREEN：12 passed](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/fix-c-green.log)、[Targeted transform/graph 证据](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/targeted-c/replay-status.json)。中间的 NumPy truth-value 和旧 decoder assert 均保留在 attempt 目录，并已有针对性测试；未把它们当作模型准确率问题。

| SUIZHOU compare page | model invoked | 最终 graph | HTML published |
|---|---|---|---|
| 13 | true | TABLE_STRUCTURE_UNRESOLVED | false |
| 35 | true | TABLE_STRUCTURE_UNRESOLVED | false |
| 41 | true | TABLE_STRUCTURE_UNRESOLVED | false |
| 46 | true | CONSISTENT | true |
| 52 | true | TABLE_STRUCTURE_UNRESOLVED | false |

五页均已调用模型，round-trip 通过，无 ERROR；未通过结构契约的四页按要求安全 fallback。没有 whole-table OCR 补救。

## 5. Targeted Replay 与完整第二轮

最终局部测试合计 **167 passed**，含前置 Heading / Table / Evidence 回归及三项新增契约测试。另将前置已验收的 80 页与本轮产物对齐：canonical text、角色、reading order、source geometry 80/80 一致；SHENNENG p2 标题仍正确，NANJING p3 安全 fallback、p4 graph 一致。[完整测试日志](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/final-targeted-tests.log)。先完成 A→B→C targeted，再运行完整第二轮。

| Contract | Pages | Pipeline | sealed PASS | sealed pending FAIL | ERROR | TIMEOUT | Ambiguous events |
|---|---:|---|---:|---:|---:|---:|---:|
| RETURN_CONFIRMATION | 29 | COMPLETE | 9 | 6 | 0 | 0 | 31 |
| KAILI_FOUR_PROJECTS | 18 | COMPLETE | 14 | 0 | 0 | 0 | 20 |
| HUANGHE_HAIXI | 8 | COMPLETE | 8 | 6 | 0 | 0 | 38 |
| PO202604090100 | 16 | COMPLETE | 12 | 5 | 0 | 0 | 29 |
| STATE_GRID_NORTH_CHINA | 49 | COMPLETE | 19 | 2 | 0 | 0 | 30 |
| HUANENG_DINGBIAN | 80 | COMPLETE | 9 | 5 | 0 | 0 | 30 |
| SUIZHOU_CHANGYUAN | 106 | COMPLETE | 8 | 75 | 0 | 0 | 186 |
| HANENG_GONGHE | 10 | COMPLETE | 4 | 3 | 0 | 0 | 10 |
| TIELING_SERVICE | 6 | COMPLETE | 2 | 3 | 0 | 0 | 12 |
| LIANJIANG_PINGPO | 54 | COMPLETE | 8 | 1 | 0 | 0 | 24 |
| TIANJIN_XINKOU | 10 | COMPLETE | 1 | 1 | 0 | 0 | 5 |
| HUABEI_FIVE_STATIONS | 95 | COMPLETE | 7 | 10 | 0 | 0 | 36 |
| SHENNENG_NAIMAN | 62 | COMPLETE | 9 | 4 | 0 | 0 | 23 |
| NUOYANG | 12 | COMPLETE | 16 | 3 | 0 | 0 | 54 |
| NANJING_DINGYI_XUZHOU_BANGYU | 16 | COMPLETE | 5 | 0 | 0 | 0 | 18 |

全部文档均保存 Extraction / Clause / Matching / Diff / Table / Evidence / Ownership，阶段 JSON 在 `round-2/contracts/<contract>/stages/`。逐字段比较、候选排序、source block linkage、page membership、section_path 和 event projection 均保留，不只比较计数。

Extraction 路由：`{'NATIVE_GOOD': 239, 'SCANNED': 247, 'MIXED': 85}`。571 页 duplicate canonical / missing source / uncovered canonical = **0 / 0 / 0**；source→coordinate→page→overlay 自动检查零错误。NATIVE_GOOD full-page=0，MIXED 仅 uncovered/masked regions，SCANNED 每页一份正常 coverage；PP-Structure=0、`/layout-parsing`=0、禁止的重复 full-page/full-document=0。[逐文档/request 审计](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/routing-hard-assertions.json)。

表格共 115 个 region：`{'TABLE_STRUCTURE_UNRESOLVED': 54, 'automatic_contract_pass': 49, 'CONSISTENT': 12}`。最终业务 Evidence：2444 份，linkage=`{'PENDING_HUMAN_REVIEW': 1018, 'PIXEL_SOURCE_LINKED': 414, 'LINKED': 1012}`，invalid page=0。自动 canonical 坐标链通过不等于所有最终业务 evidence 已获人工裁决；无法精确关联的 source 身份继续进入 Ambiguous，未隐藏。

## 6. Remaining Ambiguous 与停止点

[完整 case ledger](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/golden-case-ledger.json)、[failure ledger](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/golden-failure-ledger.json)、[按业务事件聚类](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/ambiguous-business-events.json)、[新 review pack](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/full-golden-hard-fix/round-2/review-pack/index.html)。

本轮重新计算 1112 条 Ambiguous，形成 **546 个业务事件簇**。优先使用既有 event_response 的 event_id / raw_diff_ids 归组；历史事实只有严格语义 identity 一致时才与当前事件合并。缺失 Golden 事实保留独立身份，未进行模糊合并或人工裁决。旧的 436 不沿用。

下表是待查原因的观察分组，同一个业务事件可涉及多个组；它不替代 first divergent stage 的因果裁决。无法确认者仍为 UNKNOWN。

| Observation group | Business events |
|---|---:|
| BASELINE_DECISION_EVIDENCE_OWNERSHIP_DIFFERENCE | 115 |
| EVENT_EXISTENCE_OR_TEXT_DIFFERENCE | 396 |
| EVIDENCE_SOURCE_LINKAGE_UNRESOLVED | 158 |
| GOLDEN_BBOX_REGRESSION | 1 |
| GOLDEN_DECISION_REGRESSION | 43 |
| GOLDEN_FALSE_NEGATIVE_REGRESSION | 80 |
| MASKED_NATIVE_OCR_CROSSING | 2 |

仍无人工 disposition 的阻断级 Ambiguous，因此完整 Full Golden 未 PASS。历史 B Chapter 边界继续是已确认 BASELINE_DEFECT_EXPOSED，不重新作为 regression；不在本轮 15 对数据集内，不虚增本轮统计。

硬失败关闭工作到此停止。未启动 A–E benchmark，未统计或发布性能结论。

READY_FOR_A_E_BENCHMARK = false
