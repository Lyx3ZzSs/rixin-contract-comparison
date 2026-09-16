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
    numbers = lambda value: Counter(re.findall(
        r"\d{2,}(?:\.\d+)?|\d(?:\.\d+)?(?=元|年|月|日|天|小时|%|个|套|台|份|万|千|米|V|A|W|Hz)", value))
    if numbers(primary) != numbers(reading):
        return None
    financial = lambda value: [v.replace("任", "仟") for v in re.findall(r"[零壹贰叁肆伍陆柒捌玖拾佰仟任万亿]+元整", value)]
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
