# MinerU 对页眉污染、附件边界与分段不一致的对应能力

日期：2026-10-09。对应任务：`c6f9fe7d-7b14-4ee2-bdb8-3f3e88ab976e`。本次为官方源码核验；没有安装模型、运行该合同的 MinerU 全流程、修改业务代码、黄金集或任务结果。

## 结论

MinerU 有可以借鉴的**页眉页脚标签纠偏、独立的页面辅助块、标题阻断段落延续、非破坏性跨页续接标记**。这些机制能够直接对应当前误报的前两层成因，而且不要求启用重型 VLM。它没有在本次检查路径中提供“两份合同一对多条款匹配”的成品解法，合同附件实例和签署地址字段也仍需本项目处理。

最佳借鉴方向是把现有 PP-DocLayoutV3 的输出交给统一归属与边界规则，再保留原始块进行比较；没有证据支持为了这两个误报整体替换为 MinerU 或把 V3 更换为 V2。

## 版本与证据边界

- GitHub release API 与官方 release 页核实最新版本为 **MinerU 4.0.11**，发布于 `2026-10-08T18:22:41Z`；tag `mineru-4.0.11-released` 解引用到 commit **`f232373fb319d63e82aefd30be40a2ccc6005d73`**。搜索引擎缓存仍显示 4.0.9，因此不使用缓存确定最新版本。[官方 release][release]
- 同时核对项目此前记录的 **4.0.10** commit **`c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93`**：本文所用 `postprocess/document.py`、`title_leveling.py`、`llm_aided.py`、PDF `normalization.py` 与 4.0.11 文件完全一致；`pp_doclayout_v2_base.py` 仅首部依赖说明不同，本文讨论的算法相同。[4.0.10 布局后处理][oldlayout]
- 4.0.11 要求 `docvortex>=0.5.12,<1`；本次追溯依赖源码固定 **DocVortex 0.5.12**，commit **`ed6c40396d8685025d2e16a4a9ce0e9201d1df07`**。4.0.10 最低要求是 0.5.4，不能把本文依赖行为无条件等同于所有 4.0.10 环境。[4.0.11 依赖][deps] [4.0.10 依赖][olddeps]
- 4.x `basic` 官方明确使用小模型且不需要本地 VLM；`standard` / `advanced` 才包含 VLM。本文不是旧 3.x `pipeline` / `hybrid` 接口研究。[固定版本档位文档][tiers]

## 1. 页眉：有直接对应的纠偏与隔离机制

`PPDocLayoutV2PostProcessor._relabel_header_footer_boundary_blocks`（本地原始源码第 537 行）先寻找已有 header/footer 锚点，再按坐标修正边界内的普通块：最靠下的页眉锚点之上改标 header；最靠上的页脚锚点之下、且横向范围合格改标 footer。页面上下 30% 内的 number 块还能提供辅助分隔线。`_reclassify_header_footer_by_page_half` 纠正上下半页颠倒的 header/footer 标签。这是模型输出后的确定性规则，不需要 VLM。[布局后处理源码][layout]

对本任务的启发：同一页眉被切为“编号”和“技术服务合同”两个框时，只要有可信页眉锚点，几何纠偏可以补齐同区域归属。之后正文切分必须消费同一份角色结果，不能在页眉比较中认为它是页眉、正文模块又认为它是正文。

限制：这些规则依赖已存在且正确的锚点；没有锚点就无法凭空建立它，也不是跨页重复文本算法。它不会自动消除两份合同“一块对两块”的比较差异。本项目仍应结合已有页眉重复证据和同一行片段聚合，并避免仅因靠近页面顶部就剔除真正标题。[布局后处理源码][layout]

## 2. 新版保留辅助块，不能沿用旧版 discarded_blocks 契约

DocVortex 的 `PageAuxTextBlock` 为 header/footer/page_number/aside_text 建立专门类型，保存在 `PageInfo.blocks`；默认 Markdown 渲染时才过滤页面辅助块。其意义是**保存文字和位置，只决定它不参与正文输出**，不需要删除原始内容。[类型定义][schema] [Markdown 输出][markdown]

