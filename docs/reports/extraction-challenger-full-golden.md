FULL_GOLDEN_STATUS = FAIL

```text
Total cases: 656
PASS: 107
Challenger regression: 2
Baseline defect exposed: 0
Expected change: 0
Ambiguous: 436
Error: 111
Timeout: 0
```

以上是完整 validation ledger 的互斥结果口径，包含原有 **255 条 sealed Golden** 与新增的契约、baseline 差异和 Evidence 来源检查；没有增加、删除或修改原有 expectation。原有 255 条单独统计为 **PASS=107、待裁决 FAIL=42、ERROR=106、TIMEOUT=0**。Error=111 包含三个合同中未能执行到最终断言的 106 项，以及 5 个独立表格异常；不是 111 个独立根因。业务管线逐一尝试 15/15 对合同，12 对到达 Summary，3 对在 Ownership 中断，无跳过、无 timeout。

已经证明 2 处 canonical 千分号错误；另外 3 个 Ownership 异常、5 个旋转表格异常阻断放行。待裁决项没有被 Codex 标成业务 PASS。历史 baseline 仅作比较证据。

| Ledger scope | 数量 |
|---|---:|
| SEALED_GOLDEN_ORACLE | 255 |
| BASELINE_BUSINESS_DIFFERENCE | 290 |
| EVIDENCE_PROVENANCE_REVIEW | 93 |
| ADDITIONAL_BUSINESS_GATE | 18 |

主要产物：[冻结 manifest](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/golden-run-manifest.json)、[逐 case ledger](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/golden-case-ledger.json)、[failure ledger](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/golden-failure-ledger.json)、[人工 review pack](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/review-pack/index.html)。机器 ledger 保留 source text / blocks、Layout、Resolver、Clause、Matching、Diff、Evidence 和 first divergent stage；未定位者明确为 UNKNOWN。

## 1. Frozen Configuration

Git HEAD：`48a23804c1273ef59deea775707047efc60ae339`。进入阶段时工作树非 clean；完整 status、index diff、working-tree diff 已保存，未重置用户改动。冻结 126 个既有实验文件、202 个生产文件、配置、Golden expectation/validator 和本地模型文件。最终 freeze check **PASS，drift=0**；未修改 Challenger、生产业务代码、Golden expectation 或冻结参数。

PP-DocLayoutV3；doc_title / paragraph_title=0.4，其余=0.5（figure_title 保持既有 0.5）；V3 权重 SHA256：`70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137`。title policy SHA256：`1891e1fb1ff53594c14892d96b221d3c70a6b99c48fd1533bf4dead8414d2042`。Native-first、SemanticRoleResolver、NativeHeadingRepair、Selective PP-OCRv6、Unified Artifact、Table fallback、Evidence 坐标与 ClauseSplitter adapter 均保持冻结。

PP-OCRv6 家族沿用用户确认的部署 identity；现有服务没有提供可核实的远端权重 hash，manifest 如实记录为 unknown，不能宣称完成服务端权重锁定。运行时版本见 [runtime identity](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/business-runtime-identity.json)。

既有 Release Gate 文档落后于用户最新 PASS 指令；本轮以用户明确的四 Gate PASS 为启动依据，不重新要求人工 Evidence 前置签收。实验通过冻结 Artifact 注入替换 ExtractionStage，之后完整运行既有 DocumentUnderstanding、Preparation、PageAlignment、Signing、Clause、Matching、Diff、Evidence、Quality、Visualization、Summary；没有重复执行生产 extractor 的 full-document OCR。

验证器修正记录：首轮错误地禁止所有下游 ROI OCR，第二轮尚未读出 SealRegion.page_number；这两轮均完整保留在 attempt-01/02 并明确作废为最终业务依据。最终 v3 记录真实 PDF / page / crop / route，按用户约束断言，并保存辅助 OCR 的输入 PNG 与原始响应；**最终完整重放了全部 15 对**。修正仅发生于验证器，没有修复或改变 Challenger 路由、坐标或业务算法。前两轮的 8/1 次拦截不能当作旧整页路径回归的证据。

