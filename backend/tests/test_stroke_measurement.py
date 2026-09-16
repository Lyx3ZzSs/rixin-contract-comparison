from itertools import groupby

import numpy as np
import pytest

from app.services.signing_region.visual import OpenCvVisualSignatureDetector


def reference_ratio(mask):
    height, width = mask.shape
    if not height or not width:
        return 0.0

    def runs(rows):
        return [max((sum(1 for _ in group) for value, group in groupby(map(bool, row)) if value), default=0)
                for row in rows]

    horizontal, vertical = runs(mask), runs(mask.T)
    return max(max(horizontal) / width if sum(x >= width * .12 for x in horizontal) >= 3 else 0.0,
               max(vertical) / height if sum(x >= height * .30 for x in vertical) >= 3 else 0.0)


@pytest.mark.parametrize("shape", [(0, 5), (5, 0), (1, 1), (2, 200), (3, 100), (50, 73), (260, 1025)])
@pytest.mark.parametrize("density", [0, .1, .5, .95, 1])
def test_stroke_ratio_matches_run_definition_across_chunks_and_transposes(shape, density):
    mask = np.random.default_rng(917).random(shape) < density
    for data in [mask, mask.T, mask[:, ::-1], mask.astype(np.uint8) * 255]:
        assert OpenCvVisualSignatureDetector._longest_dark_stroke_ratio(data) == reference_ratio(data)


def test_three_qualifying_strokes_are_required_at_the_original_thresholds():
    mask = np.zeros((100, 100), dtype=bool)
    mask[:2, :12] = True
    assert OpenCvVisualSignatureDetector._longest_dark_stroke_ratio(mask) == 0
    mask[2, :12] = True
    assert OpenCvVisualSignatureDetector._longest_dark_stroke_ratio(mask) == .12
    mask[20:50, 20:23] = True
    assert OpenCvVisualSignatureDetector._longest_dark_stroke_ratio(mask) == .3
