from __future__ import annotations

import re
import unicodedata

from app.models import BBox
from app.services.signature_compare import patterns


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def normalize_value(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).lower()


def clean_label(label: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", label or "")).strip(":：")


def clean_value(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("｜", "|")
    value = re.split(r"[|｜\n\r]", value, maxsplit=1)[0]
    value = re.sub(r"^[\s:：,，;；()（）【】\[\]-]+", "", value)
    value = re.sub(r"[\s:：,，;；()（）【】\[\]-]+$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def is_actual_value(value: str) -> bool:
    cleaned = clean_value(value)
    if not cleaned:
        return False
    residue = patterns.TEMPLATE_CLEANUP_PATTERN.sub("", cleaned)
    residue = re.sub(r"[|/\\_\-—~～·•.。,，;；:：()（）【】\[\]\s]+", "", residue)
    if not residue:
        return False
    return not bool(re.fullmatch(r"(?:或|及|和|与|其|负责人|代表|授权|委托|人|无|空|同上)+", residue))


def is_continuation_value(value: str, field_key: str) -> bool:
    cleaned = clean_value(value)
    if not is_actual_value(cleaned):
        return False
    normalized = normalize_value(cleaned)
    if field_key == "address":
        return bool(re.search(r"(?:省|市|区|县|路|街|道|号|楼|室|基地|园区|幢|层)", cleaned) or re.search(r"\d", cleaned))
    if field_key == "bank":
        return bool("银行" in cleaned or "支行" in cleaned or "分行" in cleaned)
    if field_key == "postcode":
        return bool(re.fullmatch(r"\d{3,8}", normalized))
    return True


def role_from_text(text: str) -> tuple[str, str] | None:
    match = patterns.ROLE_PATTERN.search(text or "")
    if not match:
        return None
    raw = compact(match.group(1))
    mapping = {
        "供方": ("supplier", "供方"),
        "需方": ("buyer", "需方"),
        "甲方": ("party_a", "甲方"),
        "乙方": ("party_b", "乙方"),
        "买方": ("purchaser", "买方"),
        "卖方": ("seller", "卖方"),
        "丙方": ("party_c", "丙方"),
        "丁方": ("party_d", "丁方"),
    }
    return mapping.get(raw)


def role_from_x(bbox: BBox | None, width: float) -> tuple[str, str]:
    if bbox is None or width <= 0:
        return "unknown", patterns.PARTY_LABELS["unknown"]
    center = (bbox.x0 + bbox.x1) / 2
    return (
        ("unknown_left", patterns.PARTY_LABELS["unknown_left"])
        if center <= width / 2
        else ("unknown_right", patterns.PARTY_LABELS["unknown_right"])
    )


def labeled_values(text: str) -> list[tuple[str, str, str]]:
    normalized = unicodedata.normalize("NFKC", text or "")
    matches = list(patterns.LABEL_PATTERN.finditer(normalized))
    result: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        label = clean_label(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        value = clean_value(normalized[start:end])
        result.append((label, value, field_key(label)))
    if not result:
        date_match = patterns.DATE_PATTERN.search(normalized)
        if date_match:
            result.append((patterns.FIELD_KEY_LABELS["date"], clean_value(date_match.group(0)), "date"))
    return result


def field_key(label: str) -> str:
    label_compact = compact(label)
    if "单位名称" in label_compact:
        return "company_name"
    if "地址" in label_compact:
        return "address"
    if "联系人" in label_compact:
        return "contact"
    if "邮箱" in label_compact or "email" in label_compact.lower():
        return "email"
    if "合同编号" in label_compact:
        return "contract_no"
    if "签署地点" in label_compact or "签约地点" in label_compact:
        return "sign_place"
    if "法人代表或授权委托人" in label_compact or "授权委托人" in label_compact or "授权代表" in label_compact:
        return "authorized_representative"
    if "法定代表人" in label_compact or "法人代表" in label_compact:
        return "legal_representative"
    if "委托代理人" in label_compact:
        return "agent"
    if "电话" in label_compact:
        return "phone"
    if "传真" in label_compact:
        return "fax"
    if "开户" in label_compact:
        return "bank"
    if "账号" in label_compact or "帐号" in label_compact:
        return "account"
    if "统一社会信用代码" in label_compact or "纳税人识别号" in label_compact:
        return "credit_code"
    if "税号" in label_compact:
        return "tax_no"
    if "邮政编码" in label_compact or "政编码" in label_compact:
        return "postcode"
    if "日期" in label_compact:
        return "date"
    return label_compact or "unknown"


def looks_like_signature_text(text: str) -> bool:
    text_compact = compact(text)
    return bool(text_compact and patterns.SIGNATURE_TERMS.search(text_compact))


def union_bbox(left: BBox, right: BBox | None) -> BBox:
    if right is None:
        return left
    return BBox(
        x0=min(left.x0, right.x0),
        y0=min(left.y0, right.y0),
        x1=max(left.x1, right.x1),
        y1=max(left.y1, right.y1),
    )


def position_similarity(left: BBox, right: BBox) -> float:
    left_center = ((left.x0 + left.x1) / 2, (left.y0 + left.y1) / 2)
    right_center = ((right.x0 + right.x1) / 2, (right.y0 + right.y1) / 2)
    dist = abs(left_center[0] - right_center[0]) + abs(left_center[1] - right_center[1])
    return max(0.0, 1.0 - dist / 900.0)


def value_equal(left: str, right: str) -> bool:
    left_norm = normalize_value(left)
    right_norm = normalize_value(right)
    return bool(left_norm and right_norm and left_norm == right_norm)
