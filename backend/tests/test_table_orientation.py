"""Direction decisions must conserve content and PDF evidence, including rollback."""

from copy import deepcopy
import json

import httpx
import numpy as np
from PIL import Image, ImageDraw
import pytest

from app.config import Settings
from app.services.extractors.contract_v3.artifact import enclosing, rectangle, transform_point
from app.services.extractors.contract_v3.table_orientation import TableOrientation, decide_direction
from app.services.extractors.contract_v3.table_transform import inverse_affine, orient_table_crop
from app.services.extractors.contract_v3.tables import TableResolver, _publish


def scores(a=0.2, b=0.96, c=0.3, text="合同项目"):
    return {
        str(angle): [dict(text=text, score=value) for _ in range(6)] for angle, value in zip((0, 90, 270), (a, b, c))
    }


@pytest.mark.parametrize(
    "values,expected",
    [
        (scores(), "ROTATE"),
        (scores(0.95, 0.98, 0.3), "KEEP"),
        (scores(0.2, 0.9, 0.89), "UNCERTAIN"),
        (scores(0.1, 0.7, 0.3), "UNCERTAIN"),
        (scores(text="123.50%"), "UNCERTAIN"),
        ({k: v[:3] for k, v in scores().items()}, "UNCERTAIN"),
    ],
)
def test_decision_requires_directional_margin_and_support(values, expected):
    assert decide_direction(values)["status"] == expected


def fixture():
    """Three rows/two columns, displayed sideways; text tokens remain in page space."""
    image = Image.new("RGB", (240, 180), "white")
    draw = ImageDraw.Draw(image)
    for x in (2, 120, 238):
        draw.line((x, 2, x, 178), fill="black", width=2)
    for y in (2, 61, 119, 178):
        draw.line((2, y, 238, y), fill="black", width=2)
    matrix = [0, -1, 1, 0, 0, 240]
    blocks = []
    for row in range(3):
        for col in range(2):
            box = [25 + 110 * col, 25 + 53 * row, 85 + 110 * col, 42 + 53 * row]
            poly = [transform_point(p, matrix) for p in rectangle(box)]
            blocks.append(
                dict(
                    block_id=f"b{row}{col}",
                    region_id="t",
                    source="PP-OCRv6",
                    text=f"项目{row}{col}",
                    bbox=enclosing(poly),
                    polygon=poly,
                    ocr_request_id="ocr",
                    ocr_char_provenance=[dict(char=f"项目{row}{col}", text_index=0, bbox=enclosing(poly))],
                )
            )
    artifact = dict(
        native_chars=[],
        blocks=blocks,
        regions=[
            dict(region_id="t", raw_layout_type="table", bbox=[0, 0, 180, 240], polygon=rectangle([0, 0, 180, 240]))
        ],
        coordinate_transform=dict(native_to_pixel=[1, 0, 0, 1, 0, 0]),
    )
    return artifact, image.transpose(Image.Transpose.ROTATE_90)


class Direction:
    def analyze(self, *args):
        return dict(status="ROTATE", angle_ccw=270, reason="test_evidence")

    def reset(self):
        pass


def cfg(mode="enabled"):
    return Settings(
        _env_file=None,
        table_structure_mode="legacy",
        table_orientation_mode=mode,
        table_orientation_endpoint="http://orientation.test",
    )


def meaningful(artifact):
    result = deepcopy(artifact)
    for block in result["blocks"]:
        block.pop("table_model_diagnostics", None)
        block.get("table_validation", {}).get("metrics", {}).pop("validation_seconds", None)
    for table in result["tables"]:
        table.pop("model_diagnostics", None)
        table["validation"]["metrics"].pop("validation_seconds", None)
    return result


