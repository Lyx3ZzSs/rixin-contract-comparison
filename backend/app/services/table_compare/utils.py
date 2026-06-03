"""Text normalization and utility functions for table comparison.

These are standalone functions that do not require class state.
"""

from __future__ import annotations

import re
import unicodedata

from app.services.table_compare.constants import (
    AMOUNT_TOKEN_PATTERN,
    NOISE_PATTERN,
    SUMMARY_LABEL_PATTERN,
    TABLE_HEADERS,
    TABLE_TYPE_LABELS,
)


_TABLE_OCR_CHAR_REPLACEMENTS: dict[str, str] = {
    "香": "否",
    "己": "已",
    "曰": "日",
    "未": "末",
    "戊": "戌",
}
_TABLE_OCR_REGEX_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^([0-9])號$"), r"\1"),
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    for wrong, correct in _TABLE_OCR_CHAR_REPLACEMENTS.items():
        text = text.replace(wrong, correct)
    for pattern, repl in _TABLE_OCR_REGEX_REPLACEMENTS:
        text = pattern.sub(repl, text)
    text = text.lower()
    text = re.sub(r"[\s\n\r\t]+", "", text)
    text = text.replace("：", ":").replace("；", ";").replace("，", ",").replace("。", ".")
    text = re.sub(r"[()（）]", "", text)
    return text.strip()


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalize_cell_for_compare(text: str) -> str:
    date = canonical_date(text)
    if date:
        return f"date:{date}"
    percent = canonical_percent(text)
    if percent:
        return f"percent:{percent}"
    quantity = canonical_quantity(text)
    if quantity:
        return f"quantity:{quantity}"
    amount = canonical_amount(text)
    if amount:
        return f"amount:{amount}"
    return normalize(text)


def canonical_amount(text: str) -> str:
    raw = unicodedata.normalize("NFKC", text or "")
    compact = re.sub(r"\s+", "", raw)
    if re.search(r"[年月日]", compact):
        return ""
    if not re.fullmatch(r"(?:人民币|¥|￥)?[+-]?\d[\d,]*(?:\.\d+)?(?:万)?元?", compact):
        return ""
    match = re.fullmatch(r"(?:人民币|¥|￥)?([+-]?\d[\d,]*(?:\.\d+)?)(万)?元?", compact)
    if not match:
        return ""
    value = float(match.group(1).replace(",", ""))
    if match.group(2) or "万元" in compact:
        value *= 10000
    return f"{value:.2f}"


def canonical_date(text: str) -> str:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", compact)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


def canonical_percent(text: str) -> str:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(%|％|百分之)", compact)
    if match:
        return f"{float(match.group(1)):.4f}"
    return ""


def canonical_quantity(text: str) -> str:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(套|台|个|项|批|份|件|人天|天|月|年)", compact)
    if match:
        value = float(match.group(1))
        return f"{value:.4f}{match.group(2)}"
    return ""


def is_amount_like(text: str) -> bool:
    return bool(canonical_amount(text) or re.fullmatch(r"[\d,.]+", text or ""))


def looks_like_person_or_role(text: str) -> bool:
    return bool(re.search(r"(联系人|负责人|经理|电话|手机|邮箱|@)", text or "") or len(text or "") <= 8)


def is_number_like(text: str) -> bool:
    return bool(re.fullmatch(r"[\d,.]+", text or ""))


def is_noise(text: str) -> bool:
    return bool(NOISE_PATTERN.fullmatch(text)) or text in TABLE_HEADERS