4.x 本次核验的严格 PageInfo 结构没有独立 `discarded_blocks` 字段，因此不能直接拿旧版本教程中的该字段设计新接入。合同对比也不应直接比较默认 Markdown：页眉本身可能真的变化，应该进入页眉专用比较，而不是彻底消失。

## 3. 保密合同标题：标题类别不同，但都能阻断段落延续

DocVortex `paragraphs.py` 的 `SECTION_MERGE_BARRIER_TYPES` 同时包含 `DOC_TITLE` 和 `PARAGRAPH_TITLE`。`_find_previous_text_block` 遇到其中任一种都停止向前寻找续段。页眉页脚等辅助块可以被跳过，但不会因此成为段落正文。[段落规则][paragraphs]

这与本项目“看到附件标志后禁止 doc_title 切分，却允许 paragraph_title 切分”的不对称规则直接不同。可借鉴的是：**两类可信标题都建立最基本的文本边界，标题具体级别不能取消这一边界。** 它能防止“保密合同”正文跨过标题被吞入前一个附件。

MinerU 并没有把两类标题粗暴统一成 text/title：严格 schema 仍保留 DocTitle（level 1）和 ParagraphTitle（level 2–6）。PDF normalization 用 layout doc_title 框与 VLM title 重叠来区分两类，也不是基于两份合同的相互对照。[类型定义][schema] [PDF 标题规范化][normalization]

限制：版面标题边界不等于“附件三”这个业务实例。目录中的附件项、附件封面、实际正文入口仍须区分；没有证据表明 MinerU 直接生成本项目需要的 appendix_instance_id，或者保证两份不同版式合同的标题层级完全相同。

## 4. 跨页段落：保留原块，以 continues_prev 表达关系

`merge_para_text_blocks` 不把原始块物理拼成一个丢失边界的大字符串，而是给可延续 text/ref_text 标记 `continues_prev=True`。它检查同页或相邻页、文本首尾、行方向、行框几何，并尊重 `_paragraph_boundary`，遇标题停止。[段落规则][paragraphs] [调用顺序][pages]

对当前项目的价值：原始文字、页号、位置可以保留；比较层需要协调一段对两段时，可以临时组合范围，再把差异回投原块，避免被错误的提取分段彻底锁住。

不能照搬的反例：`_has_mergeable_text_boundary` 会拒绝普通正文以数字或大写字母开头的续接。“7号金隅智造工场N6”以数字开头，所以这条通用段落规则本身无法承诺修复“27号”地址续行；签署字段恢复需要单独考虑地址标签、换行坐标和上下文。[段落规则][paragraphs]

还需保留本项目自己的证据：DocVortex 在最终对象化前会清理 `lines`、`score`、`label` 等临时字段，不能假设其最终 JSON 完整满足字符级高亮契约。[元数据清理][pages]

## 5. 标题 LLM 只是可选增强，不是这次的必要解法

MinerU 先调用 DocVortex 确定性后处理，再按配置启用 LLM。`_collect_paragraph_title_groups` 遇 DocTitle 将其固定为 1 级并开始新的标题分组；LLM 对已有 ParagraphTitle 列表赋 2–6 级。输入仅有标题文本与序号，不是整份合同正文，也不是 Original/Compare 双侧文本。标题分级仅在整本文档输入上启用。[后处理入口][document] [标题分级][title] [启用条件][aided]

因此它不会自动恢复被抑制的合同切分、不负责地址归属、不提供条款一对多比较。为这两个已经识别出文字的问题新增该 LLM，不是必要优先项。

## 适用矩阵

