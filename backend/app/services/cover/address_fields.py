"""Own labeled preamble addresses by a unique same-page party heading."""
import re
from collections import defaultdict

from .constants import FIELD_LABELS
from .evidence import build_evidence
from .patterns import parse_labeled_line, value_parts_from_line
from .types import CoverField


def extract_party_addresses(blocks):
    candidates = defaultdict(list)
    headings = []
    for block in blocks:
        parsed = parse_labeled_line(block.text)
        if parsed and parsed[0] in {"buyer", "seller"} and parsed[1]:
            headings.append((block, parsed[0]))
    for block in blocks:
        match = re.fullmatch(r"\s*(?:通讯地址|通信地址|联系地址|地址)\s*[:：]\s*([^\n:：]+)", block.text)
        if not match or block.block_type != "text":
            continue
        # Do not move an explicit participant section away from its body owner.
        if any(b.page_no == block.page_no and b.text.strip() == "签约主体" for b in blocks):
            continue
        preceding = [(h, role) for h, role in headings if h.page_no == block.page_no
                     and 0 <= block.bbox.y0-h.bbox.y1 <= 160
                     and abs(h.bbox.x0-block.bbox.x0) <= 30]
        if not preceding:
            continue
        y = max(h.bbox.y0 for h, _ in preceding)
        nearest = [(h, role) for h, role in preceding if abs(h.bbox.y0-y) < 3]
        if len(nearest) != 1:
            continue
        key = nearest[0][1] + "_address"
        value = match[1].strip()
        if value:
            candidates[(block.page_no, key)].append((block, value))
    fields, consumed = {}, set()
    for (page, key), entries in candidates.items():
        if len(entries) != 1:
            continue
        block, value = entries[0]
        fields.setdefault(page, {})[key] = CoverField(key=key, label=FIELD_LABELS[key], value=value,
            evidences=[build_evidence(block, block.text)], value_parts=value_parts_from_line(block, value))
        consumed.add(block.block_id)
    return fields, consumed
