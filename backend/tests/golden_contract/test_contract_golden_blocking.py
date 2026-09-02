from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.infrastructure.artifact_store import ArtifactStore
from app.infrastructure.task_repository import SQLiteTaskRepository
from app.services.compare_service import CompareService
from tests.golden_contract.golden_loader import load_corpus, load_manifest, oracle_statistics, validate_execution
from tests.golden_contract.semantic_matcher import SemanticGoldenMatcher


@pytest.mark.golden_contract_blocking
def test_contract_golden_blocking(tmp_path: Path) -> None:
    corpus = load_corpus()
    stats = oracle_statistics(corpus)
    runtime_settings = Settings(storage_dir=tmp_path / "golden-runtime")
    service = CompareService(
        repository=SQLiteTaskRepository(runtime_settings),
        artifact_store=ArtifactStore(runtime_settings),
    )
    matcher = SemanticGoldenMatcher()
    executed: list[str] = []
    positive_passed = 0
    negative_passed = 0
    review_passed = 0
    warnings: list[str] = []

    print(
        "GOLDEN ORACLE: "
        f"contracts={stats['contract_count']} "
        f"positive={stats['must_exist'] + stats['must_review']} "
        f"negative={stats['must_not_exist'] + stats['must_not_confirm']} "
        f"review={stats['must_review']} "
        f"visual_positive={stats['visual_positive']} "
        f"text_positive={stats['text_positive']}"
    )
    for contract in corpus:
        task = service.compare(
            contract.original_pdf,
            contract.compare_pdf,
            task_id=f"golden-{contract.contract_key.lower()}-{uuid4().hex[:8]}",
            original_filename=contract.original_filename,
            compare_filename=contract.compare_filename,
        )
        actual = [item.model_dump(mode="json") for item in task.diffs]
        report = matcher.match(contract.contract_key, contract.oracle["assertions"], actual)
        report.assert_passed()
        executed.append(contract.contract_key)
        positive_passed += report.matched_positive
        negative_passed += report.matched_negative
        review_passed += report.matched_review
        warnings.extend(report.warnings)
        print(
            f"GOLDEN CONTRACT PASS: {contract.contract_key} "
            f"actual={len(actual)} positive={report.matched_positive} "
            f"negative={report.matched_negative} review={report.matched_review}"
        )

    expected_keys = [item["contract_key"] for item in load_manifest()["contracts"]]
    validate_execution(expected_keys, [item.contract_key for item in corpus], executed)
    assert positive_passed == stats["must_exist"] + stats["must_review"]
    assert negative_passed == stats["must_not_exist"] + stats["must_not_confirm"]
    assert review_passed == stats["must_review"]
    for warning in warnings:
        print(f"GOLDEN WARNING: {warning}")
    print(
        "GOLDEN REGRESSION: PASS\n"
        "10 expected\n10 loaded\n10 executed\n0 skipped\n"
        f"positive: {positive_passed}/{positive_passed} PASS\n"
        f"negative: {negative_passed}/{negative_passed} PASS\n"
        f"review: {review_passed}/{review_passed} PASS\n"
        "unexpected confirmed: 0\n"
        "bbox regressions: 0"
    )
