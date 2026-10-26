# Contract comparison Golden corpus

Baseline version: `contract-golden-v3`

This directory is the semantic truth source for the 15-contract blocking regression gate. The sealed baseline contains 15 contracts, 30 immutable PDF hashes and 318 semantic assertions. The v3 additions were visually checked against the source PDFs by the agent under the user’s explicit promotion instruction; no database review flags were changed.

The PDFs are private test data and are deliberately not stored here. CI or a developer must provision a versioned bundle and set:

```bash
export GOLDEN_CONTRACT_DATA_ROOT=/secure/path/contract-golden
```

The bundle layout and immutable SHA256/page-count metadata are defined by `manifest.json`. Missing, replaced, or malformed PDFs fail the suite; the tests never skip.

From the repository root, run the complete blocking workflow:

```bash
make golden-contract
```

It verifies the private bundle, replays all 15 contracts, applies the semantic blocking oracle, and checks whether protected Golden files changed. The first baseline addition or a later approved Golden update must explicitly use:

```bash
GOLDEN_BASELINE_CHANGE_APPROVED=1 make golden-contract
```

This variable only acknowledges that humans reviewed the displayed semantic Golden diff. It never generates, updates, or overwrites the manifest or oracle.

Oracle files contain stable `case_id` values and semantic assertions. They intentionally do not freeze runtime diff IDs, ordering, OCR confidence, diagnostics prose, or exact pixel coordinates. There is no automatic Golden update command.

## CI contract

CI remains platform independent. It only needs to mount the private bundle, export `GOLDEN_CONTRACT_DATA_ROOT`, and execute `make golden-contract`. Missing data, a hash/page mismatch, fewer than 15 executed contracts, a skipped blocking test, a positive/negative/review mismatch, an unexpected confirmed diff, or a bbox regression returns a non-zero exit code.

The checker compares working-tree changes with `HEAD`. In a clean checkout it compares `HEAD^...HEAD`; for a multi-commit change, CI should expose the target revision through the platform-neutral `GOLDEN_BASE_REF` environment variable so the complete review range is inspected.

## Protected baseline

The following files are high-risk review areas:

- `manifest.json`
- `oracle/*.json`
- `../../golden_contract/semantic_matcher.py`
- `../../golden_contract/test_contract_golden_blocking.py`
- `../../../scripts/check_golden_baseline_change.py`

The change checker prints added/removed cases plus decision, semantic, and bbox expectation changes. A failing Golden must be diagnosed as a code regression first. Golden may change only after a real business improvement is verified against the original contracts and the new truth is approved by a human reviewer.


## 2026-09-08 v2 historical reviewed update

See [UPDATE-v2.md](UPDATE-v2.md) for evidence, changed truth, and remaining blocking issues. The v2 update keeps all 20 PDF hashes unchanged and does not imply that the current algorithm passes every assertion.

The following command describes the historical v2 replay. Its source task IDs do not match the current v2.1 manifest; do not use that old directory to claim validation of the new actual tasks. For v2.1 results, see [UPDATE-v2.1.md](UPDATE-v2.1.md).

Historical v2 command (requires the v2 baseline):

```bash
python backend/scripts/verify_golden_contract_replay.py \
  --results-root output/analysis/eight-task-fix/release \
  --data-root output/analysis/eight-task-fix/golden-inputs \
  --report output/analysis/golden-v2-update/replay-report.json
```

The verifier requires all completed result artifacts listed in the current manifest, matching business-code fingerprints and immutable input hashes. Missing data or any semantic failure returns non-zero. This is a separate offline check; it does not replace `make golden-contract`.


## Historical v2.1 update

[UPDATE-v2.1.md](UPDATE-v2.1.md) records the two user-selected completed tasks, unchanged PDF hashes, and 90 passing assertions. The remaining eight oracle files are unchanged.

## Current v3: five-task promotion (2026-09-08)

[UPDATE-v3.md](UPDATE-v3.md) records promotion of all five tasks, 56 new assertions, source-PDF review and verification results. [The intake](intake/2026-09-08-five-tasks/README.md) retains the 55 source observations and their review decisions. The local versioned private bundle is `storage/golden-intake/contract-golden-v3/` (relative to the repository root). Earlier bundles contain only ten contracts and cannot validate v3.

