# Signing Region Continuation Bbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand signing-region evidence boxes so top-of-page signing pages include the full continuous signing form, while preventing padded signing boxes from excluding adjacent body clauses.

**Architecture:** Keep the fix in the backend signing-region pipeline. `SigningBlockDetector` should merge same-page continuation rows such as address, representative, signature, postcode, and date into the detected signing block when a top signing-page context is already found. It should also recognize business/contact/account fields below a `签署页`/`签字页` page-title context, or on the next page after an `以下无正文`/terminal signing context when OCR or scan quality drops party/seal/signature anchors. `SigningClauseDocumentBuilder` should only use bbox-overlap fallback for text blocks that also look like signing text, so detector padding does not remove nearby contract clauses. `SigningRegionExtractor` and `SigningRegionDiffBuilder` can keep using the detected block bbox, so the frontend receives corrected evidence without UI changes.

**Tech Stack:** Python, FastAPI backend models, existing Pydantic `Document`/`TextBlock`/`SigningBlock` models, pytest.

---

## File Structure

- Modify: `backend/app/services/signing_region/block_detector.py`
  - Add signing-continuation regexes.
  - Add supply/demand and business signing form aliases such as `供方`/`需方`, `单位名称`, `开户银行`, `账号`, `税号`, and `邮政编码`.
  - Detect mid-page signing areas that follow a terminal clause such as `本合同经双方授权代表签字及盖章后生效`.
  - Detect business/contact/account field clusters that appear below a same-page `签署页`/`签字页` title even when OCR misses party, seal, and signature labels.
  - Detect business/contact/account field clusters on the page immediately after `以下无正文`/terminal signing context when the current page is the final or near-final page.
  - Add a conservative expansion pass for top signing-page clusters.
  - Keep body clauses and footer/page-number blocks out of signing blocks.
- Modify: `backend/app/services/signing_region/clause_document.py`
  - Keep direct `source_block_ids` exclusion.
  - Make bbox-overlap fallback semantic-aware so adjacent body text is not excluded just because signing bbox padding overlaps it.
- Modify: `backend/tests/test_signing_structure_detector.py`
  - Add unit regression tests for the real failure shape from `storage/tasks/dc69d8f4-9b14-420a-9e8a-161c6c5646a5`.
  - Add unit regression tests for `storage/tasks/3154a3a4-109f-4927-adec-e95c63e6ea31`, where the compare-side signature table only exposes `供方`/`需方` and business form fields.
  - Add unit regression tests for `storage/tasks/3627991f-2426-4a82-ade2-d02237954df2`, where the compare-side signing page OCR only exposes `签署页` plus business/contact/account fields.
  - Add unit regression tests for `storage/tasks/3627991f-2426-4a82-ade2-d02237954df2` compare page 24, where the scan exposes only lower business/contact fields on the page after `以下无正文`.
  - Add unit regression tests for `storage/tasks/6219c7d7-8df7-4b74-b57c-44edccad2b5f`, where the signature area starts around the middle of the final page after a terminal signing clause.
- Modify: `backend/tests/test_signing_clause_document.py`
  - Add regression tests for `storage/tasks/75db9356-c540-4a02-919e-44c0dd8f5f2b`, where the padded signing bbox overlapped `14.2` text.
- Modify: `backend/tests/test_signing_region_pipeline.py`
  - Add pipeline-level evidence bbox regression test to prove `DiffItem.original_evidence[].bbox` uses the expanded region.
  - Add pipeline-level clause-exclusion regression test to prove adjacent `14.2` text remains in the clause document.

No frontend changes are needed. The frontend is rendering the bbox it receives.

---

### Task 1: Add Detector Regression Tests

**Files:**
- Modify: `backend/tests/test_signing_structure_detector.py`

- [ ] **Step 1: Add a failing test for top signing-page continuation rows**

Append this test after `test_detector_finds_bottom_mixed_page_signing_block`:

```python
def test_detector_expands_top_signing_page_continuation_rows() -> None:
    page = Page(
        page_no=9,
        width=595,
        height=842,
        blocks=[
            _block("context", "签字页，此页无正文", _bbox(62, 67, 178, 81), page_no=9),
            _block("party_a", "甲方：南京瑞尚电力科技有限公司", _bbox(62, 117, 236, 131), page_no=9),
            _block("party_b", "乙方：国能日新科技股份有限公司", _bbox(301, 117, 471, 131), page_no=9),
            _block("seal_a", "(盖章)", _bbox(82, 145, 127, 167), page_no=9),
            _block("seal_b", "(盖章)", _bbox(322, 144, 367, 167), page_no=9),
            _block("address_a", "地址：南京市江宁区诚信大道", _bbox(62, 179, 226, 194), page_no=9),
            _block("address_b", "地址：北京市海淀区软件园", _bbox(301, 179, 454, 194), page_no=9),
            _block("address_a_2", "19号9幢", _bbox(62, 211, 118, 226), page_no=9),
            _block("address_b_2", "A座", _bbox(301, 211, 332, 226), page_no=9),
            _block("rep_a", "法人代表或授权委托人：", _bbox(62, 242, 198, 257), page_no=9),
            _block("rep_b", "法人代表或授权委托人：", _bbox(301, 242, 437, 257), page_no=9),
            _block("sign_a", "(签字)", _bbox(166, 269, 211, 292), page_no=9),
            _block("sign_b", "(签字)", _bbox(405, 269, 450, 292), page_no=9),
            _block("postcode_a", "邮编：211100", _bbox(62, 302, 145, 322), page_no=9),
            _block("postcode_b", "邮编：100000", _bbox(301, 302, 384, 322), page_no=9),
            _block("date_a", "日期：", _bbox(62, 331, 103, 353), page_no=9),
            _block("date_b", "日期：", _bbox(301, 331, 342, 353), page_no=9),
            _block("footer", "9", _bbox(292, 800, 303, 813), page_no=9),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert set(block.source_block_ids) >= {
        "context",
        "party_a",
        "party_b",
        "seal_a",
        "seal_b",
        "address_a",
        "address_b",
        "address_a_2",
        "address_b_2",
        "rep_a",
        "rep_b",
        "sign_a",
        "sign_b",
        "postcode_a",
        "postcode_b",
        "date_a",
        "date_b",
    }
    assert "footer" not in block.source_block_ids
    assert block.bbox.y1 >= 365
    assert block.bbox.y1 < 430
    assert "top_signing_continuation_expanded" in block.confidence_reasons
```

- [ ] **Step 2: Add a body-clause guard test**

Append this test immediately after the previous test:

```python
def test_detector_top_signing_expansion_keeps_body_clause_out() -> None:
    page = Page(
        page_no=9,
        width=595,
        height=842,
        blocks=[
            _block("context", "签字页，此页无正文", _bbox(62, 67, 178, 81), page_no=9),
            _block("party_a", "甲方：A公司", _bbox(62, 117, 160, 131), page_no=9),
            _block("party_b", "乙方：B公司", _bbox(301, 117, 399, 131), page_no=9),
            _block("seal_a", "(盖章)", _bbox(82, 145, 127, 167), page_no=9),
            _block("seal_b", "(盖章)", _bbox(322, 144, 367, 167), page_no=9),
            _block("body", "本合同经双方签字盖章后生效，双方应当履行合同约定义务。", _bbox(62, 210, 520, 232), page_no=9),
            _block("date_a", "日期：", _bbox(62, 331, 103, 353), page_no=9),
            _block("date_b", "日期：", _bbox(301, 331, 342, 353), page_no=9),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    assert "body" not in result.blocks[0].source_block_ids
    assert {"date_a", "date_b"}.issubset(result.blocks[0].source_block_ids)
```

