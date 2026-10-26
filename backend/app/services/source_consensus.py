"""Reconcile line OCR against independently located paragraph readings.

This never copies text from the other document. Both sides must have their own
complete paragraph evidence; missing sources and numeric conflicts fail closed.
"""
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from app.models import DiffItem, Document


def canonical(text: str) -> str:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    # OCR of printed amount underline, not a general punctuation ignore rule.
    return re.sub(r"_+(?=[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+元整)", "", text)


def paragraph_reading(text: str, document: Document, pages: set[int]) -> str | None:
    primary = canonical(text)
    sources = {}
    coverage = 0
    for page in document.pages:
        if page.page_no not in pages:
            continue
        for block in page.blocks:
            value = canonical(block.text)
            if not value or value not in primary or block.raw_html:
                continue
            readings = [e for e in block.source_evidence if e.source == "ppstructure"
                        and e.page_no == page.page_no and "<" not in e.raw_text
                        and e.bbox is not None]
            if len(readings) != 1:
                continue
            source = readings[0]
            # The layout source must actually contain this line geometrically.
            box = source.bbox
            if not (box.x0 - 8 <= block.bbox.x0 <= box.x1 and box.y0 - 8 <= block.bbox.y0 <= box.y1):
                continue
            key = (page.page_no, box.y0, box.x0, source.raw_text)
            sources[key] = canonical(source.raw_text)
            coverage += len(value)
    if coverage < len(primary) * 0.90 or not sources:
        return None
    reading = "".join(sources[key] for key in sorted(sources))
    if SequenceMatcher(None, primary, reading, autojunk=False).ratio() < 0.90:
        return None
    # Do not erase a different account, date, price or technical parameter just
    # because the layout engine disagrees with its line recognizer.
    def numbers(value):
        return Counter(re.findall(
        r"\d{2,}(?:\.\d+)?|\d(?:\.\d+)?(?=元|年|月|日|天|小时|%|个|套|台|份|万|千|米|V|A|W|Hz)", value))
    if numbers(primary) != numbers(reading):
        return None
    def financial(value):
        return [v.replace("任", "仟") for v in re.findall(r"[零壹贰叁肆伍陆柒捌玖拾佰仟任万亿]+元整", value)]
    if financial(primary) != financial(reading):
        return None
    return reading


def confirmed_paragraph_equivalence(diff: DiffItem, original: Document | None, compare: Document | None) -> bool:
    if diff.source_type != "clause" or diff.diff_type != "MODIFY" or original is None or compare is None:
        return False
    if min(len(diff.original_text), len(diff.compare_text)) < 25:
        return False
    before = paragraph_reading(diff.original_text, original, {e.page_no for e in diff.original_evidence})
    after = paragraph_reading(diff.compare_text, compare, {e.page_no for e in diff.compare_evidence})
    return before is not None and before == after


def unchanged_page_start_attached_to_clause(diff: DiffItem, original: Document | None, compare: Document | None) -> bool:
    """Prove a short trailing page-start block exists at the same place on both sides."""
    if diff.source_type != "clause" or diff.diff_type != "MODIFY" or original is None or compare is None:
        return False
    for before, after, left, right in ((diff.original_text, diff.compare_text, original, compare),
                                        (diff.compare_text, diff.original_text, compare, original)):
        a, b = canonical(before), canonical(after)
        if not a or not b.startswith(a):
            continue
        tail = b[len(a):]
        if not 2 <= len(tail) <= 40:
            continue
        candidates = [(page, block) for page in right.pages for block in page.blocks
                      if canonical(block.text) == tail and block.bbox.y1 < page.height * .2]
        if len(candidates) != 1:
            continue
        page, block = candidates[0]
        peers = [(p, x) for p in left.pages for x in p.blocks if p.page_no == page.page_no
                 and canonical(x.text) == tail and x.bbox.y1 < p.height * .2]
        if len(peers) == 1:
            p, x = peers[0]
            if (abs(x.bbox.x0 / p.width - block.bbox.x0 / page.width) < .05
                    and abs(x.bbox.y0 / p.height - block.bbox.y0 / page.height) < .05):
                return True
    return False


def located_blank_form_equivalence(diff: DiffItem, original: Document | None, compare: Document | None) -> bool:
    """A split optional empty field needs a local counterpart, not global text containment."""
    if original is None or compare is None or diff.source_type != 'clause' or diff.diff_type not in {'ADD','DELETE'}:
        return False
    side = 'compare' if diff.diff_type == 'ADD' else 'original'
    own, other = (compare, original) if side == 'compare' else (original, compare)
    text = canonical(getattr(diff, side+'_text'))
    m = re.fullmatch(r'(\([一二三四五六七八九十]+\)[\u4e00-\u9fff]{2,12}:)[/。oO_]*', text)
    if not m:
        return False
    pages = {e.page_no for e in getattr(diff,side+'_evidence')}
    for page in own.pages:
        if page.page_no not in pages:
            continue
        labels = [b for b in page.blocks if canonical(b.text).startswith(m[1])]
        peers = [p for p in other.pages if p.page_no == page.page_no]
        if len(peers) != 1:
            continue
        for label in labels:
            expected = [b for b in peers[0].blocks
                        if re.fullmatch(re.escape(m[1])+r'[/_。]+', canonical(b.text))
                        and abs(b.bbox.y0/peers[0].height-label.bbox.y0/page.height)<.025]
            if len(expected)!=1:
                continue
            if text.endswith(('o','O')):
                dots = [b for b in page.blocks if b.text.strip() in {'o','O'}
                        and label.bbox.x1 < b.bbox.x0 and abs(b.bbox.y1-label.bbox.y1)<5
                        and b.bbox.y1-b.bbox.y0 <= (label.bbox.y1-label.bbox.y0)*.4
                        and b.bbox.x1-b.bbox.x0 <= 6]
                if len(dots)!=1:
                    continue
            return True
    return False
