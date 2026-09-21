"""Exact-score pilot. A cutoff miss MUST materialize the original raw score.

body = max(ratio, capped_token, capped_partial). Therefore capped_partial must
exceed max(ratio, capped_token) to improve body. Pass that derived threshold to
RapidFuzz, but do not discard scores below it: raw partial is a public field and
also drives acceptance/method selection. Recompute misses without a cutoff.
This preserves every candidate and its raw score; no top-k or recall changes.
"""
from collections import Counter

from app.services.matching._fuzz import fuzz, partial_score
from app.services.matching.candidate_scoring import CandidateScoringMixin


class ExactCutoffScoring(CandidateScoringMixin):
    def _body_score_details(self, left, right):
        if not hasattr(self, 'cutoff_counts'):
            self.cutoff_counts = Counter(partial_score_pruned=0)
        self.cutoff_counts['score_details_count'] += 1
        self.cutoff_counts['chars_processed'] += len(left) + len(right)
        ratio = self._ratio_score(left, right)
        token = self._token_score(left, right)
        low, high = sorted((len(left), len(right)))
        coverage = low / high if high else 1.0
        token_cap, partial_cap = (70., 70.) if low < 8 else (
            (76., 72.) if coverage < .5 else (
                (86., 82.) if coverage < .7 else (100., 92. if coverage < .85 else 100.)))
        capped_token = min(token, token_cap)
        cutoff = max(ratio, capped_token)
        self.cutoff_counts['partial_score_called'] += 1
        if fuzz is None or not left or not right:
            partial = partial_score(left, right)
        else:
            partial = float(fuzz.partial_ratio(left, right, score_cutoff=cutoff))
            self.cutoff_counts['cutoff_calls'] += 1
            if partial == 0 and cutoff > 0:
                self.cutoff_counts['raw_score_fallback'] += 1
                self.cutoff_counts['fallback_input_chars'] += len(left) + len(right)
                partial = partial_score(left, right)
        return dict(body_ratio_score=ratio, body_token_score=token,
                    body_token_capped_score=capped_token, body_partial_score=partial,
                    body_length_coverage=coverage,
                    body_score=max(ratio, capped_token, min(partial, partial_cap)))
