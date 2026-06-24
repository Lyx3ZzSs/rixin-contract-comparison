from __future__ import annotations

from dataclasses import dataclass
import re
from difflib import SequenceMatcher
import unicodedata


@dataclass(frozen=True)
class DeliveryDatePlaceholder:
    year: str
    placeholder: str


def compact_text_for_repair(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s，。；：、“”‘’（）()\[\]【】《》,.!?:;\"']+", "", text)


def is_delivery_date_placeholder_repair(structure_text: str, ocr_text: str) -> bool:
    structure = _parse_delivery_date_placeholder(structure_text, allow_day_confusions=False)
    if structure is None or structure.placeholder != "月日":
        return False
    ocr = _parse_delivery_date_placeholder(ocr_text, allow_day_confusions=True)
    if ocr is None:
        return False
    return (
        structure.year == ocr.year
        and ocr.placeholder in {"月日", "日"}
        and compact_text_for_repair(structure_text) != compact_text_for_repair(ocr_text)
    )


def is_clause_marker_ocr_repair(structure_text: str, ocr_text: str) -> bool:
    structure = _parse_clause_marker_line(structure_text, expected_prefix="十")
    if structure is None:
        return _is_embedded_clause_marker_ocr_repair(structure_text, ocr_text)
    return _is_leading_clause_marker_ocr_repair(
        structure,
        ocr_text,
    ) or _is_embedded_clause_marker_ocr_repair(structure_text, ocr_text)


def _is_leading_clause_marker_ocr_repair(structure: tuple[str, str], ocr_text: str) -> bool:
    ocr = _parse_clause_marker_line(ocr_text, expected_prefix="土")
    if ocr is None:
        return False
    structure_marker, structure_body = structure
    ocr_marker, ocr_body = ocr
    if "十" + ocr_marker[1:] != structure_marker:
        return False
    return _body_texts_match_for_clause_marker_repair(structure_body, ocr_body)


def _is_embedded_clause_marker_ocr_repair(structure_text: str, ocr_text: str) -> bool:
    canonical_ocr_text = _canonicalize_embedded_tu_clause_markers(ocr_text)
    if canonical_ocr_text is None:
        return False
    structure_norm = compact_text_for_repair(structure_text)
    ocr_norm = compact_text_for_repair(ocr_text)
    canonical_ocr_norm = compact_text_for_repair(canonical_ocr_text)
    if not structure_norm or not canonical_ocr_norm or structure_norm == ocr_norm:
        return False
    if min(len(structure_norm), len(canonical_ocr_norm)) < 16:
        return False
    if structure_norm == canonical_ocr_norm:
        return True
    return SequenceMatcher(None, structure_norm, canonical_ocr_norm).ratio() >= 0.98


def _body_texts_match_for_clause_marker_repair(structure_body: str, ocr_body: str) -> bool:
    structure_norm = compact_text_for_repair(structure_body)
    ocr_norm = compact_text_for_repair(ocr_body)
    if min(len(structure_norm), len(ocr_norm)) < 8:
        return False
    if structure_norm == ocr_norm:
        return True
    return SequenceMatcher(None, structure_norm, ocr_norm).ratio() >= 0.96


def _canonicalize_embedded_tu_clause_markers(text: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", text or "")
    pattern = re.compile(
        r"(?P<prefix>^|[\n\r。；;])(?P<space>\s*)土(?P<suffix>[一二三四五六七八九])(?P<punct>[、,，:：.．])(?P<body>[^\n\r]*)"
    )
    matches = list(pattern.finditer(normalized))
    if not matches:
        return None
    for match in matches:
        if len(compact_text_for_repair(match.group("body"))) < 8:
            return None
    return pattern.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('space')}"
            f"十{match.group('suffix')}{match.group('punct')}{match.group('body')}"
        ),
        normalized,
    )


def _parse_clause_marker_line(text: str, *, expected_prefix: str) -> tuple[str, str] | None:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    match = re.match(
        rf"^[#\s]*(?P<marker>{expected_prefix}[一二三四五六七八九]?)[、,，:：.．](?P<body>.+)$",
        normalized,
        flags=re.S,
    )
    if match is None:
        return None
    body = match.group("body").strip()
    if not compact_text_for_repair(body):
        return None
    return match.group("marker"), body


def _parse_delivery_date_placeholder(text: str, *, allow_day_confusions: bool) -> DeliveryDatePlaceholder | None:
    compact = compact_text_for_repair(text)
    match = re.search(r"交货日期(?P<year>\d{4})年(?P<placeholder>.*?)交货", compact)
    if match is None:
        return None
    placeholder = _canonical_delivery_date_placeholder(
        match.group("placeholder"),
        allow_day_confusions=allow_day_confusions,
    )
    if placeholder is None:
        return None
    return DeliveryDatePlaceholder(year=match.group("year"), placeholder=placeholder)


def _canonical_delivery_date_placeholder(text: str, *, allow_day_confusions: bool) -> str | None:
    if not text:
        return None
    char_map = {
        "月": "月",
        "日": "日",
    }
    if allow_day_confusions:
        char_map.update(
            {
                "且": "日",
                "目": "日",
                "曰": "日",
                "口": "日",
            }
        )
    canonical: list[str] = []
    for char in text:
        mapped = char_map.get(char)
        if mapped is None:
            return None
        canonical.append(mapped)
    return "".join(canonical)
