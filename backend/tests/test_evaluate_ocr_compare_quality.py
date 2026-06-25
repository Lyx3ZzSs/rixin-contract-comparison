from pathlib import Path

from scripts.evaluate_ocr_compare_quality import discover_cases


def test_discover_cases_ignores_incomplete_directories() -> None:
    cases = discover_cases(Path("tests/fixtures/ocr_compare_cases"))

    assert [case.case_id for case in cases] == ["simple_scanned"]
    assert cases[0].case_dir == Path("tests/fixtures/ocr_compare_cases/simple_scanned")
