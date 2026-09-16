"""Representation changes must neither erase nor duplicate printed field changes."""
import pytest

from app.models import DiffItem
from app.services.signing_table_equivalence import printed_fields, reconcile_printed_form
from tests.test_document_identity_regressions import signing_page


def missing_form(page, kind="DELETE"):
    return DiffItem(diff_id="D100", diff_type=kind, source_type="table",
                    **{"original_text" if kind == "DELETE" else "compare_text":
                       "\n".join(b.text for b in page.blocks)})


def test_equal_form_has_no_diff_across_layout_representations():
    before, after = signing_page(table=True), signing_page()
    assert reconcile_printed_form(missing_form(before), before, after) == []


@pytest.mark.parametrize("kind", ["ADD", "DELETE"])
def test_account_change_becomes_one_located_modification_in_either_layout_direction(kind):
    before = signing_page(table=kind == "DELETE")
    after = signing_page(accounts=("111102", "222202"), table=kind == "ADD")
    result = reconcile_printed_form(missing_form(before if kind == "DELETE" else after, kind), before, after)
    assert len(result) == 1
    diff = result[0]
    assert (diff.diff_type, diff.original_text, diff.compare_text) == ("MODIFY", "111101", "111102")
    assert diff.match_score_details["party_role"] == "甲方"
    assert diff.match_score_details["field_key"] == "account"
    assert diff.original_evidence[0].bbox.y0 == 320
    assert diff.compare_evidence[0].bbox.x1 < 330
    assert diff.quality_status == "NEEDS_REVIEW"


def test_accounts_swapped_between_parties_remain_two_changes():
    before = signing_page(table=True)
    after = signing_page(accounts=("222202", "111101"))
    result = reconcile_printed_form(missing_form(before), before, after)
    assert {(d.match_score_details["party_role"], d.original_text, d.compare_text) for d in result} == {
        ("甲方", "111101", "222202"), ("乙方", "222202", "111101")}


def test_column_order_does_not_change_party_identity():
    before, after = signing_page(table=True), signing_page()
    for b in after.blocks:
        offset = 260 if b.bbox.x0 < 300 else -260
        b.bbox.x0 += offset
        b.bbox.x1 += offset
        for c in b.char_boxes:
            c.bbox.x0 += offset
            c.bbox.x1 += offset
    assert printed_fields(before) == printed_fields(after)
    assert reconcile_printed_form(missing_form(before), before, after) == []


@pytest.mark.parametrize("missing", ["账号", "纳税人登记号", "承包人"])
def test_missing_evidence_is_not_treated_as_an_unchanged_or_deleted_field(missing):
    before, after = signing_page(table=True), signing_page()
    after.blocks = [b for b in after.blocks if not b.text.startswith(missing)]
    assert reconcile_printed_form(missing_form(before), before, after) is None


def test_unrelated_table_on_same_signing_page_is_not_consumed():
    before, after = signing_page(table=True), signing_page()
    diff = DiffItem(diff_id="other", diff_type="DELETE", source_type="table", original_text="服务清单：费用 35000")
    assert reconcile_printed_form(diff, before, after) is None


def test_duplicate_party_label_is_ambiguous_even_with_complete_fields():
    before, after = signing_page(table=True), signing_page()
    block = after.blocks[7]
    block.text = block.text.replace("承包人", "委托人")
    for c in block.char_boxes:
        c.char = block.text[c.text_index]
    assert reconcile_printed_form(missing_form(before), before, after) is None
