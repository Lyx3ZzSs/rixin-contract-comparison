from __future__ import annotations

from app.clients import HttpClientProvider, default_http_client_provider
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.services.extractors.contract_v3.extractor import ContractExtractionV3


def build_compare_document_extractor(
    *,
    artifact_store: ArtifactStore = default_artifact_store,
    client_provider: HttpClientProvider = default_http_client_provider,
) -> ContractExtractionV3:
    """Build the single production comparison extractor."""
    return ContractExtractionV3()
