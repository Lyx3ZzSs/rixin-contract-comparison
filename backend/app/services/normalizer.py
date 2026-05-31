from __future__ import annotations

import re
import unicodedata


class TextNormalizer:
    page_number_pattern = re.compile(r"^\s*((第\s*)?\d+\s*(页)?|共\s*\d+\s*页\s*第\s*\d+\s*页)\s*$")

    _LIGATURES = {
        "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff",
        "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
    }
    _LIGATURE_RE = re.compile("|".join(re.escape(k) for k in _LIGATURES))

    def normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "")
        text = self._LIGATURE_RE.sub(lambda m: self._LIGATURES[m.group()], text)
        text = text.replace("\r", "\n")
        lines = [self._clean_line(line) for line in text.splitlines()]
        lines = [line for line in lines if line and not self.page_number_pattern.match(line)]
        text = "\n".join(lines)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def normalize_for_match(self, text: str) -> str:
        text = self.normalize(text)
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[，。；：、“”‘’（）()\[\]【】《》,.!?:;\"']", "", text)
        return text.lower()

    def _clean_line(self, line: str) -> str:
        line = unicodedata.normalize("NFKC", line)
        line = re.sub(r"\s+", " ", line).strip()
        line = re.sub(r"^(合同编号|编号)[:：]\s*\S+$", "", line)
        return line


normalizer = TextNormalizer()