七条新增失败的根因修复、回归边界与最新验收记录见 [UPDATE-v3-root-fixes.md](UPDATE-v3-root-fixes.md)。

## 2026-10-08 RETURN_CONFIRMATION 更新

用户授权更新任务 `f566b562-9a68-4187-a2f9-32678e26d020` 对应的 Golden。该合同新增8条负例、修订1条真实安装新增定位，15→23条；全库255→263条。PDF哈希与其他14份oracle保持不变。详见 [更新记录](UPDATE-20261008-return-confirmation.txt)。基线更新不表示当前任务全部通过。

随后按用户授权，将该合同验证来源更新为修复后的任务 `bc3bbb91-4d4c-4bd4-928a-9843540998c0`。该任务显示20条差异；Golden仍是23条验收断言，其中18条通过、5条既有失败。公司名误报与盖章误高亮两条负例均已通过，断言及阈值未改变。详见 [修复后来源更新记录](UPDATE-20261008-return-confirmation-party-verified.txt)。

## 2026-10-08 KAILI_FOUR_PROJECTS 页码修复验证更新

按用户授权，将来源更新为 `928b3727-d136-4a71-b767-2182e2d328d7`。原14条断言保留，新增第9页相同页脚不得报差异的负例；该合同共15条规则，全库264条。当前任务仍有17条实际差异，规则数与差异数分别计数。详见 [更新记录](UPDATE-20261008-kaili-footer-page-number.txt)。

## 2026-10-09 HUANGHE_HAIXI 表格行修复验证更新

按用户授权，将来源更新为 `f73b9b86-ffe0-443f-997d-7b8fbe5f3918`。原14条断言不变，新增第3页相同报价元信息及印章碎字不得报表格差异的负例，共15条，全库265条。当前任务仍有20条实际差异；15条规则中12条通过、3条既有失败。详见 [更新记录](UPDATE-20261009-huanghe-table-row.txt)。

## 2026-10-09 PO202604090100 table and clause ownership update

The reviewed source is task `10052fcf-a151-41bd-aca9-e6b928400ff7`. All 17 existing assertions remain unchanged. Two negative assertions prohibit false changes to the identical first-page table and clause 22.5 contaminated by stamp text or the next quotation page’s company heading. This contract now has 19 assertions; the corpus has 267. The task still has 17 actual differences. Existing failures remain visible. See `UPDATE-20261009-po-table-and-clause.txt` for validation and evidence.

## 2026-10-10 STATE_GRID_NORTH_CHINA 结构归属与附件边界更新

按用户授权，将验证来源更新为 `c06e36ff-0456-42e9-b1a1-dee01d0b0ac9`。保留原21条规则，新增相同页眉、保密合同入口、项目服务期限及验收、地址续行不得报正文差异的4条负例。本合同共25条规则，全库271条；当前任务仍为22项实际差异。详见 [更新记录](UPDATE-20261010-state-grid-structure.txt)。


## 2026-10-10 HUANENG_DINGBIAN 更新

用户授权将来源更新为任务 `ea7eef80-7d17-475b-864d-c328ab222c71`。保留原14条业务与复核断言，新增6条已验证误报的负例：封面中国华能、第9/15页千分号、第18页实时性指标、第38/40页印刷签署字段。本合同14→20条规则，全库271→277条；任务页面仍为16项差异。详见 [更新记录](UPDATE-20261010-huaneng-cover-permille.txt)。既有黄金失败不因本次基准更新自动消除。

## 2026-10-14 SUIZHOU_CHANGYUAN 封面漏报验证更新

按用户授权，验证来源更新为 `bbdbdf77-c3d2-46e0-861e-b04c189b9b67`。修订标题旁未识读笔迹、下方红章误作文字两条旧标注；新增第1/34/36/42页红章定位和扫描软件文字负例，共88条规则，全库282条。任务仍为129条实际差异；88条规则中71条通过、17条既有失败。其他14份oracle与PDF哈希不变。详见 [更新记录](UPDATE-20261014-suizhou-cover-recall.txt)。

## 2026-10-14 HANENG_GONGHE 更新

