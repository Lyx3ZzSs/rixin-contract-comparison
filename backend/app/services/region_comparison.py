"""Compare located content whose layout representation differs between versions.

Physical source spans remain immutable. Only complete comparisons (or explicit
observations) claim spans; downstream clause splitting consumes that ledger.
"""
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
import unicodedata

from app.models import BBox, ComparisonObservation, DiffItem, EvidenceBox, TextBlock


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def lines(blocks):
    """Group by baseline before ordering horizontally; OCR y jitter is not order."""
    rows = []
    for block in sorted(blocks, key=lambda b: (b.bbox.y0, b.bbox.x0)):
        center = (block.bbox.y0 + block.bbox.y1) / 2
        height = max(1, block.bbox.y1 - block.bbox.y0)
        for row in reversed(rows):
            anchor = row[0]
            ac = (anchor.bbox.y0 + anchor.bbox.y1) / 2
            ah = max(1, anchor.bbox.y1 - anchor.bbox.y0)
            if abs(center - ac) <= min(height, ah) * .6:
                row.append(block)
                break
        else:
            rows.append([block])
    return [sorted(row, key=lambda b: b.bbox.x0) for row in rows]


def ordered(blocks):
    return [block for row in lines(blocks) for block in row]


@dataclass(frozen=True)
class ClaimedSpan:
    side: str
    block_id: str
    start: int
    end: int


@dataclass
class RegionComparisonResult:
    diffs: list[DiffItem] = field(default_factory=list)
    observations: list[ComparisonObservation] = field(default_factory=list)
    claimed_spans: set[ClaimedSpan] = field(default_factory=set)
    claimed_rows: set[int] = field(default_factory=set, repr=False)
    decisions: list[dict] = field(default_factory=list)

    def claims(self, side, block):
        return ClaimedSpan(side, block.block_id, 0, len(block.text)) in self.claimed_spans

    def claim(self, side, blocks):
        self.claimed_spans.update(ClaimedSpan(side, b.block_id, 0, len(b.text)) for b in blocks)

    def diagnostics(self):
        return {"decisions": self.decisions,
                "claimed_spans": [vars(s) for s in sorted(self.claimed_spans, key=lambda s: (s.side, s.block_id, s.start))],
                "observations": [o.model_dump(mode="json") for o in self.observations]}


