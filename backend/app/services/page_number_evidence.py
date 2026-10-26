"""Shared page-counter semantics and explicit, paired physical evidence."""
from dataclasses import dataclass
import re
import unicodedata

from app.models import BBox, Document, Page, TextBlock

PAGE_NUMBER_PATTERN = re.compile(
    r"^(?:第?\s*\d+\s*页?|第\s*\d+\s*页\s*共\s*\d+\s*页|共\s*\d+\s*页\s*第\s*\d+\s*页|[-—–_]*\s*\d+\s*[-—–_]*)$"
)


def page_number_key(text: str) -> tuple[int, int | None] | None:
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    if not PAGE_NUMBER_PATTERN.fullmatch(value):
        return None
    numbers = [int(n) for n in re.findall(r"\d+", value)]
    if len(numbers) == 1:
        return numbers[0], None
    return (numbers[1], numbers[0]) if value.startswith('共') else (numbers[0], numbers[1])


def page_counters(page: Page | None) -> list[TextBlock]:
    return _counter_candidates(page, require_role=True)


def _counter_candidates(page: Page | None, *, require_role: bool) -> list[TextBlock]:
    if page is None or page.width <= 0 or page.height <= 0:
        return []
    counters = [b for b in page.blocks
            if page_number_key(b.text) is not None
            and b.bbox.y0 >= page.height * .75
            and b.bbox.y1 <= page.height * .995
            and 0 < b.bbox.y1 - b.bbox.y0 <= 25
            and (not require_role or b.block_role == 'page_footer'
                 or b.block_type in {'number', 'footer', 'page_footer'}
                 or b.bbox.y0 >= page.height * .9)
            and b.block_role not in {'revision_artifact', 'table_note', 'table_caption'}
            and 'UNVERIFIED_LAYOUT_FOOTER' not in b.semantic_reasons
            and 'ppstructure_short_annotation_repair' not in (b.source or '')]
    result = []
    for counter in counters:
        fragments = [counter, *counter_decorations(page, counter)]
        fragments.sort(key=lambda b: b.bbox.x0)
        result.append(counter.model_copy(update={
            'text': ''.join(b.text.strip() for b in fragments),
            'bbox': BBox(x0=min(b.bbox.x0 for b in fragments), y0=min(b.bbox.y0 for b in fragments),
                         x1=max(b.bbox.x1 for b in fragments), y1=max(b.bbox.y1 for b in fragments)),
        }))
    return result


def counter_decorations(page: Page, counter: TextBlock) -> list[TextBlock]:
    """A dash is part of a counter only on its baseline and next to its glyphs."""
    box = counter.bbox
    return [b for b in page.blocks if b is not counter and re.fullmatch(r'\s*[-—–_]\s*', b.text)
            and abs(b.bbox.y0-box.y0) <= 3 and abs(b.bbox.y1-box.y1) <= 3
            and min(abs(b.bbox.x1-box.x0), abs(b.bbox.x0-box.x1)) <= 6]


@dataclass(frozen=True)
class PageNumberMatch:
    original: TextBlock
    compare: TextBlock


def match_page_counters(original: Page | None, compare: Page | None) -> list[PageNumberMatch]:
    """Only unambiguous equal readings can explain ink; absence is not blankness."""
    left, right = page_counters(original), page_counters(compare)
    matches = []
    for block in left:
        key = page_number_key(block.text)
        peers = [b for b in right if page_number_key(b.text) == key]
        if len(peers) != 1 or sum(page_number_key(b.text) == key for b in left) != 1:
            continue
        pair = (block, peers[0])
        if any(b.confidence is None or b.confidence < .85 for b in pair):
            continue
        # Disputed physical readings must not become an erasure mask.
        if any(any(r.raw_text.strip() and page_number_key(r.raw_text) != key
                   for r in b.source_evidence) for b in pair):
            continue
        matches.append(PageNumberMatch(*pair))
    return matches


def _isolated_counter(page: Page, counter: TextBlock) -> bool:
    """A candidate below the body cannot be a table cell or an inline number."""
    if counter.block_type not in {'text', 'number', 'footer', 'page_footer'}:
        return False
    if counter.block_role not in {'text', 'unknown', 'page_footer'}:
        return False
    # Bare or negative numbers require stronger local classification; pairing
    # alone must never turn them into footers.
    text = re.sub(r"\s+", "", counter.text)
    if not (re.fullmatch(r'[-—–_]+\d+[-—–_]+', text) or '页' in text):
        return False
    box = counter.bbox
    for block in page.blocks:
        if block.block_id == counter.block_id:
            continue
        b = block.bbox
        if block.block_type in {'table', 'table_cell'} or block.block_role in {'table_note', 'table_caption'}:
            if min(box.x1, b.x1) > max(box.x0, b.x0) and min(box.y1, b.y1) > max(box.y0, b.y0):
                return False
        if (block.block_role in {'page_footer', 'page_header', 'revision_artifact'}
                or block.block_type in {'footer', 'page_footer', 'watermark', 'seal', 'seal_region', 'image', 'figure'}
                or not block.text.strip() or re.fullmatch(r'\s*[-—–_]\s*', block.text)):
            continue
        # Leave a full line-height gap after body text. Printed fields alongside
        # or below this candidate are evidence against footer ownership.
        if b.y1 >= box.y0 - max(8, box.y1 - box.y0):
            return False
    return True


def paired_counter_role_recoveries(original: Document, compare: Document):
    """Locate untyped counters using a trusted peer and independent text geometry.

    All proposals are computed before callers mutate roles, so recovered
    candidates cannot serve as new proof for another candidate in this pass.
    """
    from app.services.cover.alignment import aligned_cover_slot

    recoveries = []
    for side, document, peer in (('original', original, compare), ('compare', compare, original)):
        peer_pages = {page.page_no: page for page in peer.pages}
        for page in document.pages:
            opposite = peer_pages.get(page.page_no)
            if opposite is None:
                continue
            trusted = page_counters(opposite)
            own_trusted = {b.block_id for b in page_counters(page)}
            candidates = _counter_candidates(page, require_role=False)
            for candidate in candidates:
                if candidate.block_id in own_trusted or not _isolated_counter(page, candidate):
                    continue
                key = page_number_key(candidate.text)
                matches = [b for b in trusted if page_number_key(b.text) == key]
                if len(matches) != 1 or sum(page_number_key(b.text) == key for b in candidates) != 1:
                    continue
                reference = matches[0]
                if not _isolated_counter(opposite, reference):
                    continue
                if any(b.confidence is None or b.confidence < .9 for b in (candidate, reference)):
                    continue
                if any(any(e.raw_text.strip() and page_number_key(e.raw_text) != key
                           for e in b.source_evidence) for b in (candidate, reference)):
                    continue
                # Exclude all counters from the transform witnesses. Equal text
                # alone cannot establish that this number occupies the peer slot.
                anchors = [b for b in page.blocks if page_number_key(b.text) is None]
                peer_anchors = [b for b in opposite.blocks if page_number_key(b.text) is None]
                if not aligned_cover_slot(candidate.bbox, reference.bbox, candidate.text,
                                          anchors, peer_anchors, (page.width, page.height),
                                          (opposite.width, opposite.height)):
                    continue
                main = next(b for b in page.blocks if b.block_id == candidate.block_id)
                recoveries.extend((side, b, reference) for b in [main, *counter_decorations(page, main)]
                                  if b.block_role != 'page_footer')
    return recoveries
