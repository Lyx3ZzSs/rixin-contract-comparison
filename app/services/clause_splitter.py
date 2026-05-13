from __future__ import annotations

import re

from app.models import Clause, Document, EvidenceBox
from app.services.normalizer import TextNormalizer


class ClauseSplitter:
    clause_start_pattern = re.compile(
        r"^\s*((第[一二三四五六七八九十百千万0-9]+[章节条])|([一二三四五六七八九十]+、)|(（[一二三四五六七八九十0-9]+）)|(\d+(?:\.\d+){0,3}[\.、]?))\s*(.*)$"
    )

    def __init__(self, text_normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = text_normalizer or TextNormalizer()

    def split(self, document: Document, prefix: str) -> list[Clause]:
        units = []
        for page in document.pages:
            for block in page.blocks:
                normalized_block = self.normalizer.normalize(block.text)
                if not normalized_block:
                    continue
                pieces = self._split_block_lines(normalized_block)
                for piece in pieces:
                    units.append(
                        {
                            "text": piece,
                            "page_no": block.page_no,
                            "block_id": block.block_id,
                            "evidence": EvidenceBox(
                                page_no=block.page_no,
                                bbox=block.bbox,
                                method="block_fallback",
                                text=piece[:300],
                            ),
                        }
                    )

        if not units:
            return []

        clauses: list[dict] = []
        current: dict | None = None
        saw_marker = False
        for unit in units:
            marker = self._parse_marker(unit["text"])
            starts_clause = marker is not None
            if starts_clause:
                saw_marker = True
            if starts_clause or current is None:
                if current is not None:
                    clauses.append(current)
                clause_no, title = marker if marker else ("", "")
                current = {
                    "clause_no": clause_no,
                    "title": title,
                    "texts": [unit["text"]],
                    "page_numbers": [unit["page_no"]],
                    "bboxes": [unit["evidence"]],
                    "source_block_ids": [unit["block_id"]],
                }
            else:
                current["texts"].append(unit["text"])
                current["page_numbers"].append(unit["page_no"])
                current["bboxes"].append(unit["evidence"])
                current["source_block_ids"].append(unit["block_id"])
        if current is not None:
            clauses.append(current)

        if not saw_marker:
            clauses = [
                {
                    "clause_no": "",
                    "title": self._title_from_text(unit["text"]),
                    "texts": [unit["text"]],
                    "page_numbers": [unit["page_no"]],
                    "bboxes": [unit["evidence"]],
                    "source_block_ids": [unit["block_id"]],
                }
                for unit in units
            ]

        result: list[Clause] = []
        for index, item in enumerate(clauses, start=1):
            text = "\n".join(item["texts"]).strip()
            result.append(
                Clause(
                    clause_id=f"{prefix}C{index:03d}",
                    clause_no=item["clause_no"],
                    title=item["title"] or self._title_from_text(text),
                    text=text,
                    normalized_text=self.normalizer.normalize_for_match(text),
                    page_numbers=sorted(set(item["page_numbers"])),
                    bboxes=item["bboxes"],
                    source_block_ids=list(dict.fromkeys(item["source_block_ids"])),
                )
            )
        return result

    def _split_block_lines(self, text: str) -> list[str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 1:
            return [text.strip()]
        pieces: list[str] = []
        current: list[str] = []
        for line in lines:
            if self._parse_marker(line) and current:
                pieces.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            pieces.append("\n".join(current))
        return pieces

    def _parse_marker(self, text: str) -> tuple[str, str] | None:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        match = self.clause_start_pattern.match(first_line)
        if not match:
            return None
        clause_no = match.group(1).rstrip("、.")
        rest = match.group(6).strip() if match.lastindex and match.lastindex >= 6 else ""
        title = self._title_from_text(rest)
        return clause_no, title

    def _title_from_text(self, text: str) -> str:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        first_line = re.sub(r"\s+", " ", first_line)
        return first_line[:40]
