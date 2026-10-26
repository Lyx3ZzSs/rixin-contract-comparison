from __future__ import annotations

from io import BytesIO
from pathlib import Path
import threading

import fitz
from PIL import Image, ImageDraw
import pytest

from app.config import Settings
from app.services.extractors.contract_v3.extractor import ContractExtractionV3
from app.services.extractors.contract_v3.layout_client import PPDocLayoutV3Client
from app.services.extractors.contract_v3.ocr import SelectiveOCRRouter
from app.services.extractors.contract_v3.table_transform import inverse_affine, upright_table_crop


def _pdf(path: Path, *, native: bool = False, raster: bool = False, table: bool = False) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    if native:
        page.insert_text((50, 55), "Native title")
    if raster:
        image = Image.new("RGB", (200, 80), "white")
        data = BytesIO()
        image.save(data, format="PNG")
        box = fitz.Rect(45, 80, 245, 160) if native else fitz.Rect(0, 0, 400, 300)
        page.insert_image(box, stream=data.getvalue())
    if table:
        for x in (40, 150, 260):
            page.draw_line((x, 90), (x, 170))
        for y in (90, 130, 170):
            page.draw_line((40, y), (260, y))
        for x, y, value in ((60, 112, "A"), (170, 112, "B"), (60, 152, "C"), (170, 152, "D")):
            page.insert_text((x, y), value)
    doc.save(path)
    doc.close()
    return path


class Layout:
    def __init__(self, boxes):
        self.boxes = boxes
        self.calls = 0

    def predict_page(self, png):
        self.calls += 1
        return self.boxes


class OCR(SelectiveOCRRouter):
    def __init__(self):
        super().__init__(Settings(_env_file=None))
        self.calls = 0

    def _request(self, png):
        self.calls += 1
        return {
            "errorCode": 0,
            "result": {
                "ocrResults": [
                    {
                        "prunedResult": {
                            "rec_texts": ["Missing text"],
                            "rec_polys": [[[15, 15], [125, 15], [125, 40], [15, 40]]],
                            "rec_scores": [0.99],
                            "text_word": [["Missing", "text"]],
                            "text_word_region": [
                                [[[15, 15], [75, 15], [75, 40], [15, 40]], [[80, 15], [125, 15], [125, 40], [80, 40]]]
                            ],
                        }
                    }
                ]
            },
        }


def _box(label: str, rect: list[int], cls: int = 22, order: int = 1):
    return {"cls_id": cls, "label": label, "score": 0.9, "coordinate": rect, "order": order}


def test_native_keeps_every_glyph_and_makes_no_ocr_request(tmp_path):
    pdf = _pdf(tmp_path / "native.pdf", native=True)
    layout, ocr = Layout([_box("doc_title", [90, 70, 280, 130], 6)]), OCR()
    result = ContractExtractionV3(layout=layout, ocr=ocr).extract(pdf)
    assert result.extractor_used == "contract_extraction_v3"
    assert all("ppstructure" not in block.source.casefold() for page in result.document.pages for block in page.blocks)
    assert result.performance["routes"] == {"NATIVE_GOOD": 1}
    assert result.performance["ocr_calls"] == ocr.calls == 0
    assert layout.calls == 1
    block = result.document.pages[0].blocks[0]
    assert block.text == "Native title" and block.block_type == "doc_title"
    assert "".join(char.char for char in block.char_boxes) == block.text


def test_mixed_ocr_only_uncovered_raster_and_preserves_native(tmp_path):
    pdf = _pdf(tmp_path / "mixed.pdf", native=True, raster=True)
    layout = Layout([_box("text", [90, 70, 280, 130]), _box("text", [90, 160, 490, 320], order=2)])
    ocr = OCR()
    result = ContractExtractionV3(layout=layout, ocr=ocr).extract(pdf)
    assert result.performance["routes"] == {"MIXED": 1}
    assert result.performance["ocr_calls"] == ocr.calls == 1
    blocks = result.document.pages[0].blocks
    assert sum(block.text == "Native title" for block in blocks) == 1
    assert sum(block.text == "Missing text" for block in blocks) == 1
    assert all(char.bbox.x0 >= 0 and char.bbox.y0 >= 0 for block in blocks for char in block.char_boxes)


def test_scanned_starts_layout_and_full_page_ocr_concurrently(tmp_path):
    pdf = _pdf(tmp_path / "scan.pdf", raster=True)
    layout_started, ocr_started = threading.Event(), threading.Event()

    class ParallelLayout(Layout):
        def predict_page(self, png):
            layout_started.set()
            assert ocr_started.wait(5)
            return super().predict_page(png)

    class ParallelOCR(OCR):
        def _request(self, png):
            ocr_started.set()
            assert layout_started.wait(5)
            return super()._request(png)

    layout, ocr = ParallelLayout([_box("text", [20, 20, 350, 100])]), ParallelOCR()
    result = ContractExtractionV3(layout=layout, ocr=ocr).extract(pdf)
    assert result.performance["routes"] == {"SCANNED": 1}
    assert result.performance["ocr_calls"] == ocr.calls == 1
    block = result.document.pages[0].blocks[0]
    assert block.text == "Missing text" and block.source == "PP-OCRv6"
    assert len(block.char_boxes) == len("Missingtext")


