from __future__ import annotations

from app.config_models import DocumentUnderstandingSettings
from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.document_understanding import DocumentUnderstandingService


def _block(
    block_id: str,
    text: str,
    y0: float,
    y1: float,
    *,
    block_type: str = "text",
    flow_role: str = "",
    confidence: float | None = 0.95,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=1,
        text=text,
        bbox=BBox(x0=50, y0=y0, x1=500, y1=y1),
        block_type=block_type,
        flow_role=flow_role,
        confidence=confidence,
    )


def _document(blocks: list[TextBlock]) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=blocks)],
    )


def test_rule_understanding_excludes_toc_from_clause_split() -> None:
    document = _document([
        _block("toc-title", "目录", 80, 110, block_type="paragraph_title", flow_role="heading"),
        _block("toc-1", "一、服务内容与要求......3", 130, 160),
        _block("toc-2", "二、合同金额......5", 170, 200),
        _block("toc-3", "三、付款方式......8", 210, 240),
        _block("toc-4", "四、违约责任......10", 250, 280),
        _block("toc-5", "五、争议解决......12", 290, 320),
    ])

    result = DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(document, "compare")
    clauses = ClauseSplitter().split(document, "N")

    assert result.semantic_decisions
    assert document.pages[0].semantic_role == "toc"
    assert all(block.enter_clause_compare is False for block in document.pages[0].blocks)
    assert clauses == []


def test_rule_understanding_routes_table_blocks_out_of_clause_compare() -> None:
    document = _document([
        _block("title", "1. 服务内容", 80, 110),
        _block("table", "序号 服务内容 数量 单价 金额", 130, 360, block_type="table", flow_role="table"),
    ])

    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert document.pages[0].blocks[1].semantic_role == "table_body"
    assert document.pages[0].blocks[1].enter_clause_compare is False
    assert len(clauses) == 1
    assert clauses[0].source_block_ids == ["title"]


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self.content}}]}


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self.content)


def test_llm_understanding_applies_valid_roles_and_rejects_unknown_block_ids() -> None:
    document = _document([
        _block("b1", "附件一 报价清单", 80, 110, confidence=0.4),
        _block("b2", "联系人：张三 电话：123", 130, 160, confidence=0.4),
    ])
    client = _FakeClient(
        """
        {
          "page_role": "quote",
          "confidence": 0.91,
          "reason": "报价清单页面",
          "block_roles": [
            {
              "block_id": "b1",
              "role": "quote",
              "confidence": 0.9,
              "enter_clause_compare": false,
              "reason": "报价标题"
            },
            {
              "block_id": "missing",
              "role": "noise",
              "confidence": 0.99,
              "enter_clause_compare": false,
              "reason": "不存在"
            }
          ]
        }
        """
    )
    settings = DocumentUnderstandingSettings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:8002/v1",
        llm_model="test-model",
        llm_confidence_accept=0.82,
    )

    result = DocumentUnderstandingService(settings, client=client).understand(document, "compare")

    assert client.calls
    assert document.pages[0].semantic_role == "quote"
    assert document.pages[0].blocks[0].semantic_role == "quote"
    assert document.pages[0].blocks[0].enter_clause_compare is False
    assert any(decision["action"] == "reject_block_role" for decision in result.validation_decisions)
