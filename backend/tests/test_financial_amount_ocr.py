from decimal import Decimal
import pytest
from app.services import financial_amount_ocr as repair
from app.services.single_character_ocr import repairable
from test_single_character_ocr import document, Reader

TEXT = '不含税价为（大写）陆万捌任捌佰陆拾柒元玖角贰分（￥68867.92元）。'


def pair(tmp_path, text=TEXT):
    return (document(tmp_path, 'o', TEXT.replace('任', '仟'), 'native'),
            document(tmp_path, 'c', text, 'PP-OCRv6'))


class FinancialReader(Reader):
    def __init__(self, tokens=('仟', '仟'), confidence=.99, corrupt_context=False):
        super().__init__(tokens, confidence, corrupt_context)

    def payload_to_document(self, token, path):
        from types import SimpleNamespace
        from app.models import BBox, CharBox, TextBlock
        value = '陆万捌' + token + '捌佰陆拾柒元玖角贰分'
        text = value if self.calls % 2 else '（大写）' + value
        if self.corrupt_context:
            text = text.replace('玖角', '捌角')
        width = len(text) * 10
        block = TextBlock(block_id='roi', page_no=1, text=text, confidence=self.confidence,
            bbox=BBox(x0=0, y0=0, x1=width, y1=15),
            char_boxes=[CharBox(char=c, page_no=1, text_index=i,
                bbox=BBox(x0=i*10, y0=0, x1=(i+1)*10, y1=15), confidence=self.confidence)
                for i, c in enumerate(text)])
        return SimpleNamespace(pages=[SimpleNamespace(width=width, height=15, blocks=[block])])


def test_verified_financial_glyph_preserves_raw_ocr_and_trace(tmp_path):
    a, b = pair(tmp_path)
    changes = repair.reconcile_financial_amounts(a, b, FinancialReader(('仟', '仟')))
    assert len(changes) == 1
    block = b.pages[0].blocks[0]
    assert block.text == TEXT.replace('任', '仟')
    assert any(e.source == 'financial_amount_before' and e.raw_text == TEXT for e in block.source_evidence)
    assert len([e for e in block.source_evidence if e.source == repair.SOURCE]) == 2
    assert all(c.char == block.text[c.text_index] for c in block.char_boxes)
    assert not repairable('仟')  # Generic prose repair remains protected.


@pytest.mark.parametrize('tokens,confidence', [(('任', '任'), .97), (('仟', '任'), .97), (('仟', '仟'), .8)])
def test_printed_typo_conflict_or_low_confidence_is_not_corrected(tmp_path, tokens, confidence):
    a, b = pair(tmp_path)
    before = b.model_dump()
    assert not repair.reconcile_financial_amounts(a, b, FinancialReader(tokens, confidence=confidence))
    assert b.model_dump() == before


@pytest.mark.parametrize('text', [TEXT.replace('68867.92', '69867.92'), TEXT.replace('（￥68867.92元）', ''),
                                  '法定代表人：任某；项目负责人任职。', TEXT.replace('任', '仟')])
def test_only_own_uppercase_amount_with_matching_numeric_value_is_candidate(tmp_path, text):
    a, b = pair(tmp_path, text)
    reader = FinancialReader(())
    assert not repair.reconcile_financial_amounts(a, b, reader)
    assert reader.calls == 0


def test_amount_parser_includes_fraction_and_never_equates_different_digits():
    assert repair.amount_value('陆万捌仟捌佰陆拾柒元玖角贰分') == Decimal('68867.92')
    assert repair.amount_value('柒万叁仟元整') == Decimal('73000')
    assert repair.amount_value('柒万捌仟元整') == Decimal('78000')
    assert repair.amount_value('肆仟壹佰叁拾贰元零捌分') == Decimal('4132.08')


@pytest.mark.parametrize('mode', ['other_digit', 'wrong_location', 'missing_geometry', 'unavailable'])
def test_no_repair_without_consistent_full_value_and_location(tmp_path, mode):
    a, b = pair(tmp_path)
    if mode == 'missing_geometry':
        b.pages[0].blocks[0].char_boxes = []
    class GuardedReader(FinancialReader):
        def payload_to_document(self, token, path):
            result = super().payload_to_document(token, path)
            if mode == 'wrong_location':
                for char in result.pages[0].blocks[0].char_boxes:
                    char.bbox.x0 = 0
                    char.bbox.x1 = 1
            return result
    reader = GuardedReader(() if mode == 'unavailable' else ('仟', '仟'), corrupt_context=mode == 'other_digit')
    before = b.model_dump()
    assert not repair.reconcile_financial_amounts(a, b, reader)
    assert b.model_dump() == before


def test_recorded_real_roi_results_remove_only_ocr_edits_and_retain_true_amount_change(tmp_path):
    import json
    from pathlib import Path
    import fitz
    from app.models import Document, Clause, DiffItem
    from app.services.diff_engine import DiffEngine
    from app.services.diff_quality import DiffQualityProcessor
    from app.services.evidence_locator import EvidenceLocator
    from app.services.clause_rewrite_highlights import prepare_clause_highlights

    root = Path(__file__).parent / 'fixtures'
    data = json.loads((root / 'financial_amount_roi.json').read_text())
    compare = Document.model_validate(data['document'])
    path = tmp_path / 'replay.pdf'
    with fitz.open() as pdf:
        for _ in range(3):
            page = pdf.new_page(width=842, height=595)
            page.set_rotation(270)
        pdf.save(path)
    compare.path = str(path)
    class RecordedReader:
        def __init__(self): self.readings = iter(data['readings'])
        def predict(self, path, file_type): return next(self.readings)
        def payload_to_document(self, payload, path): return Document.model_validate(payload)
    original = Document(filename='original', path='', page_count=0, pages=[])
    repairs = repair.reconcile_financial_amounts(original, compare, RecordedReader())
    assert len(repairs) == 2
    fixture = json.loads((root / 'financial_amount_highlights.json').read_text())
    diff = DiffItem.model_validate(fixture['diff'])
    clauses = {s: Clause.model_validate(c) for s, c in fixture['clauses'].items()}
    c = clauses['compare']
    for _, block in repairs:
        before = next(e.raw_text for e in block.source_evidence if e.source == 'financial_amount_before')
        old = next(m.group() for m in repair._TOKEN.finditer(before) if '任' in m.group())
        new = old.replace('任', '仟')
        assert new in block.text and c.text.count(old) == 1
        start = c.text.index(old)
        c.text = c.text[:start] + new + c.text[start + len(old):]
        for i in range(start, start + len(old)):
            c.char_boxes[i].char = c.text[i]
    diff.compare_text = c.text
    sa, sb, ra, rb = DiffEngine()._changed_snippets(diff.original_text, diff.compare_text)
    diff.original_snippet, diff.compare_snippet = sa, sb
    diff.original_change_ranges, diff.compare_change_ranges = ra, rb
    assert DiffQualityProcessor._normalize_financial_uppercase_amount_change(diff)
    assert (diff.original_snippet, diff.compare_snippet) == ('捌', '叁')
    EvidenceLocator().locate([diff], [clauses['original']], [c])
    prepare_clause_highlights([diff], [clauses['original']], [c])
    assert diff.clause_display_evidence is None
    assert [b.text for b in diff.field_display_evidence.original] == ['柒万捌仟元整']
    assert [b.text for b in diff.field_display_evidence.compare] == ['柒万叁仟元整']
    assert diff.original_text.count('陆万捌仟捌佰陆拾柒元玖角贰分') == 1
    assert diff.compare_text.count('陆万捌仟捌佰陆拾柒元玖角贰分') == 1
