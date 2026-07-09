from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


def _frozen_mapping(values: dict[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class ClauseSplitSettings:
    skip_block_types: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "footer",
                "header",
                "page_footer",
                "page_header",
                "footnote",
                "vision_footnote",
                "image",
                "figure",
                "seal",
                "chart",
                "formula",
                "vertical_text",
            }
        )
    )
    min_ocr_confidence: float = 0.5
    short_noise_confidence: float = 0.7
    vertical_height_width_ratio: float = 2.3
    mask_block_types: frozenset[str] = field(default_factory=lambda: frozenset({"formula", "chart", "image", "figure"}))
    mask_overlap_threshold: float = 0.5
    min_content_density: float = 0.15
    reading_order_same_line_x_backtrack: float = 8.0
    table_block_types: frozenset[str] = field(default_factory=lambda: frozenset({"table", "table_title"}))
    cover_block_types: frozenset[str] = field(default_factory=lambda: frozenset({"doc_title", "title"}))
    excluded_block_roles: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "cover_metadata",
                "table_caption",
                "table_note",
                "page_footer",
                "body_footnote",
                "noise",
            }
        )
    )
    section_role_map: Mapping[str, str] = field(
        default_factory=lambda: _frozen_mapping(
            {
                "appendix": "appendix",
                "appendix_section": "appendix",
                "quote": "quote",
                "quote_section": "quote",
                "quote_metadata": "quote",
                "safety_agreement": "safety_agreement",
                "safety_section": "safety_agreement",
            }
        )
    )
    heading_accept_score: float = 0.68
    weak_heading_review_score: float = 0.55
    heading_business_terms: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "服务范围",
                "服务内容",
                "合同金额",
                "付款",
                "结算",
                "发票",
                "交付",
                "验收",
                "违约",
                "保密",
                "知识产权",
                "争议解决",
                "不可抗力",
                "合同期限",
                "生效",
                "终止",
                "安全",
                "质量",
                "联系人",
                "技术要求",
                "系统管理",
            }
        )
    )


DEFAULT_CLAUSE_SPLIT_SETTINGS = ClauseSplitSettings()