## 2. Golden Dataset

`contract-golden-v3` 全部 15 对、30 PDF、571 页、255 条原有业务断言；PDF hash 与页数均经既有 loader 验证。没有挑选成功 case。

| Contract | 页数 original / compare | 原有断言 | PASS | 待裁决 FAIL | ERROR | 业务管线 |
|---|---:|---:|---:|---:|---:|---|
| RETURN_CONFIRMATION | 14 / 15 | 15 | 9 | 6 | 0 | COMPLETE |
| KAILI_FOUR_PROJECTS | 9 / 9 | 14 | 14 | 0 | 0 | COMPLETE |
| HUANGHE_HAIXI | 4 / 4 | 14 | 0 | 0 | 14 | ERROR |
| PO202604090100 | 7 / 9 | 17 | 12 | 5 | 0 | COMPLETE |
| STATE_GRID_NORTH_CHINA | 25 / 24 | 21 | 19 | 2 | 0 | COMPLETE |
| HUANENG_DINGBIAN | 40 / 40 | 14 | 9 | 5 | 0 | COMPLETE |
| SUIZHOU_CHANGYUAN | 53 / 53 | 83 | 0 | 0 | 83 | ERROR |
| HANENG_GONGHE | 5 / 5 | 7 | 4 | 3 | 0 | COMPLETE |
| TIELING_SERVICE | 3 / 3 | 5 | 2 | 3 | 0 | COMPLETE |
| LIANJIANG_PINGPO | 27 / 27 | 9 | 0 | 0 | 9 | ERROR |
| TIANJIN_XINKOU | 5 / 5 | 2 | 1 | 1 | 0 | COMPLETE |
| HUABEI_FIVE_STATIONS | 48 / 47 | 17 | 7 | 10 | 0 | COMPLETE |
| SHENNENG_NAIMAN | 31 / 31 | 13 | 9 | 4 | 0 | COMPLETE |
| NUOYANG | 6 / 6 | 19 | 16 | 3 | 0 | COMPLETE |
| NANJING_DINGYI_XUZHOU_BANGYU | 8 / 8 | 5 | 5 | 0 | 0 | COMPLETE |

逐文档完整记录见 [document ledger](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/document-ledger.json)。以下请求数是正确性覆盖审计，不作为性能结论；OCR region 包含已列明的辅助 ROI。

