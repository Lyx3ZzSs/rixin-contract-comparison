from pathlib import Path

from app.services.ocr_retry import NoopOcrRetryAdapter, OcrRetryRequest


def test_noop_retry_adapter_reports_retry_disabled() -> None:
    request = OcrRetryRequest(
        task_id="task-retry",
        side="original",
        page_no=2,
        pdf_path=Path("original.pdf"),
        route="HIGH_DPI_PAGE_RETRY",
        reason_codes=["LOW_AVG_CONFIDENCE"],
    )

    result = NoopOcrRetryAdapter().retry_page(request)

    assert result.status == "SKIPPED"
    assert result.reason == "RETRY_DISABLED"
    assert result.changed_output is False
    assert result.route == "HIGH_DPI_PAGE_RETRY"
    assert result.side == "original"
    assert result.page_no == 2


def test_noop_retry_adapter_does_not_require_existing_pdf() -> None:
    request = OcrRetryRequest(
        task_id="task-retry",
        side="compare",
        page_no=1,
        pdf_path=Path("missing.pdf"),
        route="TABLE_REGION_RETRY",
        reason_codes=[],
    )

    result = NoopOcrRetryAdapter().retry_page(request)

    assert result.status == "SKIPPED"
    assert result.metrics == {}