| 当前问题 | MinerU/DocVortex 已验证机制 | 本项目应借鉴 | 仍需项目实现 |
|---|---|---|---|
| 页眉一边 header、一边 text | 锚点几何纠偏；独立辅助块；输出层过滤 | 统一角色结果并同步正文、页眉比较 | 跨页重复证据；页眉一块/两块聚合 |
| doc_title 与 paragraph_title 导致不同切分 | 两种标题都是段落延续障碍 | 标题边界与标题级别分离 | 目录/封面/附件正文入口；附件实例 |
| 地址“27号”断成“2”与“7号” | 通用续接标记；但数字开头有拒绝规则 | 非破坏性保存块与续行证据 | 签署地址字段的专门续行收拢 |
| 原稿一大段、对比稿两小段 | 原块与 continues_prev 可表达关系 | 在保留位置基础上协调范围 | 单份解析器不负责双份合同的一对多匹配 |
| 编号标题强匹配掩盖正文低覆盖 | 本次检查的解析路径无此业务匹配功能 | 无可直接替换的解析配置 | 不完整覆盖时检查剩余文本，避免把未匹配直接当新增 |

## 推荐验证顺序（未执行）

1. 在当前模型结果上离线套用统一页眉归属，验证“SGTYHT/23-JS-004 技术服务合同”只进入页眉流；同时把真实改过合同编号作为阳性样本，必须仍报变化。
2. 对 doc_title/paragraph_title 都建立标题边界，再区分附件目录、封面与正文入口，验证两侧“保密合同”范围一致。
3. 加入一对多范围协调，使用“同文不同分段”负例与“保密条款内实际改字”阳性样本，验证结构修复不会掩盖真实差异。
4. 地址字段单独验证“27号”续行，保留字符级位置。不用通用合段规则掩盖跨字段拼接。

上述为源码支持的设计建议，不能表述为 MinerU 已实测消除本任务误报。

[release]: https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.11-released
[deps]: https://github.com/opendatalab/MinerU/blob/f232373fb319d63e82aefd30be40a2ccc6005d73/pyproject.toml#L42
[olddeps]: https://github.com/opendatalab/MinerU/blob/c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93/pyproject.toml#L42
[oldlayout]: https://github.com/opendatalab/MinerU/blob/c221cc41bc911ad0df3eaeda97acf6a1bfe9bf93/mineru/model/layout/pp_doclayout_v2_base.py#L537
[tiers]: https://github.com/opendatalab/MinerU/blob/f232373fb319d63e82aefd30be40a2ccc6005d73/docs/zh/usage/tiers.md
[layout]: https://github.com/opendatalab/MinerU/blob/f232373fb319d63e82aefd30be40a2ccc6005d73/mineru/model/layout/pp_doclayout_v2_base.py#L537
[schema]: https://github.com/myhloli/docvortex/blob/ed6c40396d8685025d2e16a4a9ce0e9201d1df07/src/docvortex/schema.py#L507
[markdown]: https://github.com/myhloli/docvortex/blob/ed6c40396d8685025d2e16a4a9ce0e9201d1df07/src/docvortex/render/_internal/markdown/renderer.py#L65
[paragraphs]: https://github.com/myhloli/docvortex/blob/ed6c40396d8685025d2e16a4a9ce0e9201d1df07/src/docvortex/postprocess/paragraphs.py
[pages]: https://github.com/myhloli/docvortex/blob/ed6c40396d8685025d2e16a4a9ce0e9201d1df07/src/docvortex/postprocess/pages.py
[normalization]: https://github.com/opendatalab/MinerU/blob/f232373fb319d63e82aefd30be40a2ccc6005d73/mineru/backend/analysis/pdf/normalization.py#L44
[document]: https://github.com/opendatalab/MinerU/blob/f232373fb319d63e82aefd30be40a2ccc6005d73/mineru/backend/postprocess/document.py#L12
[title]: https://github.com/opendatalab/MinerU/blob/f232373fb319d63e82aefd30be40a2ccc6005d73/mineru/backend/postprocess/title_leveling.py#L18
[aided]: https://github.com/opendatalab/MinerU/blob/f232373fb319d63e82aefd30be40a2ccc6005d73/mineru/backend/postprocess/llm_aided.py#L17
