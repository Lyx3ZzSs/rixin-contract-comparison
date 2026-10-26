"""Image rereading is the authority; peer text only selects a disputed crop."""
from types import SimpleNamespace

import fitz
import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.single_character_ocr import reconcile_single_characters, SOURCE


TEXT = '支付给乙方任何费用。如遇税率政策调整，不含税金额不变，合增值税'
INDEX = TEXT.index('合')


def document(tmp_path, name, text, source):
    path = tmp_path / f'{name}.pdf'
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=800)
        pdf.save(path)
    block = TextBlock(block_id=name, page_no=1, text=text, source=source,
        bbox=BBox(x0=50,y0=100,x1=50+10*len(text),y1=115),
        char_boxes=[CharBox(char=c,page_no=1,text_index=i,
            bbox=BBox(x0=50+10*i,y0=100,x1=60+10*i,y1=115)) for i,c in enumerate(text)])
    return Document(path=str(path),filename=path.name,page_count=1,
        pages=[Page(page_no=1,width=600,height=800,blocks=[block])])


class Reader:
    def __init__(self, tokens=('含','含'), confidence=.97, corrupt_context=False):
        self.tokens = iter(tokens)
        self.confidence = confidence
        self.corrupt_context = corrupt_context
        self.calls = 0

    def predict(self, path, file_type):
        self.calls += 1
        return next(self.tokens)

    def payload_to_document(self, token, path):
        radius = (3,5)[(self.calls-1)%2]
        text = TEXT[max(0,INDEX-radius):INDEX] + token + TEXT[INDEX+1:INDEX+1+radius]
        if self.corrupt_context:
            text = '错' + text[1:]
        width = len(text)*10
        block = TextBlock(block_id='roi',page_no=1,text=text,confidence=self.confidence,
            bbox=BBox(x0=0,y0=0,x1=width,y1=15),
            char_boxes=[CharBox(char=c,page_no=1,text_index=i,
                bbox=BBox(x0=i*10,y0=0,x1=(i+1)*10,y1=15),confidence=self.confidence)
                for i,c in enumerate(text)])
        return SimpleNamespace(pages=[SimpleNamespace(width=width,height=15,blocks=[block])])


def pair(tmp_path):
    return (document(tmp_path,'o',TEXT.replace('合','含'),'native'),
            document(tmp_path,'c',TEXT,'PP-OCRv6'))


def test_two_context_readings_repair_ocr_and_preserve_raw_evidence(tmp_path):
    original,compare = pair(tmp_path)
    reader = Reader()
    changes = reconcile_single_characters(original,compare,extractor=reader)
    assert [(side,b.block_id) for side,b in changes] == [('compare','c')]
    block = compare.pages[0].blocks[0]
    assert block.text == TEXT.replace('合','含')
    assert any(e.raw_text == TEXT and e.source == 'single_character_before' for e in block.source_evidence)
    assert len([e for e in block.source_evidence if e.source == SOURCE]) == 2
    assert all(c.char == block.text[c.text_index] for c in block.char_boxes)
    assert reader.calls == 2
    assert reconcile_single_characters(original,compare,extractor=reader) == []
    assert reader.calls == 2


@pytest.mark.parametrize('tokens', [('合','合'),('含','合'),('合','含')])
def test_real_change_or_conflicting_readings_keep_text_and_geometry(tmp_path,tokens):
    original,compare = pair(tmp_path)
    before = compare.model_dump()
    assert reconcile_single_characters(original,compare,extractor=Reader(tokens)) == []
    assert compare.model_dump() == before


@pytest.mark.parametrize('kwargs', [{'confidence':.9},{'corrupt_context':True},{'tokens':()}])
def test_insufficient_or_unavailable_evidence_cannot_copy_peer(tmp_path,kwargs):
    original,compare = pair(tmp_path)
    before = compare.model_dump()
    assert reconcile_single_characters(original,compare,extractor=Reader(**kwargs)) == []
    assert compare.model_dump() == before


def test_rereading_never_uses_peer_as_replacement(tmp_path):
    original,compare = pair(tmp_path)
    assert reconcile_single_characters(original,compare,extractor=Reader(('哈','哈')))
    assert compare.pages[0].blocks[0].text == TEXT.replace('合','哈')
    assert compare.pages[0].blocks[0].text != original.pages[0].blocks[0].text


@pytest.mark.parametrize('old,new',[('合','不'),('不','含'),('1','2'),('一','二'),('6%','9%'),('合','未')])
def test_numbers_rates_and_negations_are_not_automatically_rewritten(tmp_path,old,new):
    original = document(tmp_path,'o',TEXT.replace('合',new),'native')
    compare = document(tmp_path,'c',TEXT.replace('合',old),'PP-OCRv6')
    reader = Reader(())
    assert reconcile_single_characters(original,compare,extractor=reader) == []
    assert reader.calls == 0


def test_ambiguous_line_or_missing_character_coordinates_cannot_trigger_repair(tmp_path):
    for ambiguous in (True,False):
        original,compare = pair(tmp_path)
        if ambiguous:
            original.pages[0].blocks.append(original.pages[0].blocks[0].model_copy(deep=True))
        else:
            compare.pages[0].blocks[0].char_boxes = []
        reader = Reader(())
        assert reconcile_single_characters(original,compare,extractor=reader) == []
        assert reader.calls == 0


def test_original_ocr_side_uses_its_own_pixels_too(tmp_path):
    native,ocr = pair(tmp_path)
    changes = reconcile_single_characters(ocr,native,extractor=Reader())
    assert [(side,b.block_id) for side,b in changes] == [('original','c')]
    assert ocr.pages[0].blocks[0].text == TEXT.replace('合','含')


def test_a_correct_reading_at_the_wrong_position_cannot_authorize_repair(tmp_path):
    class MislocatedReader(Reader):
        def payload_to_document(self,token,path):
            result = super().payload_to_document(token,path)
            for char in result.pages[0].blocks[0].char_boxes:
                char.bbox = char.bbox.model_copy(update={'x0':0,'x1':1})
            return result
    original,compare = pair(tmp_path)
    before = compare.model_dump()
    assert reconcile_single_characters(original,compare,extractor=MislocatedReader()) == []
    assert compare.model_dump() == before