def test_vector_table_only_runs_for_true_table_region(tmp_path):
    pdf = _pdf(tmp_path / "table.pdf", table=True)
    layout = Layout([_box("table", [70, 170, 540, 350], 21)])
    result = ContractExtractionV3(layout=layout, ocr=OCR()).extract(pdf)
    assert result.performance["table_calls"] == 1
    assert result.performance["ocr_calls"] == 0
    assert any(block.raw_html.startswith("<table>") for block in result.document.pages[0].blocks)


def test_scanned_table_uses_one_page_ocr_and_safe_structure_fallback(tmp_path):
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    for x in (40, 150, 260):
        draw.line((x, 90, x, 170), fill="black", width=2)
    for y in (90, 130, 170):
        draw.line((40, y, 260, y), fill="black", width=2)
    data = BytesIO()
    image.save(data, format="PNG")
    pdf = tmp_path / "scanned-table.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_image(page.rect, stream=data.getvalue())
    doc.save(pdf)
    doc.close()
    layout = Layout([_box("table", [78, 178, 524, 344], 21)])
    ocr = OCR()
    result = ContractExtractionV3(layout=layout, ocr=ocr).extract(pdf)
    assert result.performance["routes"] == {"SCANNED": 1}
    assert result.performance["ocr_calls"] == ocr.calls == 1
    assert result.performance["table_calls"] == 1
    # Invalid or unassigned cell geometry must never replace canonical OCR text.
    assert any(block.text == "Missing text" for block in result.document.pages[0].blocks)


@pytest.mark.parametrize(
    "matrix", [[2, 0, 0, 2, 0, 0], [0, 2, -2, 0, 200, 0], [-2, 0, 0, -2, 200, 200], [0, -2, 2, 0, 0, 200]]
)
def test_table_transform_round_trip_for_quarter_rotations(matrix):
    from app.services.extractors.contract_v3.artifact import transform_point
    import numpy as np

    inverse = inverse_affine(matrix)
    point = [30, 40]
    actual = transform_point(transform_point(point, matrix), inverse)
    assert actual == pytest.approx(point)
    image = np.zeros((250, 250, 3), dtype=np.uint8)
    polygon = [[20, 20], [50, 20], [50, 50], [20, 50]]
    crop, geometry = upright_table_crop(image, polygon, matrix)
    assert crop.size > 0
    assert transform_point(
        transform_point(point, geometry["page_to_model"]), geometry["model_to_page"]
    ) == pytest.approx(point)


def test_polygon_ownership_and_ocr_word_evidence_keep_source_precision():
    from app.services.extractors.contract_v3.artifact import assign_order, owner, transform_point
    from app.services.extractors.contract_v3.ocr import word_provenance

    polygon = [[0, 5], [5, 0], [10, 5], [5, 10]]
    region = {
        "region_id": "diamond",
        "polygon": polygon,
        "bbox": [0, 0, 10, 10],
        "raw_score": 0.9,
        "raw_full_model_sequence": 0,
    }
    assert owner([4, 4, 6, 6], [region]) is region
    assert owner([0, 0, 2, 2], [region]) is None
    blocks = [
        {"block_id": "first", "region_id": "diamond", "bbox": [4, 4, 6, 6]},
        {"block_id": "later", "region_id": None, "bbox": [4, 20, 6, 22]},
    ]
    assign_order(blocks, [region])
    assert [block["reading_order"] for block in blocks] == [1, 2]
    result = {"text_word": [["付款"]], "text_word_region": [[[[1, 2], [21, 2], [21, 10], [1, 10]]]]}
    evidence, missing = word_provenance(result, 0, "付款", [30, 40], [0.5, 0, 0, 0.5, 0, 0], "request-1")
    assert missing == []
    assert [item["granularity"] for item in evidence] == ["word", "word"]
    assert evidence[0]["bbox"] == [15.5, 21.0, 25.5, 25.0]
    assert transform_point([31, 42], [0.5, 0, 0, 0.5, 0, 0]) == [15.5, 21.0]


def test_layout_client_uses_single_page_api_without_dynamic_batch():
    import httpx

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, json={"errorCode": 0, "result": {"model": "PP-DocLayoutV3", "batchSize": 1, "boxes": []}}
        )

    config = Settings(_env_file=None, ppdoclayout_endpoint="http://layout.test/layout-detection")
    client = PPDocLayoutV3Client(config, httpx.Client(transport=httpx.MockTransport(respond)))
    assert client.predict_page(b"png") == []
    assert requests[0].url.path == "/layout-detection"
    assert requests[0].content.find(b'"dynamicBatch":false') >= 0
