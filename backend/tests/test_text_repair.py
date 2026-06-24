from __future__ import annotations

import pytest

from app.services.text_repair import is_clause_marker_ocr_repair, is_delivery_date_placeholder_repair


@pytest.mark.parametrize(
    ("structure_text", "ocr_text"),
    [
        ("交货日期：2026年月日交货", "交货日期：2026年\n且交货"),
        ("交货日期：2026年月日交货", "交货日期：2026年月且交货"),
        ("交货日期：2026年月日交货", "交货日期：2026年目交货"),
        ("交货日期：2026年月日交货", "交货日期：2026年月曰交货"),
        ("交货日期：2026年月日交货", "交货日期：2026年月口交货"),
    ],
)
def test_delivery_date_placeholder_repair_accepts_day_ocr_confusions(
    structure_text: str,
    ocr_text: str,
) -> None:
    assert is_delivery_date_placeholder_repair(structure_text, ocr_text)


@pytest.mark.parametrize(
    ("structure_text", "ocr_text"),
    [
        ("交货日期：2026年4月21日交货", "交货日期：2026年4月2日交货"),
        ("交货日期：2026年月日交货", "交货日期：2027年且交货"),
        ("签订日期：2026年月日", "签订日期：2026年且"),
        ("交货日期：2026年月日交货", "交货日期：2026年5月日交货"),
        ("交货日期：2026年月日交货", "交货日期：2026年月21日交货"),
    ],
)
def test_delivery_date_placeholder_repair_rejects_real_date_or_different_field(
    structure_text: str,
    ocr_text: str,
) -> None:
    assert not is_delivery_date_placeholder_repair(structure_text, ocr_text)


@pytest.mark.parametrize(
    ("structure_text", "ocr_text"),
    [
        ("十二、本合同自双方签字盖章之日起生效。本合同1式4份", "土二、本合同自双方签字盖章之日起生效。本合同1式4份"),
        ("十一、其它约定事项：其它未尽事宜，双方协商解决。", "土一、其它约定事项:其它未尽事宜,双方协商解决。"),
        (
            "十一、其它约定事项：其它未尽事宜，双方协商解决。技术协议与本合同具法律效力。十二、本合同自双方签字盖章之日起生效。本合同1式4份",
            "十一、其它约定事项：其它未尽事宜，双方协商解决。技术协议与本合同具法律效力。\n土二、本合同自双方签字盖章之日起生效。本合同1式4份",
        ),
    ],
)
def test_clause_marker_repair_accepts_tu_as_ten_when_body_matches(
    structure_text: str,
    ocr_text: str,
) -> None:
    assert is_clause_marker_ocr_repair(structure_text, ocr_text)


@pytest.mark.parametrize(
    ("structure_text", "ocr_text"),
    [
        ("十二、本合同自双方签字盖章之日起生效。", "土二、解决合同纠纷的方式。"),
        ("二、质量要求，技术标准。", "土二、质量要求，技术标准。"),
        ("十二、本合同自双方签字盖章之日起生效。", "十二、本合同自双方签字盖章之日起生效。"),
        ("十二、短句", "土二、短句"),
    ],
)
def test_clause_marker_repair_rejects_non_matching_or_non_ten_markers(
    structure_text: str,
    ocr_text: str,
) -> None:
    assert not is_clause_marker_ocr_repair(structure_text, ocr_text)
