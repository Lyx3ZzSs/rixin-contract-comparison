"""Business identity shared by detector fusion and historical event projection."""

from app.models import DiffItem

SEAL_OBJECT_TYPES = frozenset({"COMPANY_ROUND_SEAL", "PERSONAL_NAME_SEAL", "EDGE_SEAM_SEAL", "OTHER_STAMP"})


def normalize_seal_source(diff: DiffItem) -> DiffItem:
    object_type = diff.match_score_details.get("object_type")
    if object_type in SEAL_OBJECT_TYPES or (
        diff.source_type == "signing_region"
        and diff.section_type in {"signature:seal", *(f"signature:{t.lower()}" for t in SEAL_OBJECT_TYPES)}
    ):
        diff.source_type = "seal"
    return diff