用户当前页面任务a42a0744对应汉能共和黄金规则7→12条，全库282→287条。新增盖章误删、页边章碎片重复计数负例及第5页两处印章定位；修订第1/3页未经确认的印章归属。来源保留当前任务，修复后5e995ea8另作验证来源。详见 [更新记录](UPDATE-20261014-haneng-seal-label.txt)。

## 2026-10-15 TIELING_SERVICE 更新

按用户授权，来源更新为修复后任务 `056488e0-f88a-4996-9644-dbab6fb01a89`。公司名与税号误报、页边章碎片重复计数纳入保护，补充第3页页边章定位并修订旧乙方归属。本合同5→9条验收规则，全库287→291条；任务实际差异仍为2条。详见 [更新记录](UPDATE-20261015-tieling-fields-seals.txt)。

## 2026-10-15 LIANJIANG_PINGPO 符号与圆章定位更新

按用户授权，来源更新为 `fd85a8b0-eca9-4f18-ac57-c0b31defb6bd`。新增4条规则，纠正1条旧日期乱码真值；本合同9→13条，全库291→295条。当前任务仍为11条差异；黄金集保留页边章分类、日期识读和日期污染正文的问题，不代表当前结果全部正确。详见 [更新记录](UPDATE-20261015-lianjiang-symbols-seal.txt)。

## 2026-10-20 TIANJIN_XINKOU 更新

按用户授权，验证来源更新为 `b0384a14-9a6a-4364-a539-e748f7d65227`。新增第5页甲方盖章标签不得误报变化、第1页空白签订地点不得报字段变化两条负例；修订骑缝章旧 `signing_region` 分类为 `seal`，保留必须存在及原定位阈值。本合同2→4条规则，全库295→297条；实际任务仍为2项印章差异。其他14份oracle和30份PDF哈希不变。详见 [更新记录](UPDATE-20261020-tianjin-label-cover.txt)。

## 2026-10-20 HUABEI_FIVE_STATIONS 区域归属与跨页字段更新

按用户授权，验证来源更新为重启后任务 `8c97ce9a-96e2-44dd-ae30-c73336d4c108`。保留原17条规则，新增第29页跨页日期、第26页付款申请正文、第15页场站标题、第27页字段标签及第28页标签双向误报共6条负例。本合同17→23条规则，全库297→303条；任务实际16条差异。新规则6/6通过并能阻断修复前的6条误报；原有5条失败继续保留。其他14份oracle及30份PDF哈希不变。详见 [更新记录](UPDATE-20261020-huabei-regions.txt)。

## 2026-10-20 SHENNENG_NAIMAN 姓名章与识读误报更新

按用户授权，来源更新为 `26a31da6-e826-4f0f-9240-84cab24d2165`。保留原13条规则的业务要求与定位阈值，修正5条印章来源分类，新增6条表格、项目名称和金额区间误报负例。奈曼13→19条，全库303→309条；当前任务仍为14项差异，19条规则全部通过，另有1条乙方签署日期待复核警告。旧冻结提取输入仍触发2条新增表格负例，未掩盖该限制。详见 [更新记录](UPDATE-20261020-naiman-seals-and-ocr.txt)。

## 2026-10-26 SUIZHOU_CHANGYUAN 金额OCR更新

按用户授权，来源更新为 `d2464761-6d53-45c6-a97d-27aa2a481c8c`。修订第3页总价真值及定位，新增两侧仟/任不得误报变化的负例。本合同88→90条，全库309→311条；当前任务仍为130项差异。其余87条规则、其他14份oracle和全部PDF哈希不变。验证及既有失败见 [更新记录](UPDATE-20261026-suizhou-amount-ocr.txt)。

## 2026-10-26 NUOYANG 已验证误报更新

用户授权将来源更新为 `2aa339c7-fe60-4b9e-b718-e909167edcf4`。原19条规则原样保留，新增7条封面标识、页码、字体批注、签署字段、联系占位符及含/合误报负例。本合同19→26条，全库311→318条；当前任务21条底层记录、18项页面差异。详见 [更新记录](UPDATE-20261026-nuoyang-reviewed-repairs.txt)。