def test_sideways_complete_grid_is_reoriented_and_tokens_conserved():
    artifact, image = fixture()
    canonical = deepcopy(artifact["blocks"])
    baseline = deepcopy(artifact)
    TableResolver(cfg("off")).resolve(baseline, None, image)
    TableResolver(cfg(), orientation=Direction()).resolve(artifact, None, image)
    old, new = baseline["tables"][0], artifact["tables"][0]
    assert old["status"] == new["status"] == "CONSISTENT"
    assert (max(c["row"] for c in old["cells"]), max(c["column"] for c in old["cells"])) == (1, 2)
    assert (max(c["row"] for c in new["cells"]), max(c["column"] for c in new["cells"])) == (2, 1)
    assert [c["text"] for c in new["cells"]] == [f"项目{r}{c}" for r in range(3) for c in range(2)]
    assert new["model_diagnostics"]["orientation"]["adopted"]
    for before, after in zip(canonical, artifact["blocks"]):
        for field in ("text", "bbox", "polygon", "ocr_char_provenance"):
            assert before[field] == after[field]


def test_shadow_does_not_change_any_published_structure():
    original, image = fixture()
    shadow = deepcopy(original)
    TableResolver(cfg("off")).resolve(original, None, image)
    TableResolver(cfg("shadow"), orientation=Direction()).resolve(shadow, None, image)
    assert meaningful(original) == meaningful(shadow)
    assert not shadow["tables"][0]["model_diagnostics"]["orientation"]["adopted"]


def test_failed_candidate_cannot_leave_partial_mutations(monkeypatch):
    artifact, image = fixture()
    baseline = deepcopy(artifact)
    TableResolver(cfg("off")).resolve(baseline, None, image)
    resolver = TableResolver(cfg(), orientation=Direction())

    def fail(target, *args):
        target["blocks"][0]["text"] = "corrupted"
        raise ValueError("invalid grid")

    monkeypatch.setattr(resolver, "_oriented_candidate", fail)
    resolver.resolve(artifact, None, image)
    assert meaningful(artifact) == meaningful(baseline)


@pytest.mark.parametrize("angle", [0, 90, 270])
@pytest.mark.parametrize(
    "display", [[1, 0, 0, 1, 0, 0], [0, -2, 2, 0, 0, 240], [-2, 0, 0, -2, 240, 180], [0, 2, -2, 0, 180, 0]]
)
def test_rotation_roundtrip_respects_pdf_display_transform(angle, display):
    image = np.zeros((180, 240, 3), dtype=np.uint8)
    image[20, 40] = 255
    transform = dict(
        page_to_model=display, model_to_page=inverse_affine(display), page_polygon=rectangle([0, 0, 120, 90])
    )
    rotated, actual = orient_table_crop(image, transform, angle)
    # Pixel center must move to the bright pixel; edges use W/H rather than W-1/H-1.
    cx, cy = transform_point([40.5, 20.5], actual["crop_to_oriented"])
    assert rotated[int(cy), int(cx)].tolist() == [255] * 3
    for p in ([0, 0], [30, 40], [120, 90]):
        assert transform_point(transform_point(p, actual["page_to_model"]), actual["model_to_page"]) == pytest.approx(p)


def test_http_contract_cache_and_probe_text_is_not_published():
    artifact, image = fixture()
    before = deepcopy(artifact)
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json=dict(
                schema_version=1,
                request_id=body["request_id"],
                preserves_input_orientation=True,
                model_sha256="a" * 64,
                angles=scores(),
            ),
        )

    probe = TableOrientation(cfg(), httpx.Client(transport=httpx.MockTransport(handler)))
    transform = dict(page_to_model=[1, 0, 0, 1, 0, 0])
    for _ in range(2):
        decision = probe.analyze(np.asarray(image)[:, :, ::-1], transform, artifact, artifact["regions"][0], [])
        assert decision["status"] == "ROTATE"
    assert len(calls) == 1 and decision["cache_hit"]
    assert artifact == before