| Document / side | 页 | Native | Mixed | Scanned | V3 新 forward / replay | OCR page / region | 其中辅助 ROI | table fallback |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| RETURN_CONFIRMATION / original | 14 | 14 | 0 | 0 | 0 / 14 | 0 / 0 | 0 | 0 |
| RETURN_CONFIRMATION / compare | 15 | 0 | 0 | 15 | 15 / 0 | 15 / 0 | 0 | 7 |
| KAILI_FOUR_PROJECTS / original | 9 | 9 | 0 | 0 | 6 / 3 | 0 / 0 | 0 | 0 |
| KAILI_FOUR_PROJECTS / compare | 9 | 0 | 0 | 9 | 9 / 0 | 9 / 0 | 0 | 3 |
| HUANGHE_HAIXI / original | 4 | 4 | 0 | 0 | 2 / 2 | 0 / 0 | 0 | 0 |
| HUANGHE_HAIXI / compare | 4 | 0 | 0 | 4 | 4 / 0 | 4 / 0 | 0 | 3 |
| PO202604090100 / original | 7 | 0 | 0 | 7 | 4 / 3 | 7 / 0 | 0 | 1 |
| PO202604090100 / compare | 9 | 0 | 0 | 9 | 9 / 0 | 9 / 0 | 0 | 3 |
| STATE_GRID_NORTH_CHINA / original | 25 | 0 | 25 | 0 | 22 / 3 | 0 / 25 | 0 | 0 |
| STATE_GRID_NORTH_CHINA / compare | 24 | 0 | 0 | 24 | 24 / 0 | 24 / 0 | 0 | 1 |
| HUANENG_DINGBIAN / original | 40 | 39 | 1 | 0 | 37 / 3 | 0 / 1 | 0 | 0 |
| HUANENG_DINGBIAN / compare | 40 | 0 | 0 | 40 | 39 / 1 | 40 / 0 | 0 | 1 |
| SUIZHOU_CHANGYUAN / original | 53 | 53 | 0 | 0 | 50 / 3 | 0 / 0 | 0 | 0 |
| SUIZHOU_CHANGYUAN / compare | 53 | 0 | 53 | 0 | 53 / 0 | 0 / 106 | 0 | 5 |
| HANENG_GONGHE / original | 5 | 4 | 1 | 0 | 3 / 2 | 0 / 4 | 0 | 0 |
| HANENG_GONGHE / compare | 5 | 0 | 0 | 5 | 5 / 0 | 5 / 0 | 0 | 1 |
| TIELING_SERVICE / original | 3 | 3 | 0 | 0 | 1 / 2 | 0 / 0 | 0 | 0 |
| TIELING_SERVICE / compare | 3 | 0 | 0 | 3 | 3 / 0 | 3 / 2 | 2 | 2 |
| LIANJIANG_PINGPO / original | 27 | 25 | 2 | 0 | 0 / 27 | 0 / 3 | 0 | 0 |
| LIANJIANG_PINGPO / compare | 27 | 0 | 1 | 26 | 27 / 0 | 26 / 1 | 0 | 3 |
| TIANJIN_XINKOU / original | 5 | 5 | 0 | 0 | 3 / 2 | 0 / 0 | 0 | 0 |
| TIANJIN_XINKOU / compare | 5 | 0 | 0 | 5 | 5 / 0 | 5 / 1 | 1 | 1 |
| HUABEI_FIVE_STATIONS / original | 48 | 48 | 0 | 0 | 45 / 3 | 0 / 0 | 0 | 1 |
| HUABEI_FIVE_STATIONS / compare | 47 | 0 | 0 | 47 | 47 / 0 | 47 / 2 | 2 | 15 |
| SHENNENG_NAIMAN / original | 31 | 30 | 1 | 0 | 0 / 31 | 0 / 1 | 0 | 0 |
| SHENNENG_NAIMAN / compare | 31 | 0 | 0 | 31 | 31 / 0 | 31 / 4 | 4 | 4 |
| NUOYANG / original | 6 | 5 | 1 | 0 | 3 / 3 | 0 / 1 | 0 | 1 |
| NUOYANG / compare | 6 | 0 | 0 | 6 | 6 / 0 | 6 / 0 | 0 | 0 |
| NANJING_DINGYI_XUZHOU_BANGYU / original | 8 | 0 | 0 | 8 | 0 / 8 | 8 / 2 | 2 | 1 |
| NANJING_DINGYI_XUZHOU_BANGYU / compare | 8 | 0 | 0 | 8 | 8 / 0 | 8 / 2 | 2 | 2 |

## 3. Extraction Results

571/571 页完成：NATIVE_GOOD=239、MIXED=85、SCANNED=247。canonical 字符级计数为 **duplicate=0 / missing source=0 / uncovered canonical text=0**。这里检查 canonical 字符是否唯一指向来源，不把这个结果等同于 PDF 文字业务准确率。

**CHALLENGER_REGRESSION：凯力 compare p6，9.2 与 9.4 中 PDF 的 `3‰` 成为 `3%o`。** 首次 divergence=`EXTRACTION`。OCR 原始输出错误；冻结 `text_contract_repair` 因 `%o→‰` 改变字符串长度而拒绝修复，错误进入 canonical text。原 PDF 视觉核对、raw OCR、blocks、字符 provenance 和修复拒绝原因见 [两个已证明回归](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/proven-regressions.json)。凯力 14/14 原有 oracle 通过仍不能覆盖这两项错误，故 Full Golden 不通过。没有改修复器或坐标索引。

