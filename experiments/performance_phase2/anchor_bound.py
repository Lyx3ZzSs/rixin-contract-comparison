"""Experimental anchor incumbent + exact DP bound; never force an anchor edge.

Unique, ordered, high-confidence prefix/suffix anchors propose a feasible path.
Gaps use the unchanged DP. This path is ONLY an upper bound: all original DP
transitions remain eligible. Any transition consumes <=3 pages and costs >=
0.025 per consumed page (match >=.05/2, group >=.22/3, gap >=.42/1).
Thus current_cost + .025 * remaining_pages is admissible. Strictly greater
states cannot improve the incumbent. Relaxation order and tie tolerance remain
unchanged. Missing/conflicting/duplicate anchors use FULL_DP_FALLBACK.
"""
from collections import Counter
import copy
import inspect
import textwrap

from app.services import page_alignment as original_module
from app.services.page_alignment import PageSequenceAligner


class AnchorBoundAligner(PageSequenceAligner):
    def align(self, original, compare):
        worker = copy.copy(self)
        worker.audit = {"full_dp_fallback_count": 0, "dp_states_pruned": 0, "strong_anchor_count": 0}
        result = PageSequenceAligner.align(worker, original, compare)
        result.performance["counters"].update(worker.audit)
        return result

    def _anchor_upper_bound(self, original, compare):
        def key(item):
            return (item.text[:128], item.text[-128:]) if item.stable else None
        a, b = [key(x) for x in original], [key(x) for x in compare]
        ca, cb = Counter(a), Counter(b)
        shared = set(a) & set(b) - {None}
        if not shared or any(ca[k] != 1 or cb[k] != 1 for k in shared):
            return None
        right_index = {k: i for i, k in enumerate(b) if k in shared}
        anchors = [(i, right_index[k]) for i, k in enumerate(a) if k in shared]
        if any(y[1] <= x[1] for x,y in zip(anchors,anchors[1:])):
            return None
        scores = [self._similarity(original[i], compare[j])[0] for i,j in anchors]
        if any(score < .98 for score in scores):
            return None
        self.audit["strong_anchor_count"] = len(anchors)
        original_by_page = {x.pages: x for x in original}
        compare_by_page = {x.pages: x for x in compare}
        total = 0.0
        oi = ci = 0
        for (i,j), score in [*zip(anchors,scores), ((len(original),len(compare)),None)]:
            gap = PageSequenceAligner._dynamic_alignment(self, original[oi:i], compare[ci:j])
            for entry in gap:
                if len(entry.original_pages) == len(entry.compare_pages) == 1:
                    total += self._match_cost(entry.alignment_score)
                elif entry.original_pages and entry.compare_pages:
                    total += self._group_cost(entry.alignment_score)
                else:
                    total += self._gap_cost(original_by_page[entry.original_pages] if entry.original_pages else compare_by_page[entry.compare_pages])
            if score is not None:
                total += self._match_cost(score)
            oi, ci = i + 1, j + 1
        return total


# Reuse the current implementation verbatim, inserting only a certified bound.
# Assertion fails loudly if the upstream DP changes; no copied scoring formula.
source = textwrap.dedent(inspect.getsource(PageSequenceAligner._dynamic_alignment))
assert source.count('    n, m = len(original), len(compare)') == 1
assert source.count('            if current == infinity:') == 1
source = source.replace('    n, m = len(original), len(compare)', '''    upper = self._anchor_upper_bound(original, compare)
    if upper is None:
        self.audit["full_dp_fallback_count"] += 1
        return PageSequenceAligner._dynamic_alignment(self, original, compare)
    n, m = len(original), len(compare)''')
source = source.replace('            if current == infinity:', '''            if current + .025 * (n - i + m - j) > upper + (n + m + 1) * 1e-8:
                self.audit["dp_states_pruned"] += 1
                continue
            if current == infinity:''')
namespace = dict(vars(original_module))
exec(compile(source, __file__, "exec"), namespace)
AnchorBoundAligner._dynamic_alignment = namespace['_dynamic_alignment']