class RegionComparator:
    def __init__(self, original, compare, alignment=None):
        self.documents = {"original": original, "compare": compare}
        self.pages = {side: {p.page_no: p for p in doc.pages} for side, doc in self.documents.items()}
        self.alignment = alignment
        self.result = RegionComparisonResult()

    def counterpart(self, side, page):
        other = "compare" if side == "original" else "original"
        numbers = ((self.alignment.counterparts_for_original if side == "original" else
                    self.alignment.counterparts_for_compare)(page.page_no)
                   if self.alignment is not None else [page.page_no])
        if len(numbers) != 1:
            return None
        return self.pages[other].get(numbers[0])

    @staticmethod
    def plain(block):
        return (bool(normalized(block.text)) and '<table' not in block.text.lower()
                and block.block_type in {'text', 'paragraph', 'table_title'}
                and block.block_role not in {'cover_metadata', 'page_footer', 'noise'}
                and block.flow_role not in {'margin', 'noise', 'non_text'}
                and block.enter_clause_compare is not False)

    @staticmethod
    def nearby(block, bounds, page, target, margin=.025):
        b = block.bbox
        x, y = (b.x0+b.x1)/2/target.width, (b.y0+b.y1)/2/target.height
        return (bounds.x0/page.width-margin <= x <= bounds.x1/page.width+margin
                and bounds.y0/page.height-margin <= y <= bounds.y1/page.height+margin)

    def compare_documents(self):
        for side, doc in self.documents.items():
            other = "compare" if side == "original" else "original"
            for page in doc.pages:
                target = self.counterpart(side, page)
                if target is None:
                    continue
                for block in page.blocks:
                    if block.block_role != 'table_caption' or self.result.claims(side, block):
                        continue
                    candidates = [b for b in target.blocks if self.plain(b)
                                  and not self.result.claims(other, b)
                                  and self.nearby(b, block.bbox, page, target)]
                    ranked = sorted(((SequenceMatcher(None, normalized(block.text), normalized(b.text), autojunk=False).ratio(), b)
                                     for b in candidates), key=lambda pair: pair[0], reverse=True)
                    if ranked and (len(ranked) == 1 or ranked[0][0] >= .65 and ranked[0][0] - ranked[1][0] >= .15):
                        self.compare_blocks(side, [block], [ranked[0][1]], 'caption_representation')
                groups = {}
                for block in page.blocks:
                    if block.block_type == 'table' and block.structure_status == 'UNSTRUCTURED_TABLE_PRESENT':
                        groups.setdefault(block.layout_block_id or block.block_id, []).append(block)
                for blocks in groups.values():
                    if all(self.result.claims(side, b) for b in blocks):
                        continue
                    bounds = blocks[0].layout_bbox or blocks[0].bbox
                    candidates = [b for b in target.blocks
                                  if (self.plain(b) or b.structure_status == 'UNSTRUCTURED_TABLE_PRESENT')
                                  and not self.result.claims(other, b)
                                  and self.nearby(b, bounds, page, target, .045)]
                    a = ''.join(normalized(b.text) for b in ordered(blocks))
                    b = ''.join(normalized(b.text) for b in ordered(candidates))
                    score = SequenceMatcher(None, a, b, autojunk=False).ratio()
                    if candidates and score >= .65:
                        self.compare_blocks(side, blocks, candidates, 'unstructured_region_text')
                    else:
                        # An extraction failure is not evidence of absence.
                        self.result.claim(side, blocks)
                        self.result.claim(other, candidates)
                        evidence = [EvidenceBox(page_no=b.page_no, bbox=b.bbox, text=b.text,
                                                method='unstructured_region') for b in blocks]
                        counterpart_evidence = [EvidenceBox(page_no=b.page_no, bbox=b.bbox, text=b.text,
                                                           method='unstructured_region') for b in candidates]
                        self.result.observations.append(ComparisonObservation(
                            observation_id=f'REGION-{side}-{blocks[0].block_id}', reason='REGION_ALIGNMENT_UNCERTAIN',
                            title='表格区域文字对应关系待复核', message='区域文字已保留，但尚未建立可靠的对侧范围。',
                            original_evidence=evidence if side == 'original' else counterpart_evidence,
                            compare_evidence=evidence if side == 'compare' else counterpart_evidence,
                        ))
        return self.result

    def reconcile_rows(self, original, compare, pairs, matcher):
        for oi, ci, _ in pairs:
            left, right = original[oi], compare[ci]
            for i, j in matcher.align_rows(left, right):
                if (i is None) == (j is None):
                    continue
                side, table, index = ('original', left, i) if i is not None else ('compare', right, j)
                row = table.rows[index]
                page = self.pages[side].get(row.page_no)
                target = self.counterpart(side, page) if page else None
                if target is None:
                    continue
                cells = [c for c in row.cells if normalized(c.text)]
                # Only a single physical band can be safely recovered from plain
                # lines. Large narrative cells remain in the structured path.
                if not cells or any(c.bbox is None for c in cells):
                    continue
                bounds = BBox(x0=min(c.bbox.x0 for c in cells), y0=min(c.bbox.y0 for c in cells),
                              x1=max(c.bbox.x1 for c in cells), y1=max(c.bbox.y1 for c in cells))
                if bounds.y1-bounds.y0 > page.height*.07:
                    continue
                other = 'compare' if side == 'original' else 'original'
                candidates = [b for b in target.blocks if self.plain(b) and not self.result.claims(other, b)
                              and self.nearby(b, bounds, page, target)]
                bands = lines(candidates)
                text = ''.join(normalized(c.text) for c in cells)
                ranked = []
                for start in range(len(bands)):
                    for end in range(start+1, min(len(bands), start+3)+1):
                        group = [b for line in bands[start:end] for b in line]
                        value = ''.join(normalized(b.text) for b in group)
                        score = SequenceMatcher(None, text, value, autojunk=False).ratio()
                        ranked.append((score, group))
                ranked.sort(key=lambda p: p[0], reverse=True)
                if not ranked or ranked[0][0] < .65:
                    continue
                if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .12:
                    continue
                # Physical cells supply source text even if OCR split the label.
                sources = [TextBlock(block_id=f'{c.source_block_id}:cell:{c.source_row}:{c.source_col}',
                                     page_no=c.page_no, bbox=c.bbox, text=c.text,
                                     confidence=min((b.confidence for b in page.blocks if b.confidence is not None
                                                     and self.nearby(b, c.bbox, page, page, 0)
                                                     and normalized(b.text) in normalized(c.text)), default=None))
                           for c in cells]
                occluded = any(b.block_type in {'seal', 'image', 'figure'}
                               and self.nearby(b, bounds, page, target) for b in target.blocks)
                self.compare_blocks(side, sources, ranked[0][1], 'row_representation', uncertain_presence=occluded)
                self.result.claimed_rows.add(id(row))
                # Also claim physical OCR lines belonging to this cell band.
                physical = [b for b in page.blocks if b.block_type == 'table'
                            and self.nearby(b, bounds, page, page, 0)
                            and normalized(b.text) in text]
                self.result.claim(side, physical)

    def compare_blocks(self, source_side, sources, targets, reason, *, uncertain_presence=False):
        left, right = (sources, targets) if source_side == 'original' else (targets, sources)
        left, right = ordered(left), ordered(right)
        texts = [''.join(normalized(b.text) for b in blocks) for blocks in (left, right)]
        for tag, i, j, k, end in SequenceMatcher(None, *texts, autojunk=False).get_opcodes():
            if tag == 'equal':
                continue
            before, after = texts[0][i:j], texts[1][k:end]
            confidence = min((b.confidence for b in [*left, *right] if b.confidence is not None), default=None)
            uncertain = (uncertain_presence or not re.search(r'[\w\u4e00-\u9fff]', before+after)
                         or confidence is not None and confidence < .85)
            self.result.diffs.append(DiffItem(
                diff_id=f'REGION-{len(self.result.diffs)+1}', source_type='table',
                diff_type='MODIFY' if before and after else 'DELETE' if before else 'ADD',
                title='表格区域文字变化', original_text=texts[0], compare_text=texts[1],
                original_snippet=before, compare_snippet=after,
                readable_change=f'{before or "∅"} → {after or "∅"}',
                quality_status='NEEDS_REVIEW' if uncertain else 'NORMAL',
                text_confidence=confidence,
                original_evidence=self.evidence(left, i, j, 'DELETE'),
                compare_evidence=self.evidence(right, k, end, 'ADD'),
                match_score_details={'change_decision': 'NEEDS_REVIEW' if uncertain else 'CONFIRMED',
                                     'decision_reason': 'REGION_READING_UNCERTAIN' if uncertain else reason,
                                     'presence_uncertain': uncertain_presence,
                                     'region_text_ranges': {'original': [i, j], 'compare': [k, end]}},
            ))
        self.result.claim('original', left)
        self.result.claim('compare', right)
        self.result.decisions.append({'reason': reason, 'original_blocks': [b.block_id for b in left],
                                      'compare_blocks': [b.block_id for b in right],
                                      'equal': texts[0] == texts[1]})

    @staticmethod
    def evidence(blocks, start, end, highlight):
        evidence, offset = [], 0
        for block in blocks:
            text = normalized(block.text)
            lo, hi = max(start, offset), min(end, offset+len(text))
            if lo < hi:
                evidence.append(EvidenceBox(page_no=block.page_no, bbox=block.bbox,
                                            text=text[lo-offset:hi-offset], method='region_source_span',
                                            granularity='source_text', highlight_type=highlight))
            offset += len(text)
        return evidence