- [ ] **Step 3: Add a supply/demand signing table regression test**

Append this test immediately after the previous test:

```python
def test_detector_recognizes_supply_demand_signing_table_after_signature_page_context() -> None:
    page = Page(
        page_no=2,
        width=597,
        height=819,
        blocks=[
            _block("body", "十二、本合同自双方签字盖章之日起生效。本合同1式4份", _bbox(68, 202, 386, 214), page_no=2),
            _block("context", "以下无正文", _bbox(68, 245.5, 129.5, 261), page_no=2),
            _block("title", "签字页", _bbox(280, 335, 327, 354.5), page_no=2, block_type="paragraph_title"),
            _block(
                "business_table",
                "需\n方\n供\n方\n电话：010-83458100\n电话：18097182156\n传真：\n开户银行：招商银行北京大屯路支行\n"
                "开户银行：招商银行股份有限公司西宁生物园区支行\n账号：110904199110901\n号：972900591810801\n"
                "税号：91630000MA7588E76L\n号：911101086723891430\n邮政编码：100096\n邮政编码：813000",
                _bbox(63, 389, 545, 654),
                page_no=2,
                block_type="table",
            ),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"context", "title", "business_table"}.issubset(block.source_block_ids)
    assert block.confidence >= 0.5
    assert "business_signing_form_fields" in block.confidence_reasons
```

- [ ] **Step 4: Add a business/contact signing-page regression test**

Append this test immediately after the previous test:

```python
def test_detector_recognizes_business_contact_fields_after_signature_page_title() -> None:
    page = Page(
        page_no=12,
        width=606,
        height=826,
        blocks=[
            _block("title", "签署页", _bbox(258.5, 82, 341, 102.5), page_no=12, block_type="paragraph_title"),
            _block("address", "地址：北京市西城区广安门内大街地址：北京市海淀区建材城中路2", _bbox(91.5, 270.5, 512, 293), page_no=12),
            _block("address_more", "号院", _bbox(312, 295.5, 340, 317.5), page_no=12),
            _block("contact_a", "联系人：环加飞", _bbox(92.5, 323.5, 192, 341), page_no=12),
            _block("contact_b", "联系人：刘玉良", _bbox(312, 320, 413.5, 337), page_no=12),
            _block("phone_a", "电话：010-83582793", _bbox(93.5, 347, 218.5, 365), page_no=12),
            _block("phone_b", "电话：18811089109", _bbox(313, 343, 433.5, 361.5), page_no=12),
            _block("fax_a", "传真：010-83582600", _bbox(93.5, 370.5, 219, 388.5), page_no=12),
            _block("fax_b", "传真：010-83458100", _bbox(313, 367, 439, 384.5), page_no=12),
            _block("email", "Email: huan.jiafei@nc.sgcc.com.cn Email: yuliang.liu@sprixin.com", _bbox(93.5, 389.5, 503, 414), page_no=12),
            _block("bank", "开户银行：中国工商银行股份有限开户银行：招商银行北京大屯路", _bbox(94.5, 412, 511.5, 436), page_no=12),
            _block("account_a", "账号：901027101", _bbox(95, 466, 202.5, 484), page_no=12),
            _block("account_b", "账号：110904199110901", _bbox(314.5, 461, 462.5, 478.5), page_no=12),
            _block("credit", "统一社会信用代码：911100000536统一社会信用代码：9111010867", _bbox(95.5, 482.5, 513, 506.5), page_no=12),
            _block("footer", "12", _bbox(305.5, 764.5, 316, 774), page_no=12, block_type="number"),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {
        "title",
        "address",
        "contact_a",
        "contact_b",
        "phone_a",
        "phone_b",
        "bank",
        "account_a",
        "account_b",
        "credit",
    }.issubset(block.source_block_ids)
    assert "footer" not in block.source_block_ids
    assert "page_signing_context_business_fields" in block.confidence_reasons
    assert block.bbox.y0 < 90
    assert block.bbox.y1 > 520
```

- [ ] **Step 5: Add a previous-page signoff business-only regression test**

Append this test immediately after the previous test:

```python
def test_detector_recognizes_business_only_page_after_previous_no_body_context() -> None:
    previous_page = Page(
        page_no=23,
        width=595,
        height=842,
        blocks=[
            _block("effective_1", "方公章或合同专用章之日起生效。合同签订日期以最后一方签署并加", _bbox(91.5, 72.5, 510.5, 89), page_no=23),
            _block("effective_2", "盖公章或合同专用章的日期为准。", _bbox(93, 97, 296, 112), page_no=23),
            _block("copies_title", "14. 份数", _bbox(118, 136.5, 172, 154.5), page_no=23, block_type="paragraph_title"),
            _block("copies", "本合同一式捌份，甲方执肆份，乙方执肆份，具有同等效力。", _bbox(119, 175.5, 494, 194), page_no=23),
            _block("special_title", "15. 特别约定", _bbox(119.5, 214.5, 200, 234), page_no=23, block_type="paragraph_title"),
            _block("special", "本特别约定是合同各方经协商后对合同其他条款的修改或补充，", _bbox(120.5, 256, 507.5, 272.5), page_no=23),
            _block("no_body", "(以下无正文)", _bbox(126.5, 326.5, 214.5, 343.5), page_no=23),
        ],
    )
    signing_page = Page(
        page_no=24,
        width=595,
        height=842,
        blocks=[
            _block("header", "SGTYHT/23-JS-004 技术服务合同", _bbox(346, 41, 510, 53), page_no=24, block_type="header"),
            _block("address", "地址：北京市西城区广安门内大街地址：北京市海淀区建材城中路", _bbox(91, 289, 509, 310), page_no=24),
            _block("address_a_more", "482号", _bbox(91, 317, 132.5, 333.5), page_no=24),
            _block("address_b_more", "27号金隅智造工场N6", _bbox(312.5, 316.5, 452, 333), page_no=24),
            _block("contact_a", "联系人：环加飞", _bbox(93, 341.5, 192.5, 357), page_no=24),
            _block("contact_b", "联系人：刘玉良", _bbox(313, 339.5, 413, 356.5), page_no=24),
            _block("phone_a", "电话：010-83582793", _bbox(93, 364.5, 218, 381), page_no=24),
            _block("phone_b", "电话：18811089109", _bbox(314, 363.5, 434.5, 380), page_no=24),
            _block("fax_a", "传真：010-83582600", _bbox(92.5, 388, 218, 405), page_no=24),
            _block("fax_b", "传真：010-83458100", _bbox(314.5, 388.5, 438.5, 402), page_no=24),
            _block("email_a", "Email: huan.jiafei@nc.sgcc.com.cn", _bbox(91.5, 412.5, 302.5, 430), page_no=24),
            _block("email_b", "Email: yuliang.liu@sprixin.com", _bbox(311.5, 411.5, 502.5, 429.5), page_no=24),
            _block("credit_a", "统一社会信用代码：911100000536", _bbox(92.5, 434.5, 302, 451.5), page_no=24),
            _block("credit_b", "统一社会信用代码：91110108672", _bbox(313, 435, 507, 450), page_no=24),
            _block("credit_a_more", "21038D", _bbox(91.5, 460.5, 140, 476.5), page_no=24),
            _block("credit_b_more", "3891430", _bbox(313.5, 460, 364.5, 474.5), page_no=24),
            _block("footer", "24", _bbox(293.5, 759, 307, 770.5), page_no=24, block_type="number"),
        ],
    )
    document = Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=2,
        pages=[previous_page, signing_page],
        profile=DocumentProfile(
            filename="test.pdf",
            page_count=2,
            page_profiles=[
                PageProfile(page_no=23, width=595, height=842, page_role="body", text_block_count=len(previous_page.blocks)),
                PageProfile(page_no=24, width=595, height=842, page_role="body", text_block_count=len(signing_page.blocks)),
            ],
        ),
    )

    result = SigningBlockDetector().detect(document)

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.page_no == 24
    assert {
        "address",
        "address_a_more",
        "address_b_more",
        "contact_a",
        "contact_b",
        "phone_a",
        "phone_b",
        "email_a",
        "email_b",
        "credit_a",
        "credit_b",
    }.issubset(block.source_block_ids)
    assert "header" not in block.source_block_ids
    assert "footer" not in block.source_block_ids
    assert "previous_page_signing_context_business_fields" in block.confidence_reasons
    assert block.bbox.y0 < 290
    assert block.bbox.y1 > 475
```

