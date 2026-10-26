"""Conservative colon heading repair using native structure, never baseline labels."""

import re
import unicodedata
from functools import lru_cache


def key(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


@lru_cache(maxsize=64)
def native_pages(path):
    import fitz
    from app.services.native_heading_repair import _native_line_records

    with fitz.open(path) as pdf:
        return [_native_line_records(page, i + 1) for i, page in enumerate(pdf)]


def colon_evidence(lines, index):
    from app.services.clause_splitter import ClauseSplitter
    from app.services.native_heading_repair import _is_valid_native_title

    parse = ClauseSplitter()._parse_marker
    line = lines[index]
    text = key(line.text)
    marker = parse(text)

    def fail(why):
        return dict(accepted=False, reason=why)

    if not text.endswith(":") or not marker or not re.fullmatch(r"\d{1,2}(?:\.\d{1,2})*", marker[0]):
        return fail("not_numbered_colon_candidate")
    number, title = marker
    title = title[:-1]
    if not _is_valid_native_title(title) or re.search(r"[，,、。;；:：()（）]", title):
        return fail("not_short_standalone_native_title")
    height = line.bbox.y1 - line.bbox.y0
    if any(
        other is not line and min(other.bbox.y1, line.bbox.y1) - max(other.bbox.y0, line.bbox.y0) > 0.5 * height
        for other in lines
    ):
        return fail("same_baseline_continuation")
    following = [r for r in lines[index + 1 :] if r.bbox.y0 > line.bbox.y0 + height / 2]
    if not following:
        return fail("no_following_body_evidence")
    nxt = following[0]
    nextmarker = parse(nxt.text)
    style = max(line.span_styles, key=lambda s: s.char_count, default=None)
    nextstyle = max(nxt.span_styles, key=lambda s: s.char_count, default=None)
    if not style or not nextstyle or style.size < nextstyle.size - 0.5:
        return fail("native_style_below_body")
    if abs(line.bbox.x0 - nxt.bbox.x0) > 3 * style.size or nxt.bbox.y0 - line.bbox.y1 > 4 * style.size:
        return fail("body_not_locally_contiguous")
    # Hierarchy must use decimal children, not an arbitrary colon-introduced list.
    child = bool(nextmarker and nextmarker[0].startswith(number + "."))
    previous = []
    for r in lines[:index]:
        m = parse(r.text)
        if m and re.fullmatch(r"\d{1,2}(?:\.\d{1,2})*", m[0]):
            previous.append((r, m))
    parts = number.split(".")
    sibling = next(
        (
            (r, m)
            for r, m in reversed(previous)
            if m[0].split(".")[:-1] == parts[:-1]
            and int(m[0].split(".")[-1]) + 1 == int(parts[-1])
            and abs(r.bbox.x0 - line.bbox.x0) < 2 * style.size
        ),
        None,
    )
    # An unnumbered paragraph must continue across lines. This rejects standalone
    # party/amount fields and short list introductions without inventing title words.
    from app.services.cover.patterns import parse_labeled_line

    labeled_body = bool(parse_labeled_line(nxt.text)) or bool(re.match(r"^.{1,12}[:：]", nxt.text.strip()))
    continuous = not labeled_body and not nextmarker and len(key(nxt.text)) > 24 and not key(nxt.text).endswith(":")
    if not child and not (sibling and continuous):
        return fail("no_decimal_child_or_sibling_with_continuous_body")
    return dict(
        accepted=True,
        reason="native_numbered_colon+standalone+body_style+local_hierarchy",
        number=number,
        hierarchy="decimal_child" if child else "preceding_sibling_and_continuous_body",
        following_text=nxt.text,
        preceding_sibling=sibling[1][0] if sibling else None,
        native_style=dict(font=style.font, size=style.size, flags=style.flags),
        indent_delta=nxt.bbox.x0 - line.bbox.x0,
        error_class="LAYOUT_RECALL_ERROR",
    )


def apply_colon_repair(artifacts, pdf_path):
    from app.services.extractors.contract_v3.geometry import overlap, area

    pages = native_pages(str(pdf_path))
    for page in artifacts:
        lines = pages[page["page_no"] - 1]
        for b in page["blocks"]:
            if (
                b["raw_layout_type"] not in {None, "text"}
                or not b.get("native_char_indices")
                or b.get("native_repair", {}).get("accepted")
            ):
                continue
            if not key(b["text"]).endswith(":"):
                continue
            matches = [
                (i, r)
                for i, r in enumerate(lines)
                if key(r.text) == key(b["text"])
                and overlap(b["bbox"], [r.bbox.x0, r.bbox.y0, r.bbox.x1, r.bbox.y1]) / area(b["bbox"]) > 0.5
            ]
            if len(matches) != 1:
                continue
            evidence = colon_evidence(lines, matches[0][0])
            b["colon_heading_evidence"] = evidence
            if evidence["accepted"]:
                b.update(
                    resolved_block_type="paragraph_title",
                    confidence=0.9,
                    reason="NativeHeadingRepair.numbered_colon_native_context",
                    native_repair=evidence,
                )
