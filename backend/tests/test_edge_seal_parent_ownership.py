"""Real three-island clipped seal must retain its detected physical parent."""
import itertools
import json
from pathlib import Path

import pytest

from app.models import BBox, DiffItem, EvidenceBox, Page
from app.services.signing_region.models import SigningVisualObject as Object
from app.services.signing_region.models import SigningVisualObjectType as Kind
from app.services.signing_region.visual_recall import (
    SigningVisualRecallResult, WholePageSigningVisualRecall as Recall,
)


def case():
    data = json.loads((Path(__file__).parent / 'fixtures/tieling_edge_seal_parent.json').read_text())
    return Page.model_validate(data['page']), [Object.model_validate(o) for o in data['objects']]


def legacy(page):
    return DiffItem(diff_id='D003', diff_type='ADD', source_type='seal',
                    compare_evidence=[EvidenceBox(page_no=2, bbox=page.blocks[0].layout_bbox,
                                                  method='seal_region')])


@pytest.mark.parametrize('order', list(itertools.permutations(range(3))))
def test_layout_parent_owns_three_islands_independent_of_input_order(order):
    page, objects = case()
    result = Recall._resolve_layout_edge_seals(page, [objects[i] for i in order])
    assert len(result) == 1
    parent = result[0]
    assert parent.object_type == Kind.EDGE_SEAM_SEAL
    assert parent.bbox.y0 <= 325.5 and parent.bbox.y1 >= 444.5
    assert set(parent.raw_ref['component_ids']) == {o.object_id for o in objects}
    assert parent.raw_ref['layout_owner_id'] == page.blocks[0].layout_block_id
    additions = Recall._aggregate_edge_fragments(Recall._dedupe_objects(result))
    seals, signing = Recall().reconcile_diffs(
        SigningVisualRecallResult(additions=additions), [legacy(page)], [], start_index=9)
    assert len(seals + signing) == 1
    assert seals[0].compare_evidence[0].bbox.y1 >= 444.5


@pytest.mark.parametrize('order', list(itertools.permutations(range(3))))
def test_fragment_fusion_never_shrinks_existing_parent_or_adds_duplicate(order):
    page, objects = case()
    original = legacy(page)
    parent_box = original.compare_evidence[0].bbox.model_copy()
    result = SigningVisualRecallResult(additions=[objects[i] for i in order])
    seals, signing = Recall().reconcile_diffs(result, [original], [], start_index=9)
    assert len(seals + signing) == 1
    assert not result.new_diff_ids
    assert seals[0].compare_evidence[0].bbox == parent_box


def test_no_layout_parent_does_not_merge_by_proximity_alone():
    page, objects = case()
    page.blocks = []
    assert Recall._resolve_layout_edge_seals(page, objects) == objects


def test_ocr_line_boxes_are_not_physical_seal_parents():
    page, objects = case()
    for block in page.blocks:
        block.layout_bbox = None
    assert Recall._resolve_layout_edge_seals(page, objects) == objects


def test_conflicting_layout_parents_abstain():
    page, objects = case()
    other = page.blocks[0].model_copy(deep=True)
    other.layout_block_id = 'another-seal'
    other.layout_bbox.x0 -= 2
    page.blocks.append(other)
    assert Recall._resolve_layout_edge_seals(page, objects) == objects


def test_unrelated_stamp_outside_parent_is_preserved():
    page, objects = case()
    separate = objects[0].model_copy(deep=True)
    separate.object_id = 'separate-round'
    separate.bbox = BBox(x0=540, y0=500, x1=598, y1=558)
    result = Recall._resolve_layout_edge_seals(page, [*objects, separate])
    assert len(result) == 2
    assert separate in result


def test_non_seal_layout_cannot_supply_ownership():
    page, objects = case()
    for block in page.blocks:
        block.block_type = 'text'
    assert Recall._resolve_layout_edge_seals(page, objects) == objects


def test_already_classified_slender_edge_fragments_keep_existing_grouping():
    page, objects = case()
    for obj in objects:
        obj.object_type = Kind.EDGE_SEAM_SEAL
        obj.raw_ref['reason'] = 'PAGE_EDGE_RED_FRAGMENT'
    assert Recall._resolve_layout_edge_seals(page, objects) == objects


def test_single_circle_with_inner_red_island_uses_existing_deduplication():
    page, objects = case()
    for obj in objects[1:]:
        obj.object_type = Kind.EDGE_SEAM_SEAL
    assert Recall._resolve_layout_edge_seals(page, objects) == objects
