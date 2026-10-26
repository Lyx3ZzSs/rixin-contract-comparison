"""Contract role adapter over existing production predicates; no baseline input."""

from types import SimpleNamespace
from collections import Counter

from app.services.extractors.contract_v3.geometry import TITLE_FAMILY
from app.services.extractors.contract_v3.artifact import to_page


class SemanticRoleResolver:
    def __init__(self):
        from app.services.clause_splitter import ClauseSplitter
        from app.services.document_preparation import DocumentPreparer

        self.splitter = ClauseSplitter()
        self.preparation = DocumentPreparer()

    def body_list_members(self, page):
        """A colon-led, same-style mixed-label list belongs to its introducing body.

        Numbered markers end the list using the existing splitter grammar. This
        rule only demotes an existing V3 title; it cannot recover missing titles.
        """

        def style(b):
            c = Counter(
                (
                    page["native_chars"][i]["font"],
                    round(page["native_chars"][i]["size"], 1),
                    page["native_chars"][i]["flags"],
                )
                for i in b.get("native_char_indices", [])
            )
            return c.most_common(1)[0][0] if c else None

        blocks = page["blocks"]
        members = {}
        for i, intro in enumerate(blocks):
            if intro["raw_layout_type"] in TITLE_FAMILY or not intro["text"].rstrip().endswith(("：", ":")):
                continue
            siblings = []
            for b in blocks[i + 1 :]:
                if self.splitter._parse_marker(b["text"]) or style(b) != style(intro) or not style(b):
                    break
                if b["raw_layout_type"] not in {None, "text", *TITLE_FAMILY}:
                    break
                if b["text"].rstrip().endswith(("。", "；", ";", ":", "：")):
                    break
                prior = siblings[-1] if siblings else intro
                height = b["bbox"][3] - b["bbox"][1]
                if not 0 <= b["bbox"][1] - prior["bbox"][3] <= height * 2.5:
                    break
                if siblings and abs(b["bbox"][0] - siblings[0]["bbox"][0]) > height:
                    break
                siblings.append(b)
            if len(siblings) >= 2 and any(b["raw_layout_type"] not in TITLE_FAMILY for b in siblings):
                members.update(
                    {b["block_id"]: intro["block_id"] for b in siblings if b["raw_layout_type"] in TITLE_FAMILY}
                )
        return members

    def resolve(self, artifacts, pdf_path):
        from app.models import Document
        from app.services.cover.patterns import is_title_candidate
        from app.services.cover.geometry import is_body_start
        from app.services.document_role_repair import repeated_title_ids, compact

        body_started = False
        section = "main_contract"
        for page in sorted(artifacts, key=lambda a: a["page_no"]):
            list_members = self.body_list_members(page)
            adapted = to_page(page)
            for i, (block, production) in enumerate(zip(page["blocks"], adapted.blocks, strict=True)):
                raw_type = block["raw_layout_type"] or "text"
                unit = SimpleNamespace(
                    text=block["text"], block_type=raw_type, section_type=section, split_flags=(), semantic_reasons=()
                )
                marker = self.splitter._parse_marker(unit.text)
                section_role = self.preparation._section_role(production, compact(unit.text))
                if section_role:
                    section = {"appendix_section": "appendix", "safety_section": "safety_agreement"}.get(
                        section_role, section
                    )
                    unit.section_type = section
                repaired = block.get("native_repair", {}).get("accepted", False)
                resolved = block["resolved_block_type"] if repaired else raw_type
                confidence, reason = block["confidence"], block["reason"]
                aliases = {
                    "display_formula": "formula",
                    "inline_formula": "formula",
                    "header_image": "header",
                    "footer_image": "footer",
                }
                if raw_type in aliases:
                    resolved, reason = aliases[raw_type], "v3_to_production_visual_type_alias"
                explicit_body = is_body_start(unit.text, raw_type) or self.splitter._is_body_intro(unit.text)
                if explicit_body and not (repaired and resolved == "doc_title"):
                    body_started = True
                if raw_type in TITLE_FAMILY:
                    # Do not let doc_title itself make the cover predicate self-fulfilling.
                    probe = SimpleNamespace(**{**vars(unit), "block_type": "paragraph_title"})
                    heading = self.splitter.heading_detector.candidate(probe, marker, None)
                    if not body_started and is_title_candidate(self.splitter.normalizer, production):
                        resolved, confidence, reason = "doc_title", 0.9, "cover.is_title_candidate+prebody_context"
                    elif (
                        body_started
                        and section_role
                        and heading is not None
                        and heading.score >= self.splitter.heading_accept_score
                    ):
                        resolved, confidence, reason = (
                            "paragraph_title",
                            heading.score,
                            "DocumentPreparation.section_role+ClauseHeading",
                        )
                    elif raw_type == "doc_title" and not explicit_body:
                        # A visual document title is not demoted by merely pretending
                        # it is paragraph_title and then asking an unnumbered predicate.
                        reason = "v3_doc_title_preserved_without_body_boundary"
                    elif (
                        heading is not None
                        and heading.score >= self.splitter.heading_accept_score
                        and (body_started or not self.splitter._is_pre_body_noise(probe, marker))
                    ):
                        resolved, confidence, reason = (
                            "paragraph_title",
                            heading.score,
                            "ClauseHeading.contextual_heading",
                        )
                        body_started = True
                    if body_started and block["block_id"] in list_members:
                        resolved, confidence, reason = "text", 0.9, "document_context.colon_led_same_style_body_list"
                        block["resolution_evidence"] = {"introducing_block": list_members[block["block_id"]]}
                block.update(
                    resolved_block_type=resolved,
                    semantic_confidence=confidence,
                    reason=reason,
                    document_context=dict(
                        body_started=body_started,
                        section_context=section,
                        previous_block=page["blocks"][i - 1]["block_id"] if i else None,
                        next_block=page["blocks"][i + 1]["block_id"] if i + 1 < len(page["blocks"]) else None,
                        numbering=marker[0] if marker else None,
                    ),
                )
        doc = Document(
            filename=str(pdf_path).split("/")[-1],
            path=str(pdf_path),
            page_count=len(artifacts),
            pages=[to_page(a) for a in sorted(artifacts, key=lambda a: a["page_no"])],
        )
        repeated = repeated_title_ids(doc)
        for page in artifacts:
            for b in page["blocks"]:
                if b["block_id"] in repeated and b["raw_layout_type"] in TITLE_FAMILY:
                    b.update(
                        resolved_block_type="doc_title",
                        confidence=0.95,
                        reason="document_role_repair.repeated_title_ids",
                        enter_clause_compare=False,
                    )
        return artifacts