- [ ] **Step 6: Add a final-page mid-page signing area regression test**

Append this test immediately after the previous test:

```python
def test_detector_recognizes_mid_page_signing_area_after_terminal_clause() -> None:
    page = Page(
        page_no=7,
        width=595,
        height=842,
        blocks=[
            _block("terminal", "22.3 本合同经双方授权代表签字及盖章后生效。", _bbox(63, 235, 293, 247), page_no=7),
            _block("party_a", "甲方：【南京国电南自电网自动化有限公", _bbox(62, 334, 284, 354), page_no=7),
            _block("party_b", "乙方：【国能日新科技股份有限公司】（盖章）", _bbox(277, 338, 520, 349), page_no=7),
            _block("sign_b", "授权代表签字：", _bbox(286, 371, 360, 384), page_no=7),
            _block("date_a", "日期：", _bbox(63, 508, 96, 523), page_no=7),
            _block("date_b", "日期：", _bbox(285, 507, 319, 523), page_no=7),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert {"party_a", "party_b", "sign_b", "date_a", "date_b"}.issubset(block.source_block_ids)
    assert "terminal_signing_clause_context" in block.confidence_reasons
    assert block.bbox.y0 < 335
    assert block.bbox.y1 > 520
```

- [ ] **Step 7: Run the focused detector tests and verify the new tests fail**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py -q
```

Expected before implementation:

```text
FAILED backend/tests/test_signing_structure_detector.py::test_detector_expands_top_signing_page_continuation_rows
FAILED backend/tests/test_signing_structure_detector.py::test_detector_recognizes_supply_demand_signing_table_after_signature_page_context
FAILED backend/tests/test_signing_structure_detector.py::test_detector_recognizes_business_contact_fields_after_signature_page_title
FAILED backend/tests/test_signing_structure_detector.py::test_detector_recognizes_business_only_page_after_previous_no_body_context
FAILED backend/tests/test_signing_structure_detector.py::test_detector_recognizes_mid_page_signing_area_after_terminal_clause
```

The failure should show that `address_a` or another lower signing field is missing from `block.source_block_ids`, or that `block.bbox.y1` is still around the top seal area instead of reaching the date row.

---

### Task 2: Add Pipeline Evidence Regression Test

**Files:**
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: Add a failing pipeline test**

Append this test after `test_signing_region_stage_builds_diff_and_covers_seal`:

```python
def test_signing_region_stage_uses_expanded_bbox_for_top_signature_page(tmp_path: Path) -> None:
    def page(swapped: bool) -> Page:
        first_party = TextBlock(
            block_id="party_b" if swapped else "party_a",
            page_no=9,
            text="乙方：国能日新科技股份有限公司" if swapped else "甲方：南京瑞尚电力科技有限公司",
            bbox=BBox(x0=62, y0=117, x1=236, y1=131),
        )
        second_party = TextBlock(
            block_id="party_a" if swapped else "party_b",
            page_no=9,
            text="甲方：南京瑞尚电力科技有限公司" if swapped else "乙方：国能日新科技股份有限公司",
            bbox=BBox(x0=301, y0=117, x1=471, y1=131),
        )
        return Page(
            page_no=9,
            width=595,
            height=842,
            blocks=[
                TextBlock(block_id="context", page_no=9, text="签字页，此页无正文", bbox=BBox(x0=62, y0=67, x1=178, y1=81)),
                first_party,
                second_party,
                TextBlock(block_id="seal_a", page_no=9, text="(盖章)", bbox=BBox(x0=82, y0=145, x1=127, y1=167)),
                TextBlock(block_id="seal_b", page_no=9, text="(盖章)", bbox=BBox(x0=322, y0=144, x1=367, y1=167)),
                TextBlock(block_id="address_a", page_no=9, text="地址：南京市江宁区诚信大道", bbox=BBox(x0=62, y0=179, x1=226, y1=194)),
                TextBlock(block_id="address_b", page_no=9, text="地址：北京市海淀区软件园", bbox=BBox(x0=301, y0=179, x1=454, y1=194)),
                TextBlock(block_id="rep_a", page_no=9, text="法人代表或授权委托人：", bbox=BBox(x0=62, y0=242, x1=198, y1=257)),
                TextBlock(block_id="rep_b", page_no=9, text="法人代表或授权委托人：", bbox=BBox(x0=301, y0=242, x1=437, y1=257)),
                TextBlock(block_id="sign_a", page_no=9, text="(签字)", bbox=BBox(x0=166, y0=269, x1=211, y1=292)),
                TextBlock(block_id="sign_b", page_no=9, text="(签字)", bbox=BBox(x0=405, y0=269, x1=450, y1=292)),
                TextBlock(block_id="postcode_a", page_no=9, text="邮编：211100", bbox=BBox(x0=62, y0=302, x1=145, y1=322)),
                TextBlock(block_id="postcode_b", page_no=9, text="邮编：100000", bbox=BBox(x0=301, y0=302, x1=384, y1=322)),
                TextBlock(block_id="date_a", page_no=9, text="日期：", bbox=BBox(x0=62, y0=331, x1=103, y1=353)),
                TextBlock(block_id="date_b", page_no=9, text="日期：", bbox=BBox(x0=301, y0=331, x1=342, y1=353)),
                TextBlock(block_id="footer", page_no=9, text="9", bbox=BBox(x0=292, y0=800, x1=303, y1=813)),
            ],
        )

    original = Document(filename="original.pdf", path="original.pdf", page_count=9, pages=[page(swapped=False)])
    compare = Document(filename="compare.pdf", path="compare.pdf", page_count=9, pages=[page(swapped=True)])
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

    SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_enabled=False,
    ).execute(ctx)

    assert len(ctx.signing_region_diffs) == 1
    diff = ctx.signing_region_diffs[0]
    assert diff.original_evidence[0].bbox.y1 >= 365
    assert diff.compare_evidence[0].bbox.y1 >= 365
    assert "address_a" in ctx.signing_region_debug["signing_blocks"]["original"][0]["source_block_ids"]
    assert "date_b" in ctx.signing_region_debug["signing_blocks"]["compare"][0]["source_block_ids"]
    assert "footer" not in ctx.signing_region_debug["signing_blocks"]["original"][0]["source_block_ids"]
```

- [ ] **Step 2: Run the pipeline test and verify it fails**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_uses_expanded_bbox_for_top_signature_page -q
```