LIANJIANG compare p1 另有两条 OCR `一` 与 masked native 字符相交，冻结保护逻辑拒绝拼接；无 canonical 重复，但真实遗漏还是线条噪声尚未裁决，归类 AMBIGUOUS / OCR_ROUTING 并进入 review pack。

硬断言结果：NATIVE_GOOD full-page OCR=0；MIXED 的 142 次 selective region OCR 使用 native mask；SCANNED 247 页各一次 normal full-page coverage。最终辅助 OCR 共 13 次，均记录为局部 ROI；PP-Structure request=0、`/layout-parsing` request=0、额外 full-page / full-document OCR=0。辅助 ROI 的 request/response 位于各 contract 的 auxiliary-ocr 目录。[完整请求审计](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/routing-hard-assertions.json)。

## 4. Clause Boundary Results

30/30 文档均生成 baseline ↔ Challenger 的逐 block / clause ledger，包含 clause number、heading、body、anchor boundary、page membership、section_path、source block linkage；不是只比较条款数量。每份在 documents/<contract>/<side>/baseline-comparison/，汇总见 [Clause comparison](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/clause-comparison-summary.json)。

前置 Gate 的 80 页经 PDF hash / page 对齐（3 个样本只映射 ID）后，canonical text、角色、reading order、native/OCR provenance **80/80 等同已验收版本**。SHENNENG p2 `2、合同总价：` 仍为 paragraph_title。[Release regression replay](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/release-regression-replay.json)。既有 B 的 25 个 Chapter boundary 裁决继续是 BASELINE_DEFECT_EXPOSED；B 是既有 fixture，不在这 30 PDF 内，未重跑、未当作新 regression 或虚增本轮通过数。

## 5. Matching Results

12 对合同完整保存选中的 original/compare clause、pairing、match_method、score_details、match_candidates 及排序，并与历史 saved baseline 分别留档。3 对在 SigningRegionStage 中断，因此它们的 Matching / Diff / 最终 Evidence 明确为 NOT_REACHED，不能推测通过。未改 PageAlignment 或 Matching。

所有逐阶段 JSON 在 contracts/<contract>/stages/；最终配对在 result.json。无证据证明 earliest layer 的不匹配仍写 UNKNOWN，不能用“最终 diff 不一致”替代归因。

## 6. Diff Results

保留 diff_type、两侧全文 / snippet、范围、业务分类、review / recognition decision 和 event projection。原有 matcher 不做改动。最终 oracle 的 pending 不匹配与 unexpected confirmed diff 均逐项进入 ledger；MUST_NOT_EXIST / MUST_NOT_CONFIRM 的成功也记录实际匹配集合，未用 diff count 代替验证。

历史 baseline 保存结果用同一 matcher 回放自身已有 **55 项不匹配**（包含 unexpected confirmed），因此旧结果不是绝对真值。详见 [baseline oracle compatibility](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/baseline-oracle-compatibility.json)。对无法从原 PDF 和阶段证据作出业务判断的变化，没有自行判为 baseline defect 或 expected change。

## 7. Table Results

共 115 个 V3 table region：49 个 native vector 契约通过、11 个 structure-only graph 一致、50 个明确 TABLE_STRUCTURE_UNRESOLVED；另 5 个执行 ERROR。只有前 60 个发布 HTML；未通过者不发布 HTML。row / column、rowspan / colspan、token ownership、原 geometry、fallback 状态保存在 artifacts 与 tables/graphs。

NANJING original p3 继续安全 fallback，p4 graph 一致，未恢复错误 HTML。**5 个 ERROR 均为 SUIZHOU compare p13/35/41/46/52 的 270° 页面**：冻结 table_structure 入口断言只接受 `[2,0,0,2,0,0]`，实际为 `[0,-2,2,0,0,1684]`，在模型调用前失败；首次 divergence=`TABLE`。保留异常和 source/layout；错误占位空记录有单独 sentinel index，绝不当作模型预测或通过的 fallback。[Table execution errors](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/tables/execution-errors.json)。

