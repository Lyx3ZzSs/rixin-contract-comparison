from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ClauseNumber:
    raw_number: str
    canonical_number: str
    title: str
    level: int
    style: str
    marker_text: str
    weak: bool = False
    non_clause: bool = False
    risk_flags: tuple[str, ...] = ()


class ClauseNumberParser:
    """Parse contract numbering independently from heading confidence rules."""

    circled_digits = {
        "①": "1",
        "②": "2",
        "③": "3",
        "④": "4",
        "⑤": "5",
        "⑥": "6",
        "⑦": "7",
        "⑧": "8",
        "⑨": "9",
        "⑩": "10",
    }
    chinese_digit_values = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    chinese_unit_values = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    chinese_number_chars = "零〇一二两三四五六七八九十百千万"
    formal_pattern = re.compile(rf"^第(?P<number>[0-9{chinese_number_chars}]+)(?P<unit>[章节条])")
    chinese_list_pattern = re.compile(rf"^(?P<number>[{chinese_number_chars}]+)[、.．]")
    parenthesized_pattern = re.compile(rf"^[（(](?P<number>[0-9{chinese_number_chars}]+)[)）]")
    decimal_pattern = re.compile(r"^(?P<number>\d+(?:\.\d+)*)(?P<trailing>[.．、])?(?!\d)")
    circled_pattern = re.compile(r"^(?P<number>[①②③④⑤⑥⑦⑧⑨⑩])")
    quantity_or_amount_pattern = re.compile(
        r"^\s*\d+(?:\.\d+)?(?:[~～—-]\d+(?:\.\d+)?)?\s*"
        r"(万元|亿元|人民币|美元|usd|rmb|cny|元(?!器)|个工作日|工作日|自然日|"
        r"日内|个月|套|台|个|项|批|份|天|日|月|年|号|%)",
        re.IGNORECASE,
    )
    date_pattern = re.compile(r"^\s*(?:19|20)\d{2}(?:[./年-]\d{1,2}){1,2}日?\s*$")

    def parse_line(self, text: str) -> ClauseNumber | None:
        source_text = (text or "").strip()
        first_line = source_text.splitlines()[0] if source_text else ""
        if not first_line:
            return None
        raw = first_line.strip()
        circled_match = self.circled_pattern.match(raw)
        if circled_match:
            return self._from_match(source_text, circled_match, "circled")
        normalized = unicodedata.normalize("NFKC", raw).strip()
        normalized_source = unicodedata.normalize("NFKC", source_text).strip()
        for style, pattern in (
            ("formal", self.formal_pattern),
            ("chinese_list", self.chinese_list_pattern),
            ("parenthesized", self.parenthesized_pattern),
            ("decimal", self.decimal_pattern),
        ):
            match = pattern.match(normalized)
            if not match:
                continue
            return self._from_match(normalized_source, match, style)
        return None

    def normalize_number(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").strip()
        value = value.strip("、.． ")
        value = value.removeprefix("第")
        for suffix in ("章", "节", "条"):
            value = value.removesuffix(suffix)
        value = value.strip("（）()")
        if value in self.circled_digits:
            return self.circled_digits[value]
        if value and all(char in self.chinese_number_chars for char in value):
            converted = self.chinese_to_int(value)
            if converted is not None:
                return str(converted)
        return value.lower()

    def level_of(self, value: str, style: str = "") -> int:
        raw = unicodedata.normalize("NFKC", value or "").strip()
        normalized = self.normalize_number(raw)
        if re.fullmatch(r"\d+(?:\.\d+)+", normalized):
            return min(8, normalized.count(".") + 1)
        formal_match = self.formal_pattern.match(raw)
        if style == "formal" or formal_match is not None:
            unit = formal_match.group("unit") if formal_match is not None else ""
            return {"章": 1, "节": 2, "条": 3}.get(unit, 3)
        if style == "parenthesized" or re.fullmatch(r"[（(].+[)）]", raw):
            return 4
        if style == "circled":
            return 5
        return 1

    def is_formal_number(self, value: str) -> bool:
        return bool(self.formal_pattern.match(unicodedata.normalize("NFKC", value or "").strip()))

    def is_non_clause_numeric(self, text: str, parsed: ClauseNumber | None = None) -> bool:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", first_line))
        if not compact:
            return True
        if parsed is not None and parsed.style == "formal":
            return False
        if re.fullmatch(r"\d{1,3}", compact):
            return True
        if self.date_pattern.fullmatch(compact):
            return True
        if self.quantity_or_amount_pattern.match(first_line):
            return True
        if re.match(r"^\s*\d+\s*[~～—-]\s*\d+", first_line):
            return True
        if parsed is not None and re.fullmatch(r"\d{3,}(?:\.\d+)?", parsed.canonical_number):
            return True
        return False

    def chinese_to_int(self, value: str) -> int | None:
        if not value:
            return None
        if all(char in self.chinese_digit_values for char in value):
            digits = "".join(str(self.chinese_digit_values[char]) for char in value)
            return int(digits)

        total = 0
        section = 0
        number = 0
        for char in value:
            if char in self.chinese_digit_values:
                number = self.chinese_digit_values[char]
                continue
            unit = self.chinese_unit_values.get(char)
            if unit is None:
                return None
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        return total + section + number

    def _from_match(self, line: str, match: re.Match[str], style: str) -> ClauseNumber:
        marker_text = match.group(0)
        raw_value = marker_text.rstrip("、.．")
        if style == "formal":
            raw_value = marker_text
        elif style in {"chinese_list", "decimal"}:
            raw_value = match.group("number")
        elif style == "parenthesized":
            raw_value = marker_text
        elif style == "circled":
            raw_value = match.group("number")
        title = self._title_from_text(line[match.end() :].strip())
        if not title and "\n" in line:
            title = self._title_from_text(line.split("\n", 1)[1])
        canonical = self.normalize_number(raw_value)
        risks: list[str] = []
        weak = False
        if style == "decimal" and re.fullmatch(r"\d+", canonical):
            weak = True
            risks.append("WEAK_NUMERIC_MARKER")
        non_clause = self.is_non_clause_numeric(line, None if style == "formal" else None)
        return ClauseNumber(
            raw_number=raw_value,
            canonical_number=canonical,
            title=title,
            level=self.level_of(raw_value, style),
            style=style,
            marker_text=marker_text,
            weak=weak,
            non_clause=non_clause,
            risk_flags=tuple(dict.fromkeys(risks)),
        )

    @staticmethod
    def _title_from_text(text: str) -> str:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        first_line = re.sub(r"\s+", " ", first_line)
        return first_line[:40]