Expected before implementation:

```text
FAILED backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_uses_expanded_bbox_for_top_signature_page
```

The failure should show the evidence bbox still ends near the top seal row, around `y1=185`, instead of including the lower date rows.

---

### Task 3: Protect Clause Exclusion From Padded Bbox Overlap

**Files:**
- Modify: `backend/app/services/signing_region/clause_document.py`
- Modify: `backend/tests/test_signing_clause_document.py`
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: Add a failing unit test for the 14.2 false exclusion**

Append this test after `test_builder_removes_high_confidence_signing_block_blocks_only` in `backend/tests/test_signing_clause_document.py`:

```python
def test_builder_keeps_adjacent_clause_line_when_signing_padding_overlaps() -> None:
    clause_tail = TextBlock(
        block_id="clause_tail",
        page_no=10,
        text="及付款方式、交付时间、验收标准。",
        bbox=_bbox(61.4, 597.9, 253.9, 610.5),
    )
    signing_party = TextBlock(
        block_id="signing_party",
        page_no=10,
        text="甲方：江苏东大金智信息系统有限公司乙方：国能日新科技股份有限公司",
        bbox=_bbox(87.0, 622.4, 482.9, 632.9),
    )
    signing_seal = TextBlock(
        block_id="signing_seal",
        page_no=10,
        text="(盖章)",
        bbox=_bbox(67.5, 643.4, 104.5, 658.9),
    )
    page = Page(page_no=10, width=595, height=842, blocks=[clause_tail, signing_party, signing_seal])
    doc = Document(filename="x.pdf", path="x.pdf", page_count=1, pages=[page])
    block = _signing_block(
        "SB-10-1",
        10,
        _bbox(48.0, 601.9, 500.9, 744.4),
        source_block_ids=["signing_party", "signing_seal"],
        text=f"{signing_party.text}\n{signing_seal.text}",
    )

    result = SigningClauseDocumentBuilder().build(doc, [block])

    assert [b.block_id for b in result.document.pages[0].blocks] == ["clause_tail"]
    assert result.excluded_block_ids == ["signing_party", "signing_seal"]
    assert [entry["block_id"] for entry in result.entries] == ["signing_party", "signing_seal"]
```

- [ ] **Step 2: Add a pipeline regression test that keeps 14.2 text in the clause document**

Append this test after `test_signing_region_stage_builds_diff_and_covers_seal` in `backend/tests/test_signing_region_pipeline.py`:

```python
def test_signing_region_stage_keeps_adjacent_14_2_clause_text(tmp_path: Path) -> None:
    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=10,
        pages=[
            Page(
                page_no=10,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="clause_tail",
                        page_no=10,
                        text="及付款方式、交付时间、验收标准。",
                        bbox=BBox(x0=61.4, y0=597.9, x1=253.9, y1=610.5),
                    ),
                    TextBlock(
                        block_id="signing_party",
                        page_no=10,
                        text="甲方：江苏东大金智信息系统有限公司乙方：国能日新科技股份有限公司",
                        bbox=BBox(x0=87.0, y0=622.4, x1=482.9, y1=632.9),
                    ),
                    TextBlock(
                        block_id="signing_seal_a",
                        page_no=10,
                        text="(盖章)",
                        bbox=BBox(x0=67.5, y0=643.4, x1=104.5, y1=658.9),
                    ),
                    TextBlock(
                        block_id="signing_seal_b",
                        page_no=10,
                        text="(盖章)",
                        bbox=BBox(x0=337.9, y0=643.4, x1=374.9, y1=658.9),
                    ),
                    TextBlock(
                        block_id="signing_rep_a",
                        page_no=10,
                        text="法人代表或授权委托人：",
                        bbox=BBox(x0=85.5, y0=668.9, x1=210.5, y1=679.5),
                    ),
                    TextBlock(
                        block_id="signing_rep_b",
                        page_no=10,
                        text="法人代表或授权委托人：",
                        bbox=BBox(x0=326.4, y0=668.9, x1=450.9, y1=679.5),
                    ),
                    TextBlock(
                        block_id="signing_date_a",
                        page_no=10,
                        text="日期：",
                        bbox=BBox(x0=84.5, y0=712.4, x1=121.0, y1=729.9),
                    ),
                    TextBlock(
                        block_id="signing_date_b",
                        page_no=10,
                        text="日期：",
                        bbox=BBox(x0=325.4, y0=712.4, x1=360.9, y1=729.9),
                    ),
                ],
            )
        ],
    )
    compare = original.model_copy(deep=True)
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

    SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_enabled=False,
    ).execute(ctx)

    assert ctx.clause_document_original is not None
    assert [block.block_id for block in ctx.clause_document_original.pages[0].blocks] == ["clause_tail"]
    excluded = ctx.signing_region_debug["clause_exclusion"]["original"]
    assert "clause_tail" not in {entry["block_id"] for entry in excluded}
```

- [ ] **Step 3: Run the clause-document test and verify it fails**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_clause_document.py::test_builder_keeps_adjacent_clause_line_when_signing_padding_overlaps -q
```

Expected before implementation:

```text
FAILED backend/tests/test_signing_clause_document.py::test_builder_keeps_adjacent_clause_line_when_signing_padding_overlaps
```

The failure should show `clause_tail` was incorrectly removed because its bbox overlaps the padded signing block.

- [ ] **Step 4: Run the pipeline regression test and verify it fails**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_keeps_adjacent_14_2_clause_text -q
```

Expected before implementation:

```text
FAILED backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_keeps_adjacent_14_2_clause_text
```

- [ ] **Step 5: Add semantic gating for bbox fallback matches**

In `backend/app/services/signing_region/clause_document.py`, insert these imports/constants after the existing imports:

```python
import re


SIGNING_BBOX_FALLBACK_RE = re.compile(
    r"甲方|乙方|丙方|丁方|盖章|签章|公章|签字|签名|法定代表人|法人代表|授权代表|授权委托人|"
    r"日期[:：]?|签订日期|签署日期|地址[:：]|住址[:：]|邮编[:：]"
)
NUMBERED_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十百千万0-9]+[、.．]|"
    r"\d+(?:\.\d+){0,4}[、.．]?)"
)
BODY_VERB_RE = re.compile(r"应当|负责|承担|履行|支付|违约|权利|义务|为准|合同经|生效|协商|约定")
VISUAL_BLOCK_TYPES = {"seal", "stamp", "image", "figure", "table"}
```

Then replace the bbox fallback loop in `_matching_signing_block`:

```python
        for signing_block in signing_blocks:
            if self._overlap_ratio(text_block.bbox, signing_block.bbox) >= self.overlap_threshold:
                return signing_block
```

with:

```python
        if not self._can_match_by_bbox_fallback(text_block):
            return None

        for signing_block in signing_blocks:
            if self._overlap_ratio(text_block.bbox, signing_block.bbox) >= self.overlap_threshold:
                return signing_block
```

Add this method before `_overlap_ratio`:

```python
    @staticmethod
    def _can_match_by_bbox_fallback(text_block: TextBlock) -> bool:
        block_type = (text_block.block_type or "").lower()
        if block_type in VISUAL_BLOCK_TYPES:
            return True

        text = re.sub(r"\s+", "", text_block.text or "")
        if not text:
            return False
        if NUMBERED_RE.match(text):
            return False
        if BODY_VERB_RE.search(text) and not SIGNING_BBOX_FALLBACK_RE.search(text):
            return False
        return SIGNING_BBOX_FALLBACK_RE.search(text) is not None
```