## 8. Evidence Results

既有 heading/table/ownership/evidence 相关测试 **63 passed**；571 页 source→coordinate→page→overlay 自动 round-trip 无错误，前置人工 Evidence QA 不重新作为启动条件。

最终业务 Evidence 共 775 个记录，source linkage 状态为 `{'PENDING_HUMAN_REVIEW': 250, 'PIXEL_SOURCE_LINKED': 305, 'LINKED': 220}`；97 个最终 char box 未被当前 source-identity audit 精确链接，invalid page=0。它们没有被称为已证明的 bbox regression，也没有被判 PASS；辅助 ROI、无 char-box fallback 与 canonical 来源的映射仍需裁决。视觉对象以原 PDF / page / bbox 的 pixel source 记录，不冒称 glyph precision。完整字段、polygon/source refs、precision 和 review state 在各 contract 的 evidence-source-ledger.json。

相关 AMBIGUOUS 按业务 event 分组生成两侧 PDF crop、highlight、baseline / Challenger / expectation、文字和原因。[Evidence summary](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/final-evidence-summary.json)。Full Golden 的最终 Evidence 全绿条件尚未满足。

## 9. Ownership Results

保存 event ownership、review state、cover/body、section_path 与 source block linkage；完整投影在 ownership-ledger.json。

黄河海西、随州、连江三对在 `_two_column_midpoint` 抛出空集合 max()/min()。独立最小复现使用已保存的 prepared source blocks，定位为 **OWNERSHIP**：将 x0 round 到三位后，再与未 round 的原坐标做 <=/>= 筛选；例如 56.59000015258789 > 56.59，导致左集合为空。这是新输入触发的既有下游缺陷，作为 ERROR 阻断，未冒充已验收的 baseline extraction 改善，也没有修改其它层。[三个独立复现](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/extraction-challenger-full-golden/ownership-error-reproducers.json)。

## 10. Difference Adjudication

2 项已证明 CHALLENGER_REGRESSION；本轮新增 BASELINE_DEFECT_EXPOSED=0、EXPECTED_CHANGE=0。这不撤销历史已批准的 B Chapter 等裁决。仍有 436 个 ledger 项无法自动裁决，包含原有 oracle 差异、unexpected confirmed、290 项历史业务输出差异、Evidence 来源映射和两条 OCR overlap；均保留为 PENDING_HUMAN_REVIEW。部分项关联同一实际事件，ledger scope 明确区分，不能当作同样数量的独立根因。

547 个 review pack 记录含错误合同的上下文材料；原页定位不可确定时明确标注整页 context，黄色边框不是已定位 glyph。错误合同的后续 Matching/Diff/Evidence 未产生，不伪造其输出。人工 disposition 尚未填写。

## 11. Remaining Blockers

1. 凯力 p6 两处 `3‰→3%o` canonical 错误，首次层 EXTRACTION。
2. 三个合同的签署区 round/unrounded 空集合异常，首次层 OWNERSHIP。
3. 五个旋转页面不满足冻结表格入口契约，首次层 TABLE。
4. 未裁决业务 / baseline 差异，以及最终 Evidence 来源映射；UNKNOWN 保持 UNKNOWN。
5. 远端 OCR 精确权重 identity 尚无服务端可核验证据。

本轮停止在失败归因；未修复业务代码、未改 expectation、未做架构探索、未启动性能优化。

## 12. Performance Benchmark Readiness

不满足 Challenger regression=0、ERROR=0、阻断 AMBIGUOUS 全有 disposition 的条件。未启动 A–E benchmark；没有计算或发布 P50、P95、speedup 或性能结论。

READY_FOR_A_E_BENCHMARK = false
