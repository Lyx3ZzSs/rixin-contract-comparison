"""Recover rejected forms using unique same-content evidence on aligned pages."""

from app.models import BBox
from app.services.signing_region.models import SigningBlock, SigningBlockRole, SigningBlockConfidenceLevel


def recover_paired_forms(detector, original_doc, compare_doc, original, compare, alignment):
    if alignment is None:
        return []
    decisions = []
    # Only pre-existing detections may corroborate recovery, never a recovered one.
    for side, doc, result, peers in (
        ("original", original_doc, original, tuple(compare.blocks)),
        ("compare", compare_doc, compare, tuple(original.blocks)),
    ):
        pages = {p.page_no: p for p in doc.pages}
        peer_doc = compare_doc if side == "original" else original_doc
        peer_pages = {p.page_no: p for p in peer_doc.pages}
        proposals = []
        for candidate in result.low_confidence_candidates:
            page = pages.get(candidate.get("page_no"))
            if page is None or float(candidate.get("score", 0)) < 0.35:
                continue
            names = detector._company_party_names(candidate.get("text", ""))
            if set(names) != {"甲方", "乙方"}:
                continue
            box = BBox.model_validate(candidate["bbox"])
            matches = []
            for peer in peers:
                # A full form may contain additional fields. Compare only its
                # independently recognized company-name source blocks.
                pp = peer_pages[peer.page_no]
                peer_names = {}
                for b in pp.blocks:
                    if b.block_id in peer.source_block_ids:
                        peer_names.update(detector._company_party_names(b.text))
                op, cp = (page.page_no, peer.page_no) if side == "original" else (peer.page_no, page.page_no)
                if names != peer_names or alignment.relationship_score({op}, {cp}) < 0.8:
                    continue
                if abs(box.y0 / page.height - peer.bbox.y0 / pp.height) > 0.12:
                    continue
                matches.append(peer)
            if len(matches) == 1:
                proposals.append((candidate, page, matches[0]))
        for candidate, page, peer in proposals:
            if sum(p.block_id == peer.block_id for _, _, p in proposals) != 1:
                continue
            ids = set(candidate["block_ids"])
            if any(ids.intersection(b.source_block_ids) for b in result.blocks):
                continue
            sources = detector._extend_party_dates([b for b in page.blocks if b.block_id in ids], page)
            block = SigningBlock(
                block_id=f"SB-{page.page_no}-paired-{len(result.blocks)}",
                page_no=page.page_no,
                bbox=detector._padded_union([b.bbox for b in sources], page),
                block_role=SigningBlockRole.BOTH_PARTIES,
                confidence=0.5,
                confidence_level=SigningBlockConfidenceLevel.MEDIUM,
                confidence_reasons=[*candidate["reasons"], "aligned_peer_identical_parties"],
                source_block_ids=[b.block_id for b in sources],
                text="\n".join(b.text for b in sources),
            )
            result.blocks.append(block)
            result.pages = [p for p in result.pages if p.page_no != page.page_no]
            result.pages.append(
                detector._to_page(page, page.semantic_role, [b for b in result.blocks if b.page_no == page.page_no])
            )
            decisions.append(
                dict(
                    side=side,
                    page_no=page.page_no,
                    block_id=block.block_id,
                    peer_block_id=peer.block_id,
                    reason="aligned_peer_identical_parties",
                )
            )
    return decisions
