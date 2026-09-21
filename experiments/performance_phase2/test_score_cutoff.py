import random

from app.services.matching.candidate_scoring import CandidateScoringMixin
from score_cutoff import ExactCutoffScoring


def test_raw_score_is_preserved_including_below_cutoff_and_length_caps():
    old, new = CandidateScoringMixin(), ExactCutoffScoring()
    cases = [('', ''), ('', 'abc'), ('a', 'b'), ('abc', 'xyzabcuvw'),
             ('交货付款条款' * 15, '交货付款条款' * 8 + '争议管辖'),
             ('aa bb cc', 'cc aa bb'), ('a' * 150, 'b' * 150)]
    rng = random.Random(20260921)
    for _ in range(250):
        a = ''.join(rng.choices('甲乙交付abc ', k=rng.randrange(1, 500)))
        b = a[:rng.randrange(len(a))] + ''.join(rng.choices('甲乙交付abc ', k=rng.randrange(1, 300)))
        cases.append((a, b))
    for a, b in cases:
        assert new._body_score_details(a, b) == old._body_score_details(a, b)
    assert new.cutoff_counts['raw_score_fallback'] > 0
