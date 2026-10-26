"""Join physical continuation cells before comparing their complete contents."""
import re
import unicodedata

from app.services.table_compare.types import CellSourceFragment


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def join_continuations(tables, others, pairs, pages=None):
    """Return consumed tail indices; identity uses labels and column geometry.

    A blank label cell with the same physical columns on the next page is a
    continuation candidate. The paired form must carry the same owner label.
    Values are deliberately not required to be equal across the documents.
    """
    used, diagnostics = set(), []
    paired = {a for a, _ in pairs}
    for index, tail in enumerate(tables):
        if index in paired or len(tail.rows) != 1:
            continue
        cells = tail.rows[0].cells
        if len(cells) != 2 or compact(cells[0].text) or not compact(cells[1].text):
            continue
        candidates = []
        for owner_index, other_index in pairs:
            owner, other = tables[owner_index], others[other_index]
            if not owner.rows or not other.rows or owner.rows[-1].page_no + 1 != tail.rows[0].page_no:
                continue
            row, counterpart = owner.rows[-1], other.rows[-1]
            if len(row.cells) != 2 or len(counterpart.cells) != 2:
                continue
            if not compact(row.cells[0].text) or compact(row.cells[0].text) != compact(counterpart.cells[0].text):
                continue
            if not all(a.bbox and b.bbox and max(abs(a.bbox.x0-b.bbox.x0), abs(a.bbox.x1-b.bbox.x1)) <= 8
                       for a, b in zip(row.cells, cells)):
                continue
            # A new independent form starts with a label. An empty first cell
            # continuing the same columns must also start higher on its page.
            if cells[1].bbox.y0 >= row.cells[1].bbox.y0:
                continue
            if pages is not None:
                owner_page, tail_page = pages.get(row.page_no), pages.get(tail.rows[0].page_no)
                if owner_page is None or tail_page is None:
                    continue
                if row.cells[1].bbox.y1 < owner_page.height * .8 or cells[1].bbox.y0 > tail_page.height * .25:
                    continue
                if any(b.block_type in {'title', 'doc_title', 'paragraph_title', 'figure_title'}
                       and b.bbox.y1 < cells[1].bbox.y0 and compact(b.text) for b in tail_page.blocks):
                    continue
            candidates.append(row)
        if len(candidates) != 1:
            continue
        row = candidates[0]
        cell = row.cells[1]
        if len(cell.source_fragments) == 1:
            cell.source_fragments = [CellSourceFragment(cell.text, cell.page_no, cell.bbox, cell.source_block_id)]
        cell.text += cells[1].text
        cell.source_fragments.append(CellSourceFragment(cells[1].text, cells[1].page_no,
                                                       cells[1].bbox, cells[1].source_block_id))
        used.add(index)
        diagnostics.append({"source_page": tail.rows[0].page_no, "owner_page": row.page_no,
                            "owner_row": row.cells[0].text, "continuation": cells[1].text,
                            "reason": "label_and_physical_column_continuation"})
    return used, diagnostics