- [ ] **Step 6: Run clause document tests**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_clause_document.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 7: Run the pipeline regression test**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_keeps_adjacent_14_2_clause_text -q
```

Expected:

```text
... passed
```

---

### Task 4: Implement Conservative Signing Continuation Expansion

**Files:**
- Modify: `backend/app/services/signing_region/block_detector.py`

- [ ] **Step 1: Add role aliases, business form fields, and continuation regex constants**

Replace the existing `PARTY_RE` line with:

```python
PARTY_RE = re.compile(r"甲方|乙方|丙方|丁方|供方|需方|买方|卖方|采购方|供货方|委托方|受托方")
```

Insert these constants after `DATE_LABEL_RE`:

```python
BUSINESS_SIGNING_FIELD_RE = re.compile(
    r"单位名称|单位地址|地址|住址|通讯地址|联系地址|邮编|邮政编码|电话|传真|邮箱|开户行|"
    r"开户银行|银行账号|账号|帐号|帐\s*号|账\s*号|税号|纳税人识别号|统一社会信用代码"
)
TERMINAL_SIGNING_CLAUSE_RE = re.compile(
    r"本合同.*(?:签字|签名|盖章|签章).*生效|双方授权代表签字及盖章后生效|签字盖章之日起生效"
)
CONTINUATION_LABEL_RE = re.compile(
    r"地址|住址|通讯地址|联系地址|邮编|邮政编码|电话|传真|邮箱|开户行|银行账号|账号|"
    r"帐号|帐\s*号|账\s*号|税号|纳税人识别号|统一社会信用代码|日期"
)
ADDRESS_BODY_RE = re.compile(r"[省市区县镇乡村路街道号室楼层园区大厦]")
FOOTER_ONLY_RE = re.compile(r"^\s*(?:第\s*)?\d+\s*(?:页)?\s*$|^\s*[-—]\s*\d+\s*[-—]\s*$")
```

- [ ] **Step 2: Thread previous-page signing context through `detect`**

Replace `detect` with:

```python
    def detect(self, document: Document) -> SigningBlockDetectionResult:
        result = SigningBlockDetectionResult()
        page_roles = self._page_roles(document)
        previous_context_pages = self._previous_signing_context_pages(document)
        for page in document.pages:
            blocks = self._detect_page_blocks(
                page,
                page_roles.get(page.page_no, "body"),
                result,
                previous_signing_context=page.page_no in previous_context_pages,
            )
            result.blocks.extend(blocks)
            if blocks:
                result.pages.append(self._to_page(page, page_roles.get(page.page_no, "body"), blocks))
        return result
```

Replace the `_detect_page_blocks` signature with:

```python
    def _detect_page_blocks(
        self,
        page: Page,
        page_role: str,
        result: SigningBlockDetectionResult,
        *,
        previous_signing_context: bool = False,
    ) -> list[SigningBlock]:
```

Replace the candidate collection line with:

```python
        candidates = [
            block
            for block in page.blocks
            if self._is_candidate(block, page, previous_signing_context=previous_signing_context)
        ]
```

- [ ] **Step 3: Update `_detect_page_blocks` to expand each candidate cluster before scoring**

Replace this block:

```python
        signing_blocks: list[SigningBlock] = []
        for index, cluster in enumerate(self._cluster(candidates), start=1):
            text = "\n".join(block.text.strip() for block in cluster if block.text.strip())
            bbox = self._padded_union([self._effective_bbox(block) for block in cluster], page)
            score, reasons = self._score_cluster(cluster, page, page_role)
```

with:

```python
        signing_blocks: list[SigningBlock] = []
        consumed_block_ids: set[str] = set()
        for index, candidate_cluster in enumerate(self._cluster(candidates), start=1):
            cluster = [block for block in candidate_cluster if block.block_id not in consumed_block_ids]
            if not cluster:
                continue
            expanded_cluster = self._expand_signing_continuation(
                page,
                cluster,
                consumed_block_ids,
                previous_signing_context=previous_signing_context,
            )
            expanded_cluster = self._attach_signing_context_blocks(page, expanded_cluster, consumed_block_ids)
            expanded = len(expanded_cluster) > len(cluster)
            cluster = expanded_cluster
            text = "\n".join(block.text.strip() for block in cluster if block.text.strip())
            bbox = self._padded_union([self._effective_bbox(block) for block in cluster], page)
            score, reasons = self._score_cluster(
                cluster,
                page,
                page_role,
                previous_signing_context=previous_signing_context,
            )
            if expanded:
                reason = self._expansion_reason(
                    page,
                    cluster,
                    previous_signing_context=previous_signing_context,
                )
                if reason and reason not in reasons:
                    reasons.append(reason)
            consumed_block_ids.update(block.block_id for block in cluster)
```

- [ ] **Step 4: Update candidate gating for business-form and terminal-clause contexts**

Replace `_is_candidate` with:

```python
    def _is_candidate(
        self,
        block: TextBlock,
        page: Page,
        *,
        previous_signing_context: bool = False,
    ) -> bool:
        text = self._compact(block.text)
        if not text:
            return False
        bbox = self._effective_bbox(block)
        in_bottom = bbox.y1 >= page.height * self.bottom_ratio
        in_top = bbox.y0 <= page.height * self.top_continuation_ratio
        after_terminal_clause = self._is_after_terminal_signing_clause(block, page)
        after_signing_page_context = self._is_after_signing_page_context(block, page)
        after_previous_page_context = previous_signing_context and BUSINESS_SIGNING_FIELD_RE.search(text) is not None
        has_signing_signal = any(pattern.search(text) for pattern in [
            PARTY_RE,
            SEAL_RE,
            SIGN_RE,
            REPRESENTATIVE_RE,
            DATE_LABEL_RE,
            SIGNING_CONTEXT_RE,
            BUSINESS_SIGNING_FIELD_RE,
        ])
        if not has_signing_signal:
            return False
        if self._looks_like_contract_body_text(block, text):
            return False
        return (
            in_bottom
            or in_top
            or after_terminal_clause
            or after_signing_page_context
            or after_previous_page_context
            or bool(SIGNING_CONTEXT_RE.search(text))
        )
```

- [ ] **Step 5: Update scoring for paired aliases, business forms, and terminal context**

Change the `_score_cluster` signature to:

```python
    def _score_cluster(
        self,
        blocks: list[TextBlock],
        page: Page,
        page_role: str,
        *,
        previous_signing_context: bool = False,
    ) -> tuple[float, list[str]]:
```

In `_score_cluster`, replace:

```python
        if PARTY_RE.search(text) and "甲方" in text and "乙方" in text:
            score += 0.25
            reasons.append("paired_parties")
        elif PARTY_RE.search(text):
            score += 0.1
            reasons.append("party_label")
        signal_count = sum(bool(pattern.search(text)) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE])
```

with:

```python
        if self._has_paired_party_roles(text):
            score += 0.25
            reasons.append("paired_parties")
        elif PARTY_RE.search(text):
            score += 0.1
            reasons.append("party_label")
        signal_count = sum(bool(pattern.search(text)) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE])
        business_field_count = len(BUSINESS_SIGNING_FIELD_RE.findall(text))
        if business_field_count >= 3:
            score += 0.25
            reasons.append("business_signing_form_fields")
