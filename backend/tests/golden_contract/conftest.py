from __future__ import annotations

import pytest


_SKIPPED_BLOCKING_TESTS: list[str] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped and "test_contract_golden_blocking.py" in report.nodeid:
        _SKIPPED_BLOCKING_TESTS.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _SKIPPED_BLOCKING_TESTS:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep(
            "=",
            "GOLDEN EXECUTION ERROR: blocking Golden tests were skipped: "
            + ", ".join(_SKIPPED_BLOCKING_TESTS),
            red=True,
        )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
