"""Contract signing areas must survive OCR gaps without absorbing approval forms."""
import pytest

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder


def block(key, text, y, *, x=120, height=16):
    return TextBlock(block_id=key, page_no=1, text=text, block_type="text",
                     bbox=BBox(x0=x, y0=y, x1=x+250, y1=y+height))


def doc(blocks):
    return Document(filename="test.pdf", path="test.pdf", page_count=1,
                    pages=[Page(page_no=1, width=600, height=820, blocks=blocks)])


@pytest.mark.parametrize("title", ["项目开工申请单","交工验收单"])
def test_approval_form_stays_in_content_comparison(title):
    source = doc([block("title",title,100),block("form","建设单位意见：\n签字：\n盖章：\n日期：",500,height=180)])
    result = SigningBlockDetector().detect(source)
    assert not result.blocks and not result.pages and not result.low_confidence_candidates
    assert result.excluded_candidates[0]["reason"] == "operational_approval_form_not_contract_signing"
    assert SigningClauseDocumentBuilder().build(source,result.blocks).document == source


def test_contract_signing_area_on_appendix_form_page_is_retained():
    source = doc([block("title","交工验收单",100),block("form","建设单位意见：",300),
                  block("contract","甲方（盖章）：公司\n乙方（盖章）：公司\n法定代表人（签字）：\n签订日期：",520,height=180)])
    assert SigningBlockDetector().detect(source).blocks
