"""Explicit, conservative native repair; never hide layout recall in the resolver."""

from collections import Counter

from app.services.extractors.contract_v3.geometry import TITLE_FAMILY, area, overlap


class NativeHeadingRoleRepair:
    def apply(self, artifacts, pdf_path):
        from app.models import BBox
        from app.services.document_role_repair import compact
        from app.services.native_heading_repair import (
            NativeLineRecord,
            NativeSpanStyle,
            load_native_section_title_candidates,
            _same_multiline_title_style,
            _visually_contiguous_title_lines,
            load_native_heading_index,
            NativeHeadingRepairService,
        )

        candidates = load_native_section_title_candidates(pdf_path)
        for page in artifacts:
            records = {}
            for b in page["blocks"]:
                counts = Counter(
                    (page["native_chars"][i]["size"], page["native_chars"][i]["font"], page["native_chars"][i]["flags"])
                    for i in b.get("native_char_indices", [])
                )
                records[b["block_id"]] = NativeLineRecord(
                    b["text"],
                    BBox(**dict(zip(("x0", "y0", "x1", "y1"), b["bbox"]))),
                    (),
                    tuple(NativeSpanStyle(*style, count) for style, count in counts.items()),
                )
            anchors = [
                b for b in page["blocks"] if b["raw_layout_type"] in TITLE_FAMILY and b.get("native_char_indices")
            ]
            pending = []
            for b in page["blocks"]:
                if b["raw_layout_type"] in TITLE_FAMILY or not b.get("native_char_indices"):
                    continue
                candidate = next(
                    (
                        c
                        for c in candidates
                        if c.page_no == page["page_no"]
                        and compact(c.text) == compact(b["text"])
                        and overlap(b["bbox"], [c.bbox.x0, c.bbox.y0, c.bbox.x1, c.bbox.y1]) / area(b["bbox"]) > 0.5
                    ),
                    None,
                )
                if candidate:
                    pending.append((b, candidate))
            # Every chain must terminate in an actual V3 title region, with native style evidence.
            while pending:
                accepted = []
                for b, candidate in pending:
                    record = records[b["block_id"]]

                    def connected(anchor):
                        other = records[anchor["block_id"]]
                        left, right = sorted((record, other), key=lambda r: r.bbox.y0)
                        centered = abs((left.bbox.x0 + left.bbox.x1 - right.bbox.x0 - right.bbox.x1) / 2) <= max(
                            left.bbox.y1 - left.bbox.y0, right.bbox.y1 - right.bbox.y0
                        )
                        return (
                            centered
                            and _same_multiline_title_style(left, right)
                            and _visually_contiguous_title_lines(left, right)
                        )

                    anchor = next((a for a in anchors if connected(a)), None)
                    if anchor:
                        b.update(
                            resolved_block_type=candidate.block_type,
                            confidence=0.9,
                            reason="NativeHeadingRepair.native_section_candidate",
                        )
                        b["native_repair"] = dict(
                            accepted=True,
                            anchor_block_id=anchor["block_id"],
                            reason="existing_native_candidate+v3_title_chain+same_style+contiguous+centered",
                            error_class="LAYOUT_RECALL_ERROR",
                        )
                        anchors.append(b)
                        accepted.append(b["block_id"])
                if not accepted:
                    break
                pending = [(b, c) for b, c in pending if b["block_id"] not in accepted]
            for b, _ in pending:
                b["native_repair"] = dict(
                    accepted=False, reason="no_connected_v3_title_style_anchor", error_class="LAYOUT_RECALL_ERROR"
                )
        # Existing production numbered-heading candidates have a different contract:
        # exact native text + geometry + local parent/child structure. Keep this
        # explicitly separate from visual title resolution and cover-style repair.
        from app.services.extractors.contract_v3.artifact import to_page
        from app.models import Document
        from app.services.clause_splitter import ClauseSplitter
        from types import SimpleNamespace

        doc = Document(
            filename=str(pdf_path).split("/")[-1],
            path=str(pdf_path),
            page_count=len(artifacts),
            pages=[to_page(a) for a in artifacts],
        )
        splitter = ClauseSplitter()
        repair = NativeHeadingRepairService()
        numbered = load_native_heading_index(pdf_path).candidates
        for a, p in zip(artifacts, doc.pages, strict=True):
            for b, production in zip(a["blocks"], p.blocks, strict=True):
                if b["raw_layout_type"] not in {None, "text"} or b.get("native_repair", {}).get("accepted"):
                    continue
                matches = [
                    c
                    for c in numbered
                    if c.page_no == a["page_no"]
                    and compact(c.text) == compact(b["text"])
                    and repair._geometry_matches(production.bbox, c.bbox)
                ]
                if len(matches) != 1:
                    continue
                candidate = matches[0]
                probe = SimpleNamespace(
                    text=b["text"], block_type="text", section_type="main_contract", semantic_reasons=()
                )
                heading = splitter.heading_detector.candidate(probe, splitter._parse_marker(b["text"]), None)
                if heading is not None and heading.score >= splitter.heading_accept_score:
                    continue
                if not repair._has_local_structure(doc, p, production, candidate.number):
                    continue
                b.update(
                    resolved_block_type="paragraph_title",
                    confidence=0.9,
                    reason="NativeHeadingRepair.exact_numbered_candidate+local_structure",
                )
                b["native_repair"] = dict(
                    accepted=True,
                    error_class="LAYOUT_RECALL_ERROR",
                    reason="production_native_heading_index+exact_geometry+production_local_structure",
                    native_number=candidate.number,
                    native_text=candidate.text,
                    style_claim="No visual emphasis claimed; numbering hierarchy is the corroborating evidence",
                )
        from app.services.extractors.contract_v3.colon_heading import apply_colon_repair

        apply_colon_repair(artifacts, pdf_path)
        return artifacts