def punctuation_fold(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def joint_table_type(original, compare) -> str:
    types = [table_type(table) for table in (original, compare) if table is not None]
    if "product" in types:
        return "product"
    if "payment" in types:
        return "payment"
    if "cover" in types:
        return "cover"
    return types[0] if types else "generic"


def table_type(table) -> str:
    text = normalize(table.all_cell_text()) if table is not None else ""
    if not text:
        return "generic"
    if any(token in text for token in ("产品名称", "详细配置", "单价", "金额", "标的物")):
        return "product"
    if any(token in text for token in ("付款", "支付", "付款节点", "付款条件", "进度款", "验收款")):
        return "payment"
    if any(token in text for token in ("验收", "标准", "指标", "测试")):
        return "acceptance"
    if any(token in text for token in ("联系人", "电话", "邮箱", "通讯地址")):
        return "contact"
    if any(token in text for token in ("甲方", "乙方", "签订日期", "合同编号", "签订地点")):
        return "cover"
    return "generic"


def table_type_label(table_type_str: str) -> str:
    return TABLE_TYPE_LABELS.get(table_type_str, TABLE_TYPE_LABELS["generic"])


def loose_literal_pattern(text: str) -> str:
    return r"\s*".join(re.escape(char) for char in text)


def looks_like_amount_value(norm: str) -> bool:
    if not norm.startswith("amount:"):
        return False
    try:
        return abs(float(norm.split(":", 1)[1])) >= 100.0
    except ValueError:
        return False


def is_loose_subsequence_present(token: str, source: str) -> bool:
    if len(token) < 4 or not re.search(r"[一-鿿]", token):
        return False
    max_span = max(80, len(token) * 8)
    first_char = token[0]
    start = source.find(first_char)
    while start >= 0:
        pos = start + 1
        matched = True
        last = start
        for char in token[1:]:
            index = source.find(char, pos)
            if index < 0 or index - start > max_span:
                matched = False
                break
            last = index
            pos = index + 1
        if matched and last - start <= max_span:
            return True
        start = source.find(first_char, start + 1)
    return False


def anchor_cell(table, row: int, col: int):
    cell = table.get_cell(row, col)
    if not cell or cell.row_index != row or cell.col_index != col:
        return None
    return cell


# ---------------------------------------------------------------------------
# Continuation marker detection (inspired by MinerU table_continuation.py)
# ---------------------------------------------------------------------------

_CONTINUATION_END_MARKERS: tuple[str, ...] = (
    "(续)", "（续）", "(续表)", "（续表）", "(续上表)", "（续上表）",
    "(continued)", "(cont.)", "(cont'd)", "(…continued)", "continued",
    "续表",
)
_CONTINUATION_INLINE_MARKERS: tuple[str, ...] = ("(continued)",)


def _full_to_half(text: str) -> str:
    result: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(" ")
        else:
            result.append(ch)
    return "".join(result)


def is_continuation_text(text: str) -> bool:
    """Detect continuation markers like '(续)', '(续表)', '(continued)'."""
    normalized = _full_to_half((text or "").strip()).lower()
    if not normalized:
        return False

    for marker in _CONTINUATION_END_MARKERS:
        marker_lower = marker.lower()
        if not normalized.endswith(marker_lower):
            continue
        if marker_lower == "continued":
            start = len(normalized) - len(marker_lower)
            if start > 0 and normalized[start - 1].isalpha():
                continue
        return True

    for marker in _CONTINUATION_INLINE_MARKERS:
        if marker.lower() in normalized:
            return True

    return False


def build_row_signature(cells: list):
    """Build a RowSignature from a list of _LogicalCell or TableCell."""
    from app.services.table_compare.types import RowSignature

    col_count = sum(getattr(c, "colspan", 1) for c in cells)
    colspans = tuple(getattr(c, "colspan", 1) for c in cells)
    rowspans = tuple(getattr(c, "rowspan", 1) for c in cells)
    texts = tuple(normalize(getattr(c, "text", "")) for c in cells)
    return RowSignature(col_count=col_count, colspans=colspans, rowspans=rowspans, texts=texts)


def first_unit_token(tokens: list[str]) -> str:
    units = {"套", "台", "个", "项", "批", "份", "件", "年", "月", "天", "人天"}
    for token in tokens:
        if unicodedata.normalize("NFKC", token or "").strip() in units:
            return token
    return ""


def summary_labels(text: str) -> list[str]:
    compact = normalize(text)
    if not compact:
        return []
    return [match.group(0) for match in SUMMARY_LABEL_PATTERN.finditer(compact)]


def summary_amounts(text: str, labels: list[str] | None = None) -> list[str]:
    raw = unicodedata.normalize("NFKC", text or "")
    for label in (summary_labels(raw) if labels is None else labels):
        raw = re.sub(loose_literal_pattern(label), " ", raw)

    amounts: list[str] = []
    for match in AMOUNT_TOKEN_PATTERN.finditer(raw):
        token = match.group(0)
        if canonical_amount(token):
            amounts.append(token)
    return amounts


def summary_text_has_only_labels_and_amounts(text: str, labels: list[str]) -> bool:
    raw = unicodedata.normalize("NFKC", text or "")
    for label in labels:
        raw = re.sub(loose_literal_pattern(label), " ", raw)
    raw = AMOUNT_TOKEN_PATTERN.sub(" ", raw)
    residual = re.sub(r"[\s,，、;；:.。|/\\\-]+", "", raw)
    return not residual


def summary_labels_from_summary_text(text: str) -> list[str]:
    labels = summary_labels(text)
    if not labels:
        return []
    if not summary_text_has_only_labels_and_amounts(text, labels):
        return []
    return labels


def summary_amount_line_amounts(text: str) -> list[str]:
    amounts = summary_amounts(text, labels=[])
    if not amounts:
        return []
    raw = unicodedata.normalize("NFKC", text or "")
    raw = AMOUNT_TOKEN_PATTERN.sub(" ", raw)
    residual = re.sub(r"[\s,，、;；:.。|/\\\-]+", "", raw)
    return amounts if not residual else []
