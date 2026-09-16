"""Compare printed signing-form fields across table and text representations.

    Uses OCR character geometry, not an unordered bag of account numbers: each
    value remains bound to its field and party column.
"""
from __future__ import annotations

import re
import unicodedata

from dataclasses import dataclass

from app.models import BBox, CharBox, DiffItem, EvidenceBox, Page

PARTIES = re.compile(r"(委托人|承包人|甲方|乙方)\s*[:：]")
LABELS = re.compile(r"^(委托人|承包人|甲方|乙方|法定代表人或其委托代理人|地址|开户银行|账号|纳税人登记号|签字日期)[:：]?")
KEYS = {"委托人": "party", "甲方": "party", "承包人": "party", "乙方": "party",
        "地址": "address", "开户银行": "bank", "账号": "account", "纳税人登记号": "tax"}
ROLES = {"委托人": "甲方", "甲方": "甲方", "承包人": "乙方", "乙方": "乙方"}
FIELD_TITLES = {"party": "单位", "address": "地址", "bank": "开户银行", "account": "账号", "tax": "纳税人登记号"}


@dataclass
class PrintedField:
    value: str
    chars: list[CharBox]

    def evidence(self, page_no: int) -> EvidenceBox:
        return EvidenceBox(
            page_no=page_no,
            bbox=BBox(x0=min(c.bbox.x0 for c in self.chars), y0=min(c.bbox.y0 for c in self.chars),
                      x1=max(c.bbox.x1 for c in self.chars), y1=max(c.bbox.y1 for c in self.chars)),
            text=self.value, method="signing_printed_field_geometry", confidence=0.9,
        )


def printed_fields(page: Page) -> list[dict[str, str]] | None:
    form = printed_form(page)
    if form is None:
        return None
    return [{key: field.value for key, field in form[role].items()} for role in ("甲方", "乙方")]


def printed_form(page: Page) -> dict[str, dict[str, PrintedField]] | None:
    anchors = []
    chars = []
    margin = page.width
    for block in page.blocks:
        if block.block_role == "revision_artifact":
            margin = min(margin, block.bbox.x0)
            continue
        if block.block_type in {"seal", "seal_region", "header", "footer", "number"}:
            continue
        indexed = {c.text_index: c for c in block.char_boxes if c.text_index is not None}
        for match in re.finditer(r"(?:设置格式|删除|插入)\[[^\]]+\][:：]", block.text):
            char = indexed.get(match.start())
            if char and char.bbox.x0 >= page.width * 0.65:
                margin = min(margin, char.bbox.x0)
        for match in PARTIES.finditer(block.text):
            char = indexed.get(match.start())
            if char and char.bbox.y0 < page.height * 0.3:
                anchors.append((char.bbox.x0, char.bbox.y0))
        chars.extend(block.char_boxes)
    if not anchors:
        return None
    top = min(y for _, y in anchors)
    starts = []
    for x, y in sorted(anchors):
        if abs(y - top) <= 20 and not any(abs(x - old) < 20 for old in starts):
            starts.append(x)
    if len(starts) != 2 or starts[1] - starts[0] < page.width * 0.2:
        return None
    result = {}
    for low, high in ((starts[0] - 4, starts[1] - 2), (starts[1] - 2, margin - 2)):
        selected = []
        for c in chars:
            x, y = (c.bbox.x0 + c.bbox.x1) / 2, (c.bbox.y0 + c.bbox.y1) / 2
            if low <= x < high and top - 5 <= y < page.height * 0.92:
                selected.append(c)
        rows = []
        for char in sorted(selected, key=lambda c: ((c.bbox.y0 + c.bbox.y1) / 2, c.bbox.x0, c.text_index or 0)):
            cy = (char.bbox.y0 + char.bbox.y1) / 2
            if not rows or abs(cy - rows[-1][0]) > 5:
                rows.append((cy, []))
            rows[-1][1].append(char)
        fields = {}
        key = ""
        role = ""
        for _, row in rows:
            line = "".join(c.char for c in sorted(row, key=lambda c: (c.bbox.x0, c.text_index or 0)))
            line = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line))
            match = LABELS.match(line)
            if match:
                if match[1] in ROLES:
                    if role:
                        return None
                    role = ROLES[match[1]]
                key = KEYS.get(match[1], "")
                if key:
                    if key in fields:
                        return None
                    fields[key] = PrintedField(line[match.end():], list(row))
            elif key:
                fields[key].value += line
                fields[key].chars.extend(row)
        if (set(fields) != set(KEYS.values()) or not all(f.value for f in fields.values())
                or not role or role in result):
            return None
        result[role] = fields
    if set(result) != {"甲方", "乙方"}:
        return None
    return result


def equivalent_printed_fields(left: Page, right: Page) -> bool:
    before, after = printed_fields(left), printed_fields(right)
    return before is not None and after is not None and before == after


def reconcile_printed_form(diff: DiffItem, original: Page, compare: Page) -> list[DiffItem] | None:
    """Replace a missing *form* representation with party-bound field changes.

    None means identity was not established; [] means all observed fields match.
    Only consume a whole form candidate, never an unrelated table on its page.
    Missing or ambiguous fields retain the original candidate for review.
    """
    if diff.diff_type not in {"ADD", "DELETE"}:
        return None
    text = diff.original_text if diff.diff_type == "DELETE" else diff.compare_text
    if (not {"甲方", "乙方"}.issubset({ROLES[m[1]] for m in PARTIES.finditer(text)})
            or not all(label in text for label in ("地址", "开户银行", "账号", "纳税人登记号"))):
        return None
    before, after = printed_form(original), printed_form(compare)
    if before is None or after is None:
        return None
    changes = []
    for role in ("甲方", "乙方"):
        for key, label in FIELD_TITLES.items():
            left, right = before[role][key], after[role][key]
            if left.value == right.value:
                continue
            changes.append(DiffItem(
                diff_id=f"{diff.diff_id}-{role}-{key}", diff_type="MODIFY", source_type="table",
                title=f"{role} · {label}", original_text=left.value, compare_text=right.value,
                original_snippet=left.value, compare_snippet=right.value,
                original_evidence=[left.evidence(original.page_no)], compare_evidence=[right.evidence(compare.page_no)],
                match_method="signing_printed_field_identity", quality_status="NEEDS_REVIEW",
                match_score_details={"decision_reason": "CROSS_REPRESENTATION_FIELD_CHANGE",
                                     "party_role": role, "field_key": key, "reconciled_from": diff.diff_id,
                                     "page_alignment": diff.match_score_details.get("page_alignment", {})},
            ))
    return changes
