# 独立 issue：三位条款编号被当作数量过滤

状态：记录，未修改 ClauseSplitter。此项不属于 B 的 25 个净新增。

B 原文编号为 1–320。Baseline 与最终 Challenger 均只建立 1–99 的数字编号边界，100–320 共 221 项没有独立边界。

`ClauseNumberParser` / `_parse_marker` 可以解析 `100. Supplier shall provide the service.`；实际阻断发生在 `backend/app/services/clause_splitter.py` 的 `_is_quantity_or_amount_marker`：`re.fullmatch(r"\d{3,}(?:\.\d+)?", clause_no or "")` 返回 true，ClauseHeading candidate 随之为 None。99 的 candidate score 为 0.70，100/101/320 的 candidate 为 None。

相同 text/type 直接调用 downstream 可稳定重现，因此另立 ClauseSplitter issue 有依据。后续若获准处理，需要同时覆盖真实金额／数量、三位及更多位连续编号、附件数字项，不能只删除数量防护以通过 B。

证据：[最终 B 与 parser probe](../../output/analysis/extraction-challenger-business-equivalence/B/final-verification.json)。