def test_budget_and_bad_provider_fail_regionally():
    artifact, image = fixture()

    def handler(request):
        return httpx.Response(
            200,
            json=dict(
                schema_version=1,
                request_id="wrong",
                preserves_input_orientation=False,
                model_sha256="a" * 64,
                angles=scores(),
            ),
        )

    probe = TableOrientation(cfg(), httpx.Client(transport=httpx.MockTransport(handler)))
    args = (np.asarray(image), dict(page_to_model=[1, 0, 0, 1, 0, 0]), artifact, artifact["regions"][0], [])
    assert probe.analyze(*args)["status"] == "FAILED"
    probe.elapsed = probe.config.table_orientation_document_budget
    assert probe.analyze(*args)["reason"] == "DOCUMENT_BUDGET_EXHAUSTED"


@pytest.mark.parametrize("native", [False, True])
def test_numeric_or_native_evidence_skips_network(native):
    artifact, image = fixture()
    for block in artifact["blocks"]:
        block["text"] = "123.45%"

    def unexpected(request):
        pytest.fail("Native/numeric evidence must not call scoring")

    probe = TableOrientation(cfg(), httpx.Client(transport=httpx.MockTransport(unexpected)))
    result = probe.analyze(
        np.asarray(image),
        dict(page_to_model=[1, 0, 0, 1, 0, 0]),
        artifact,
        artifact["regions"][0],
        [dict(source="native_pdf")] if native else [],
    )
    assert result["reason"] == ("NATIVE_TEXT" if native else "NON_DIRECTIONAL_TEXT")


def test_detection_failure_falls_back_without_changing_evidence(monkeypatch):
    from app.services.extractors.contract_v3.ocr import SelectiveOCRRouter
    from app.services.extractors.base import DocumentExtractionError

    artifact, image = fixture()
    artifact["blocks"] = []
    before = deepcopy(artifact)

    def fail(*args):
        raise DocumentExtractionError("offline")

    monkeypatch.setattr(SelectiveOCRRouter, "_request", fail)
    result = TableOrientation(cfg()).analyze(
        np.asarray(image),
        dict(page_to_model=[1, 0, 0, 1, 0, 0]),
        artifact,
        artifact["regions"][0],
        [],
    )
    assert result["status"] == "FAILED" and result["detection_requests"] == 1
    assert result["score_requests"] == 0 and artifact == before


def test_candidate_missing_tokens_is_rejected_atomically(monkeypatch):
    artifact, image = fixture()
    baseline = deepcopy(artifact)
    TableResolver(cfg("off")).resolve(baseline, None, image)
    resolver = TableResolver(cfg(), orientation=Direction())
    candidate = resolver._oriented_candidate

    def lose_token(*args):
        table = candidate(*args)
        table["cells"][0]["tokens"].pop()
        return table

    monkeypatch.setattr(resolver, "_oriented_candidate", lose_token)
    resolver.resolve(artifact, None, image)
    assert meaningful(artifact) == meaningful(baseline)
    assert not artifact["tables"][0]["model_diagnostics"]["orientation"]["token_conservation"]


@pytest.mark.parametrize("local_to_page", [[1, 0, 0, 1, 0, 0], [0, -1, 1, 0, 0, 20], [0, 1, -1, 0, 20, 0]])
def test_boundary_token_ownership_is_invariant_in_reading_frame(local_to_page):
    def mapped(box):
        polygon = [transform_point(p, local_to_page) for p in rectangle(box)]
        return dict(polygon=polygon, bbox=enclosing(polygon))

    cells = [dict(row=r, column=0, rowspan=1, colspan=1, **mapped([0, r * 10, 20, (r + 1) * 10])) for r in (0, 1)]
    token = dict(id="word:0", text="边界", source="PP-OCRv6", **mapped([5, 9, 15, 11]))
    region = dict(region_id="t", bbox=[0, 0, 20, 20])
    table = _publish(
        dict(blocks=[]),
        region,
        cells,
        [token],
        "raster_cell_graph",
        page_to_model=inverse_affine(local_to_page),
        validate_in_model_space=True,
    )
    assert table["status"] == "CONSISTENT"
    assert [cell["text"] for cell in table["cells"]] == ["", "边界"]
