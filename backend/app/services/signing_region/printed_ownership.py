"""Recover printed party lines without treating stamp OCR as contract prose."""
import re


PARTY_LINE = re.compile(r"^(甲方|乙方|丙方)\s*[:：]\s*([^\n:：]{4,80}?公司)\s*$")


def reconcile_party_table_diffs(diffs, original, compare):
    """Prefer independently observed, party-bound OCR lines over polluted cells.

    Only a company heading row with short nonnumeric stamp residue qualifies.
    Disagreement or absent primary observations leaves the candidate intact.
    """
    kept, decisions = [], []
    for diff in diffs:
        names = []
        for text in (diff.original_text, diff.compare_text):
            match = re.fullmatch(r"\s*(甲方|乙方|丙方)\s*[:：]\s*([^\n]{4,80}?公司)([^\d\n]{0,24})", text)
            names.append(match)
        if diff.diff_type != "MODIFY" or not all(names) or names[0][1] != names[1][1]:
            kept.append(diff)
            continue
        tails = [re.sub(r'[()（）]|盖章', ' ', m[3]).split() for m in names]
        if any(len(tokens) > 6 or any(len(token) > 1 for token in tokens) for tokens in tails):
            kept.append(diff)
            continue
        observations = []
        for document, evidence in ((original, diff.original_evidence), (compare, diff.compare_evidence)):
            pages = {e.page_no for e in evidence}
            found = set()
            for page in document.pages:
                if page.page_no not in pages:
                    continue
                for block in page.blocks:
                    if block.block_type != "table":
                        continue
                    # Scope the observation to the candidate's actual table.
                    if not any(e.page_no == page.page_no and block.bbox.x0 <= e.bbox.x0 <= block.bbox.x1
                               and block.bbox.y0-8 <= e.bbox.y0 <= block.bbox.y1 for e in evidence):
                        continue
                    for line in block.text.splitlines():
                        m = PARTY_LINE.fullmatch(line.strip())
                        if m and m[1] == names[0][1]:
                            found.add(m[2])
            observations.append(found)
        if (len(observations[0]) == 1 and observations[0] == observations[1]
                and names[0][2] in observations[0]):
            decisions.append({"diff_id": diff.diff_id, "reason": "PARTY_ROW_PRIMARY_OCR_AGREEMENT",
                              "party": names[0][1], "value": next(iter(observations[0]))})
        else:
            kept.append(diff)
    return kept, decisions


def exclude_equal_signing_prose(left_result, right_result, matches, left_blocks, right_blocks):
    """Consume only recognized fields whose party-bound values agree on both sides.

    Unlike region-wide masks, unexpected prose and unrecognized values remain
    in clause comparison. Changed field values remain owned by the field diff.
    """
    def fields(region):
        result = {}
        for e in region.elements:
            if e.element_type not in {'field', 'party_field'}:
                continue
            role = e.raw_ref.get('party_role')
            key = 'party_name' if e.element_type == 'party_field' else e.raw_ref.get('field_key')
            if not role or not key or (role, key) in result:
                return None
            result[role, key] = re.sub(r'\s+', '', e.text).replace('：', ':')
        return result

    for left, right, _ in matches:
        if left is None or right is None:
            continue
        _consume_complete_party_date_fields(left_result, right_result, left, right)
        lf, rf = fields(left), fields(right)
        if not lf or lf != rf or not all((role, 'party_name') in lf for role in ('甲方', '乙方')):
            continue
        names = [v.split(':', 1)[-1] for (role, key), v in lf.items() if key == 'party_name']
        values = {v for v in lf.values() if v}
        for result, region, blocks in ((left_result, left, left_blocks), (right_result, right, right_blocks)):
            owner = next((b for b in blocks if b.block_id == region.signing_block_id), None)
            if owner is None or owner.exclude_from_clause_diff:
                continue
            for page in result.document.pages:
                if page.page_no != region.page_no:
                    continue
                kept = []
                for block in page.blocks:
                    t = re.sub(r'\s+', '', block.text).replace('：', ':')
                    known = (t in values or t in {'代表:', '时间:', '日期:'}
                             or (t.startswith(('时间:', '日期:')) and t.split(':', 1)[1] in values))
                    inscription = (block.block_type in {'seal', 'stamp', 'ocr_line'} and 4 <= len(t) <= 12
                                   and any(t in name for name in names))
                    if block.block_id in owner.source_block_ids and (known or inscription):
                        result.excluded_block_ids.append(block.block_id)
                        result.entries.append({'page_no': page.page_no, 'block_id': block.block_id,
                                               'reason': 'matched_party_bound_signing_field',
                                               'signing_block_id': owner.block_id})
                    else:
                        kept.append(block)
                page.blocks = kept


def _consume_complete_party_date_fields(left_result, right_result, left, right):
    """Matched field comparisons own complete party/date source lines, including edits.

    Partial OCR field matches cannot consume a block with unrelated content.
    Dates are kept out of a second, less structured clause comparison.
    """
    def fields(region):
        found = {}
        for e in region.elements:
            key = (e.raw_ref.get('party_role'), e.raw_ref.get('field_key'))
            if e.element_type not in {'party_field', 'field'} or key[0] not in {'甲方', '乙方'}:
                continue
            if key[1] not in {'party_name', 'date'}:
                continue
            if key in found:
                return {}
            found[key] = e
        return found

    lf, rf = fields(left), fields(right)
    if not all((role, 'party_name') in lf and (role, 'party_name') in rf for role in ('甲方', '乙方')):
        return
    common = lf.keys() & rf.keys()
    for result, region, fs in ((left_result, left, lf), (right_result, right, rf)):
        owned = {}
        for key in common:
            for source_id in fs[key].raw_ref.get('source_block_ids', []):
                owned.setdefault(source_id, []).append(fs[key])
        for page in result.document.pages:
            if page.page_no != region.page_no:
                continue
            kept = []
            for b in page.blocks:
                elems = owned.get(b.block_id, [])
                text = re.sub(r'\s+', '', b.text).replace('：', ':')
                party_parts = [re.sub(r'\s+', '', e.text).replace('：', ':') for e in elems
                               if e.raw_ref.get('field_key') == 'party_name']
                party_text = ''.join(sorted(party_parts, key=text.find))
                date_only = (len(elems) == 1 and elems[0].raw_ref.get('field_key') == 'date'
                             and re.fullmatch(r'(?:\d{4})?年(?:\d{1,2})?月(?:\d{1,2})?日', text))
                if elems and (text == party_text or date_only):
                    result.excluded_block_ids.append(b.block_id)
                    result.entries.append(dict(page_no=page.page_no, block_id=b.block_id,
                                               signing_block_id=region.signing_block_id,
                                               reason='matched_complete_party_date_field'))
                else:
                    kept.append(b)
            page.blocks = kept