```

After the existing `_cluster_in_top` scoring block, insert:

```python
        if self._cluster_after_terminal_signing_clause(blocks, page):
            score += 0.15
            reasons.append("terminal_signing_clause_context")
        if business_field_count >= 3 and self._cluster_after_signing_page_context(blocks, page):
            score += 0.2
            reasons.append("page_signing_context_business_fields")
        if business_field_count >= 5 and previous_signing_context and self._cluster_has_business_signing_fields(blocks):
            score += 0.25
            reasons.append("previous_page_signing_context_business_fields")
```

- [ ] **Step 6: Add helper methods before `_to_page`**

Insert these methods before `_to_page`:

```python
    def _expand_signing_continuation(
        self,
        page: Page,
        cluster: list[TextBlock],
        consumed_block_ids: set[str],
        *,
        previous_signing_context: bool = False,
    ) -> list[TextBlock]:
        if not self._should_expand_signing_continuation(
            page,
            cluster,
            previous_signing_context=previous_signing_context,
        ):
            return cluster

        included = list(cluster)
        included_ids = {block.block_id for block in included}
        initial_bottom = max(self._effective_bbox(block).y1 for block in included)
        current_bottom = initial_bottom
        cluster_bbox = self._union_bbox([self._effective_bbox(block) for block in included])
        max_y0 = min(page.height * self.bottom_ratio, current_bottom + page.height * 0.3)
        ordered_blocks = sorted(
            page.blocks,
            key=lambda block: (block.page_no, self._effective_bbox(block).y0, self._effective_bbox(block).x0),
        )

        for block in ordered_blocks:
            if block.block_id in included_ids or block.block_id in consumed_block_ids:
                continue
            bbox = self._effective_bbox(block)
            if bbox.y1 <= initial_bottom + 2:
                continue
            if bbox.y0 > max_y0:
                continue
            if bbox.y0 - current_bottom > self.cluster_gap:
                break
            if not self._is_signing_continuation_block(block, page, cluster_bbox):
                continue
            included.append(block)
            included_ids.add(block.block_id)
            current_bottom = max(current_bottom, bbox.y1)

        return sorted(
            included,
            key=lambda block: (block.page_no, self._effective_bbox(block).y0, self._effective_bbox(block).x0),
        )

    def _attach_signing_context_blocks(
        self,
        page: Page,
        cluster: list[TextBlock],
        consumed_block_ids: set[str],
    ) -> list[TextBlock]:
        text = self._compact("\n".join(block.text for block in cluster))
        if BUSINESS_SIGNING_FIELD_RE.search(text) is None:
            return cluster
        if not self._cluster_after_signing_page_context(cluster, page):
            return cluster

        top_y0 = min(self._effective_bbox(block).y0 for block in cluster)
        context_blocks = [
            block
            for block in page.blocks
            if block.block_id not in consumed_block_ids
            and block not in cluster
            and SIGNING_CONTEXT_RE.search(self._compact(block.text))
            and self._effective_bbox(block).y1 <= top_y0
            and top_y0 - self._effective_bbox(block).y1 <= page.height * 0.28
            and not FOOTER_ONLY_RE.match(self._compact(block.text))
        ]
        if not context_blocks:
            return cluster
        return sorted(
            [*context_blocks, *cluster],
            key=lambda block: (block.page_no, self._effective_bbox(block).y0, self._effective_bbox(block).x0),
        )

    def _should_expand_signing_continuation(
        self,
        page: Page,
        cluster: list[TextBlock],
        *,
        previous_signing_context: bool = False,
    ) -> bool:
        text = self._compact("\n".join(block.text for block in cluster))
        has_top_signing_page = (
            self._cluster_in_top(cluster, page)
            and SIGNING_CONTEXT_RE.search(text) is not None
            and PARTY_RE.search(text) is not None
            and (SEAL_RE.search(text) is not None or SIGN_RE.search(text) is not None)
        )
        has_business_signing_page = (
            (
                SIGNING_CONTEXT_RE.search(text) is not None
                or self._cluster_after_signing_page_context(cluster, page)
                or previous_signing_context
            )
            and BUSINESS_SIGNING_FIELD_RE.search(text) is not None
        )
        has_terminal_context = self._cluster_after_terminal_signing_clause(cluster, page)
        return has_top_signing_page or has_business_signing_page or has_terminal_context

    def _expansion_reason(
        self,
        page: Page,
        cluster: list[TextBlock],
        *,
        previous_signing_context: bool = False,
    ) -> str:
        if self._cluster_after_terminal_signing_clause(cluster, page):
            return "terminal_signing_clause_context"
        text = self._compact("\n".join(block.text for block in cluster))
        if BUSINESS_SIGNING_FIELD_RE.search(text) and previous_signing_context:
            return "previous_page_signing_context_business_fields"
        if BUSINESS_SIGNING_FIELD_RE.search(text) and self._cluster_after_signing_page_context(cluster, page):
            return "page_signing_context_business_fields"
        return "top_signing_continuation_expanded"

    def _is_signing_continuation_block(self, block: TextBlock, page: Page, cluster_bbox: BBox) -> bool:
        text = self._compact(block.text)
        if not text or FOOTER_ONLY_RE.match(text):
            return False
        if self._looks_like_contract_body_text(block, text):
            return False
        bbox = self._effective_bbox(block)
        if bbox.y0 >= page.height * 0.9:
            return False
        if not self._horizontally_related(bbox, cluster_bbox, page):
            return False
        return (
            PARTY_RE.search(text) is not None
            or SEAL_RE.search(text) is not None
            or SIGN_RE.search(text) is not None
            or REPRESENTATIVE_RE.search(text) is not None
            or DATE_LABEL_RE.search(text) is not None
            or BUSINESS_SIGNING_FIELD_RE.search(text) is not None
            or CONTINUATION_LABEL_RE.search(text) is not None
            or (ADDRESS_BODY_RE.search(text) is not None and len(text) <= 80)
        )

    def _is_after_terminal_signing_clause(self, block: TextBlock, page: Page) -> bool:
        anchor_y = self._terminal_signing_anchor_y(page)
        if anchor_y is None:
            return False
        bbox = self._effective_bbox(block)
        return anchor_y <= bbox.y0 <= page.height * 0.78

    def _cluster_after_terminal_signing_clause(self, blocks: list[TextBlock], page: Page) -> bool:
        return any(self._is_after_terminal_signing_clause(block, page) for block in blocks)

    def _terminal_signing_anchor_y(self, page: Page) -> float | None:
        anchors = [
            self._effective_bbox(block).y1
            for block in page.blocks
            if TERMINAL_SIGNING_CLAUSE_RE.search(self._compact(block.text))
        ]
        return max(anchors) if anchors else None

    def _is_after_signing_page_context(self, block: TextBlock, page: Page) -> bool:
        bbox = self._effective_bbox(block)
        anchor_y = self._signing_context_anchor_y(page, before_y=bbox.y0)
        if anchor_y is None:
            return False
        return anchor_y <= bbox.y0 <= page.height * 0.78

    def _cluster_after_signing_page_context(self, blocks: list[TextBlock], page: Page) -> bool:
        return any(self._is_after_signing_page_context(block, page) for block in blocks)

    def _signing_context_anchor_y(self, page: Page, before_y: float | None = None) -> float | None:
        anchors = []
        for block in page.blocks:
            bbox = self._effective_bbox(block)
            if before_y is not None and bbox.y1 > before_y:
                continue
            if SIGNING_CONTEXT_RE.search(self._compact(block.text)):
                anchors.append(bbox.y1)
        return max(anchors) if anchors else None

    def _previous_signing_context_pages(self, document: Document) -> set[int]:
        pages = sorted(document.pages, key=lambda page: page.page_no)
        if not pages:
            return set()
        last_page_no = max(page.page_no for page in pages)
        context_pages: set[int] = set()
        for previous_page, current_page in zip(pages, pages[1:]):
            if current_page.page_no < last_page_no - 1:
                continue
            if not self._page_has_signoff_context(previous_page):
                continue
            if not self._page_looks_like_business_signing_continuation(current_page):
                continue
            context_pages.add(current_page.page_no)
        return context_pages

    def _page_has_signoff_context(self, page: Page) -> bool:
        text = self._compact("\n".join(block.text for block in page.blocks))
        return "以下无正文" in text or TERMINAL_SIGNING_CLAUSE_RE.search(text) is not None

    def _page_looks_like_business_signing_continuation(self, page: Page) -> bool:
        business_blocks = [
            block
            for block in page.blocks
            if self._is_business_signing_field_block(block, page)
        ]
        if self._business_field_count(business_blocks) < 5:
            return False
        if not self._looks_two_column(business_blocks, page):
            return False
        substantive_non_business = [
            block
            for block in page.blocks
            if not self._is_business_signing_field_block(block, page)
            and not self._is_ignorable_page_chrome(block, page)
            and self._is_substantive_non_signing_block(block, page)
        ]
        return not substantive_non_business

    def _cluster_has_business_signing_fields(self, blocks: list[TextBlock]) -> bool:
        return self._business_field_count(blocks) >= 5

    def _business_field_count(self, blocks: list[TextBlock]) -> int:
        return sum(len(BUSINESS_SIGNING_FIELD_RE.findall(self._compact(block.text))) for block in blocks)

    def _is_business_signing_field_block(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text or self._is_ignorable_page_chrome(block, page):
            return False
        bbox = self._effective_bbox(block)
        if bbox.y0 >= page.height * 0.9:
            return False
        return (
            BUSINESS_SIGNING_FIELD_RE.search(text) is not None
            or (ADDRESS_BODY_RE.search(text) is not None and len(text) <= 80)
        )

    def _is_ignorable_page_chrome(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text:
            return True
        block_type = (block.block_type or "").lower()
        if block_type in {"header", "footer", "number"}:
            return True
        bbox = self._effective_bbox(block)
        return FOOTER_ONLY_RE.match(text) is not None or bbox.y0 >= page.height * 0.9

    @staticmethod
    def _has_paired_party_roles(text: str) -> bool:
        return (
            ("甲方" in text and "乙方" in text)
            or ("供方" in text and "需方" in text)
            or ("买方" in text and "卖方" in text)
            or ("采购方" in text and "供货方" in text)
            or ("委托方" in text and "受托方" in text)
        )

    def _horizontally_related(self, bbox: BBox, cluster_bbox: BBox, page: Page) -> bool:
        margin = max(self.padding * 2, page.width * 0.05)
        return bbox.x1 >= cluster_bbox.x0 - margin and bbox.x0 <= cluster_bbox.x1 + margin

    @staticmethod
    def _union_bbox(bboxes: list[BBox]) -> BBox:
        return BBox(
            x0=min(bbox.x0 for bbox in bboxes),
            y0=min(bbox.y0 for bbox in bboxes),
            x1=max(bbox.x1 for bbox in bboxes),
            y1=max(bbox.y1 for bbox in bboxes),
        )
```

- [ ] **Step 7: Run detector tests and verify they pass**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 8: Run pipeline signing tests and verify they pass**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py -q
```

Expected:

```text
... passed
```

---

### Task 5: Verify Evidence Output and Regression Surface

**Files:**
- No code files.

- [ ] **Step 1: Run the focused combined test set**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py backend/tests/test_signing_region_pipeline.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 2: Run clause document regression tests**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_clause_document.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 3: Run backend syntax check**

Run:

```bash
cd backend && python -m compileall app tests
```

Expected:

```text
0 errors
```

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected:

```text
no output
```

- [ ] **Step 5: Inspect the original short-bbox failing task after rerun**

Rerun task `dc69d8f4-9b14-420a-9e8a-161c6c5646a5` only when it is acceptable to rewrite that local task artifact. Use the existing `job.json` payload:

```bash
env -u VIRTUAL_ENV PYTHONPATH=backend uv run python -c 'import json; from pathlib import Path; from app.models import CompareOptions; from app.application.compare_tasks import CompareTaskApplication; task_dir=Path("storage/tasks/dc69d8f4-9b14-420a-9e8a-161c6c5646a5"); payload=json.loads((task_dir/"job.json").read_text(encoding="utf-8"))["payload"]; CompareTaskApplication()._run_compare_task(original_path=Path(payload["original_path"]), compare_path=Path(payload["compare_path"]), task_id=payload["task_id"], original_filename=payload.get("original_filename"), compare_filename=payload.get("compare_filename"), compare_options=CompareOptions.model_validate(payload.get("compare_options") or {})); print("rerun_done")'
```

Then inspect:

```bash
python -m json.tool storage/tasks/dc69d8f4-9b14-420a-9e8a-161c6c5646a5/debug/signing_region.json
```

Expected:

```text
original_regions[0].bbox.y1 should be near the date row, around 365-380 instead of around 180.
original_regions[0].elements[0].raw_ref.source_block_ids should include address, representative, signature, postcode, and date block ids.
diffs[0].original_evidence[0].bbox should match the expanded original region bbox.
```

- [ ] **Step 6: Inspect the 14.2 false-exclusion task after rerun**

Rerun task `75db9356-c540-4a02-919e-44c0dd8f5f2b` only when it is acceptable to rewrite that local task artifact:

```bash
env -u VIRTUAL_ENV PYTHONPATH=backend uv run python -c 'import json; from pathlib import Path; from app.models import CompareOptions; from app.application.compare_tasks import CompareTaskApplication; task_dir=Path("storage/tasks/75db9356-c540-4a02-919e-44c0dd8f5f2b"); payload=json.loads((task_dir/"job.json").read_text(encoding="utf-8"))["payload"]; CompareTaskApplication()._run_compare_task(original_path=Path(payload["original_path"]), compare_path=Path(payload["compare_path"]), task_id=payload["task_id"], original_filename=payload.get("original_filename"), compare_filename=payload.get("compare_filename"), compare_options=CompareOptions.model_validate(payload.get("compare_options") or {})); print("rerun_done")'
```

Then inspect:

```bash
jq '.clause_exclusion.original[] | select(.block_id=="p10_ppocrv5_b23")' storage/tasks/75db9356-c540-4a02-919e-44c0dd8f5f2b/debug/signing_region.json
jq '.diffs[] | select(.clause_no=="14.2" or (.compare_snippet|contains("及付款方式"))) | {diff_id,source_type,clause_no,compare_snippet}' storage/tasks/75db9356-c540-4a02-919e-44c0dd8f5f2b/task.json
```

Expected:

```text
The first jq command prints no entry for p10_ppocrv5_b23.
The second jq command should not show a false ADD diff whose compare_snippet is only "及付款方式、交付时间、验收标准。".
```

- [ ] **Step 7: Inspect the supply/demand signing table task after rerun**

Rerun task `3154a3a4-109f-4927-adec-e95c63e6ea31` only when it is acceptable to rewrite that local task artifact:

```bash
env -u VIRTUAL_ENV PYTHONPATH=backend uv run python -c 'import json; from pathlib import Path; from app.models import CompareOptions; from app.application.compare_tasks import CompareTaskApplication; task_dir=Path("storage/tasks/3154a3a4-109f-4927-adec-e95c63e6ea31"); payload=json.loads((task_dir/"job.json").read_text(encoding="utf-8"))["payload"]; CompareTaskApplication()._run_compare_task(original_path=Path(payload["original_path"]), compare_path=Path(payload["compare_path"]), task_id=payload["task_id"], original_filename=payload.get("original_filename"), compare_filename=payload.get("compare_filename"), compare_options=CompareOptions.model_validate(payload.get("compare_options") or {})); print("rerun_done")'
```

Then inspect:

```bash
jq '{original_blocks: .signing_blocks.original, compare_blocks: .signing_blocks.compare, matches: .matches, diffs: .diffs}' storage/tasks/3154a3a4-109f-4927-adec-e95c63e6ea31/debug/signing_region.json
jq '.diffs[] | select(.source_type=="signing_region") | {diff_id,diff_type,title,original_evidence,compare_evidence}' storage/tasks/3154a3a4-109f-4927-adec-e95c63e6ea31/task.json
```

Expected:

```text
Both original_blocks and compare_blocks contain a page 2 signing block.
The compare page 2 signing block source_block_ids include the table block containing 供方/需方 business fields.
The signing region diff is not a one-sided DELETE caused by missing compare-side signing detection.
```

- [ ] **Step 8: Inspect the final-page mid-page signing task after rerun**

Rerun task `6219c7d7-8df7-4b74-b57c-44edccad2b5f` only when it is acceptable to rewrite that local task artifact:

```bash
env -u VIRTUAL_ENV PYTHONPATH=backend uv run python -c 'import json; from pathlib import Path; from app.models import CompareOptions; from app.application.compare_tasks import CompareTaskApplication; task_dir=Path("storage/tasks/6219c7d7-8df7-4b74-b57c-44edccad2b5f"); payload=json.loads((task_dir/"job.json").read_text(encoding="utf-8"))["payload"]; CompareTaskApplication()._run_compare_task(original_path=Path(payload["original_path"]), compare_path=Path(payload["compare_path"]), task_id=payload["task_id"], original_filename=payload.get("original_filename"), compare_filename=payload.get("compare_filename"), compare_options=CompareOptions.model_validate(payload.get("compare_options") or {})); print("rerun_done")'
```

Then inspect:

```bash
jq '{original_blocks: .signing_blocks.original, compare_blocks: .signing_blocks.compare, matches: .matches, diffs: .diffs}' storage/tasks/6219c7d7-8df7-4b74-b57c-44edccad2b5f/debug/signing_region.json
jq '.diffs[] | select(.source_type=="signing_region") | {diff_id,diff_type,title,original_evidence,compare_evidence}' storage/tasks/6219c7d7-8df7-4b74-b57c-44edccad2b5f/task.json
```

Expected:

```text
Both original_blocks and compare_blocks contain a page 7 signing block.
The original page 7 signing block source_block_ids include party_a, party_b, sign, and date OCR blocks.
The signing region diff is not a one-sided ADD caused by missing original-side signing detection.
```

- [ ] **Step 9: Inspect the business/contact signing-page task after rerun**

Rerun task `3627991f-2426-4a82-ade2-d02237954df2` only when it is acceptable to rewrite that local task artifact:

```bash
env -u VIRTUAL_ENV PYTHONPATH=backend uv run python -c 'import json; from pathlib import Path; from app.models import CompareOptions; from app.application.compare_tasks import CompareTaskApplication; task_dir=Path("storage/tasks/3627991f-2426-4a82-ade2-d02237954df2"); payload=json.loads((task_dir/"job.json").read_text(encoding="utf-8"))["payload"]; CompareTaskApplication()._run_compare_task(original_path=Path(payload["original_path"]), compare_path=Path(payload["compare_path"]), task_id=payload["task_id"], original_filename=payload.get("original_filename"), compare_filename=payload.get("compare_filename"), compare_options=CompareOptions.model_validate(payload.get("compare_options") or {})); print("rerun_done")'
```

Then inspect:

```bash
jq '{original_blocks: .signing_blocks.original, compare_blocks: .signing_blocks.compare, matches: .matches, diffs: .diffs}' storage/tasks/3627991f-2426-4a82-ade2-d02237954df2/debug/signing_region.json
jq '.diffs[] | select(.source_type=="signing_region") | {diff_id,diff_type,title,original_evidence,compare_evidence}' storage/tasks/3627991f-2426-4a82-ade2-d02237954df2/task.json
```

Expected:

```text
original_blocks contains page 12 and page 25 signing blocks; compare_blocks contains page 12 and page 24 signing blocks.
The compare page 12 signing block source_block_ids include the 签署页 title plus address/contact/phone/bank/account/credit-code OCR blocks.
The compare page 24 signing block source_block_ids include address/contact/phone/email/credit-code OCR blocks, but not the header or page number.
The signing region diff is not a one-sided DELETE caused by missing compare-side signing detection.
```

---

## Risk Controls

- Do not switch to full-page evidence bbox by default. That would highlight page numbers, blank regions, and possible body text.
- Only expand when the starting cluster is a top signing-page cluster with explicit `签字页`/`签署页`/`以下无正文` context, party labels, and seal/signature signal.
- Treat `供方`/`需方` and business form fields as signing signals only when they are in a signing-page context, below a same-page `签署页`/`签字页` title, bottom area, or after a terminal signing clause; those words appear in ordinary contract clauses too.
- When OCR exposes only business/contact/account fields below `签署页`, attach the signing title block to the evidence bbox, but do not expand to the full page.
- When OCR exposes only lower business/contact fields on a page without a signing title, require previous-page `以下无正文`/terminal signing context, final or near-final page position, dense business fields, and two-column layout before treating it as signing continuation.
- Treat mid-page signing areas as valid only after a terminal signing clause, not just because a block contains `甲方` or `乙方`.
- Keep `_looks_like_contract_body_text` in the expansion gate so clauses like `本合同经双方签字盖章后生效` are not swallowed.
- Keep footer/page-number regex exclusion so page numbers are not part of signing evidence.
- Keep bbox-overlap fallback in `SigningClauseDocumentBuilder` conservative. Direct `source_block_ids` are trusted; bbox-only matches must look like signing text.
- Keep `SigningRegionExtractor` unchanged unless tests show a block bbox/region bbox mismatch. The extractor currently uses `SigningBlock.bbox`, which is the desired single source of truth.

---

## Self-Review

- Spec coverage: The plan addresses six observed root causes: `SB-9-1` did not include lower signing continuation rows; `SigningClauseDocumentBuilder` excluded adjacent `14.2` text because a padded signing bbox overlapped it; supply/demand signing tables were missed when OCR exposed only business form fields; final-page mid-page signing areas were missed when they appeared after a terminal signing clause; business/contact/account signing pages were missed when OCR kept only the `签署页` title and business fields; lower-only business/contact signing continuation pages were missed when scan/OCR dropped the title and upper signature rows.
- Placeholder scan: There are no placeholder markers or unspecified implementation steps.
- Type consistency: Detector helpers use existing `BBox`, `Page`, and `TextBlock` imports already present in `block_detector.py`; clause-document helpers use the existing `TextBlock` import plus local regex constants.
- Test coverage: Unit tests cover expansion, body exclusion, supply/demand business signing tables, signature-title business/contact pages, previous-page signoff business-only pages, terminal-clause mid-page signing areas, semantic bbox fallback, and padded-overlap false exclusion. Pipeline tests cover the user-visible evidence bbox and clause-document exclusion behavior.
