"""Regression contracts for confirmed errors, with real-change controls."""
import json
from pathlib import Path

import pytest

from app.models import DiffItem, Document, BBox, EvidenceBox
from app.services.diff_quality import DiffQualityProcessor
from app.services.source_consensus import confirmed_paragraph_equivalence
from app.services.signing_region.value_ocr import SigningFieldValueOCR
from app.services.signing_region.models import SigningRegion

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "output/analysis/three-task-root-cause-20260908"
FIX = ROOT / "output/analysis/three-task-fix-20260908"


def saved(prefix, diff_id):
    path = AUDIT / f"{prefix}-saved.json"
    if not path.exists():
        pytest.skip("local reviewed corpus unavailable")
    return DiffItem.model_validate(next(d for d in json.loads(path.read_text())["diffs"] if d["diff_id"] == diff_id))


def test_multiline_payment_does_not_invent_a_missing_financial_digit():
    diff = DiffItem(diff_id="payment", diff_type="MODIFY", source_type="clause",
                    original_text="首次付款壹\n万伍仟元整；第二次付款壹万伍仟元整。",
                    compare_text="首次付款壹万伍仟元整；第二次付款壹万伍仟元整。")
    assert not DiffQualityProcessor._normalize_financial_uppercase_amount_change(diff)


def test_second_payment_change_has_its_own_character_ranges_and_no_old_boxes():
    original = "第一次付款壹万伍仟元整；第二次付款壹\n万伍仟元整。"
    compare = "第一次付款壹万伍仟元整；第二次付款贰万伍仟元整。"
    diff = DiffItem(diff_id="payment", diff_type="MODIFY", source_type="clause", original_text=original, compare_text=compare,
                    original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=0,y0=0,x1=500,y1=500))])
    assert DiffQualityProcessor._normalize_financial_uppercase_amount_change(diff)
    assert diff.title == "付款金额（大写）"
    assert (diff.original_snippet, diff.compare_snippet) == ("壹", "贰")
    assert original[diff.original_change_ranges[0].start:diff.original_change_ranges[0].end] == "壹"
    assert diff.original_change_ranges[0].start > original.index("第二次")
    assert diff.original_evidence == []


def test_amount_rewrite_cannot_hide_other_changed_terms():
    diff = DiffItem(diff_id="d", diff_type="MODIFY", source_type="clause",
                    original_text="总价壹万元整，期限一年", compare_text="总价贰万元整，期限两年")
    assert not DiffQualityProcessor._normalize_financial_uppercase_amount_change(diff)
    result = DiffQualityProcessor().process([diff]).diffs[0]
    assert result.quality_status == "NEEDS_REVIEW"
    assert result.original_text.endswith("期限一年") and result.compare_text.endswith("期限两年")


@pytest.mark.parametrize("prefix,diff_id", [("3c511b79", "D013"), ("1d80ab9f", "D024")])
def test_independent_paragraph_sources_remove_confirmed_noise_but_not_real_edits(prefix, diff_id):
    diff = saved(prefix, diff_id)
    documents = []
    for side in ("original", "compare"):
        path = FIX / f"{prefix}-{side}-document.json"
        if not path.exists():
            pytest.skip("local source documents unavailable")
        documents.append(Document.model_validate_json(path.read_text()))
    assert confirmed_paragraph_equivalence(diff, *documents)
    assert not confirmed_paragraph_equivalence(diff, documents[0], None)
    if prefix == "3c511b79":
        diff.compare_text = diff.compare_text.replace("15000", "25000")
        assert not confirmed_paragraph_equivalence(diff, *documents)
    else:
        for block in documents[1].pages[12].blocks:
            for evidence in block.source_evidence:
                if evidence.source == "ppstructure":
                    evidence.raw_text = evidence.raw_text.replace("可单独另附", "必须另附")
        assert not confirmed_paragraph_equivalence(diff, *documents)


def test_cached_local_reading_recovers_phone_without_copying_peer_value():
    tasks = list((ROOT / "storage/tasks").glob("7a41cb60*"))
    if not tasks:
        pytest.skip("local source documents unavailable")
    task = tasks[0]
    payload = json.loads((task / "diagnostics/debug/signing_region.json").read_text())
    original = next(SigningRegion.model_validate(r) for r in payload["original_regions"] if r["page_no"] == 55)
    compare = next(SigningRegion.model_validate(r) for r in payload["compare_regions"] if r["page_no"] == 54)
    # A different peer value must NOT influence the recovered original phone.
    for element in compare.elements:
        if element.raw_ref.get("field_key") == "phone":
            element.text = "19999999999"
    ocr = SigningFieldValueOCR()
    def cached(path, **kwargs):
        import hashlib
        key = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        cache = FIX / "field-ocr-cache" / f"{key}.json"
        if not cache.exists():
            pytest.skip("cached field reading unavailable")
        return json.loads(cache.read_text())["response"]
    ocr.extractor.predict = cached
    diagnostics = ocr.recover_missing_fields(next((task / "input/original").glob("*.pdf")), original, compare)
    phone = next(e for e in original.elements if e.raw_ref.get("field_key") == "phone" and e.raw_ref.get("party_role") == "买方")
    assert phone.text == "15756250946"
    assert len(phone.raw_ref["value_evidence"]) == 2
    assert diagnostics[0]["status"] == "RECOVERED"


def test_pixel_slash_repair_preserves_printed_digits(tmp_path):
    import fitz
    from app.models import CharBox, TextBlock, Page
    from app.services.table_slash_recovery import repair_table_slashes
    path = tmp_path / "glyphs.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((100, 100), "1", fontname="tiro", fontsize=12)
    page.insert_text((150, 100), "7", fontname="tiro", fontsize=12)
    page.draw_line((202, 98), (206, 88), width=0.5)
    words = page.get_text("words")
    boxes = [BBox(x0=w[0], y0=w[1], x1=w[2], y1=w[3]) for w in words]
    boxes.append(BBox(x0=200,y0=86,x1=208,y1=100))
    pdf.save(path)
    pdf.close()
    block = TextBlock(block_id="table", page_no=1, text="1\n7\n1", block_type="table", bbox=BBox(x0=80,y0=70,x1=230,y1=110),
                      raw_html="<table><tr><td>1</td><td>7</td><td>1</td></tr></table>",
                      char_boxes=[CharBox(char=c, page_no=1, text_index=i*2, bbox=b) for i,(c,b) in enumerate(zip("171",boxes))])
    document = Document(path=str(path),filename=path.name,page_count=1,pages=[Page(page_no=1,width=595,height=842,blocks=[block])])
    repaired = repair_table_slashes(document)
    assert len(repaired) == 1
    assert block.text == "1\n7\n/"
    assert [c.char for c in block.char_boxes] == ["1", "7", "/"]


def test_evidence_relocation_cannot_borrow_repeated_text_from_another_page(tmp_path):
    import fitz
    from app.services.text_coordinate_locator import TextCoordinateLocator
    path = tmp_path / "repeated.pdf"
    pdf = fitz.open()
    pdf.new_page().insert_text((100,100), "Battery 24AH")
    pdf.new_page()
    pdf.save(path)
    pdf.close()
    evidence = EvidenceBox(page_no=2,bbox=BBox(x0=90,y0=90,x1=200,y1=110),method="block_fallback",text="Battery 24AH")
    with fitz.open(path) as pdf:
        assert TextCoordinateLocator()._locate_snippet(pdf,"Battery 24AH",[evidence],"MODIFY") == []
