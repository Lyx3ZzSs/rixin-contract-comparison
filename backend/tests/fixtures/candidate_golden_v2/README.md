# Candidate contract Golden v2

This is a non-blocking, development-only working oracle for five contracts reviewed on 2026-09-02.
It does not modify or participate in the sealed `contract-golden-v1` gate.

`oracle.json` records stable semantic `case_id` values for 34 true-positive diffs, 69 false-positive
diffs, and 12 false-negative events. `before_diff_id` is trace metadata for the reviewed run only;
matching and future promotion must use `case_id`, `page`, and `semantic_fact`.

Phase 1 may downgrade an observation-related false positive to `NEEDS_REVIEW`, but must not silently
drop its candidate evidence. All 34 positive facts must remain user-visible, and the 12 known false
negatives must not increase.
