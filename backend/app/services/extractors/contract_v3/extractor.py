"""Production Native-first, PP-DocLayoutV3, selective PP-OCRv6 extraction."""

from __future__ import annotations

from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import logging
from pathlib import Path
import time

import fitz
from PIL import Image

from app.config import Settings, settings
from app.models import Document
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.contract_v3.artifact import UnifiedArtifactAdapter, merge_native, regions_from_official
from app.services.extractors.contract_v3.layout_client import PPDocLayoutV3Client
from app.services.extractors.contract_v3.native import NativeReader
from app.services.extractors.contract_v3.native_repair import NativeHeadingRoleRepair
from app.services.extractors.contract_v3.ocr import SelectiveOCRRouter, coverage_plan
from app.services.extractors.contract_v3.semantic import SemanticRoleResolver
from app.services.extractors.contract_v3.tables import TableResolver
from app.services.extractors.contract_v3.text_contract_repair import apply_text_contract_repairs

logger = logging.getLogger(__name__)


class ContractExtractionV3:
    name = "contract_extraction_v3"

    def __init__(
        self,
        config: Settings = settings,
        *,
        layout: PPDocLayoutV3Client | None = None,
        ocr: SelectiveOCRRouter | None = None,
        tables: TableResolver | None = None,
    ) -> None:
        self.config = config
        self.layout = layout or PPDocLayoutV3Client(config)
        self.ocr = ocr or SelectiveOCRRouter(config)
        self.tables = tables or TableResolver(config)
        self.native = NativeReader()
        self.adapter = UnifiedArtifactAdapter()

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        source = Path(path)
        if not source.is_file() or source.suffix.lower() != ".pdf":
            raise DocumentExtractionError(f"无效 PDF 文件: {source}")
        started = time.perf_counter()
        if hasattr(self.tables, "start_document"):
            self.tables.start_document()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        artifacts: list[dict] = []
        samples: list[dict] = []
        images: list[Image.Image] = []
        prefetch: dict[str, Future] = {}
        route_counts: Counter[str] = Counter()
        ocr_calls = 0
        table_calls = 0
        # Keep at most max_inflight rendered pages in memory or queued for Layout.
        pending: deque[tuple[dict, dict, Image.Image, Future]] = deque()
        try:
            with (
                fitz.open(source) as pdf,
                ThreadPoolExecutor(max_workers=self.config.ppdoclayout_max_inflight) as layout_pool,
                ThreadPoolExecutor(max_workers=1) as ocr_pool,
            ):
                if len(pdf) == 0:
                    raise DocumentExtractionError("PDF 没有页面")

                def collect() -> None:
                    sample, raw, image, future = pending.popleft()
                    boxes = future.result()
                    regions = regions_from_official(sample, boxes, {"boxes": []})
                    artifact = merge_native(sample, raw, regions)
                    artifacts.append(artifact)
                    samples.append(sample)
                    images.append(image)

                for page_no, page in enumerate(pdf, 1):
                    sample, raw, image, png = self.native.read_page(
                        page, source=str(source), digest=digest, page_no=page_no
                    )
                    # Pages without native glyphs can start their one full-page OCR
                    # while Layout runs. All other OCR waits for coverage analysis.
                    if not self.native.has_native_chars(raw):
                        prefetch[sample["id"]] = ocr_pool.submit(self.ocr.prefetch_scanned, image)
                    pending.append((sample, raw, image, layout_pool.submit(self.layout.predict_page, png)))
                    if len(pending) >= self.config.ppdoclayout_max_inflight:
                        collect()
                while pending:
                    collect()

                NativeHeadingRoleRepair().apply(artifacts, str(source))
                resolver = SemanticRoleResolver()
                resolver.resolve(artifacts, str(source))
                for artifact, sample, image in zip(artifacts, samples, images, strict=True):
                    artifact["coverage"] = coverage_plan(artifact, sample)
                    route = artifact["coverage"]["route"]
                    route_counts[route] += 1
                    prefetched = prefetch.pop(sample["id"]).result() if sample["id"] in prefetch else None
                    ocr_calls += self.ocr.recognize(artifact, sample, image, prefetched=prefetched)
                    artifact["extraction_complete"] = True
                from app.services.extractors.contract_v3.printed_ownership import recover_table_headers
                for artifact in artifacts:
                    recover_table_headers(artifact)
                resolver.resolve(artifacts, str(source))
                apply_text_contract_repairs(artifacts)
                for artifact, image in zip(artifacts, images, strict=True):
                    table_calls += self.tables.resolve(artifact, pdf[artifact["page_no"] - 1], image)
        except DocumentExtractionError:
            raise
        except Exception as exc:
            logger.exception("ContractExtractionV3 failed for %s", source)
            raise DocumentExtractionError(f"PP-DocLayoutV3 提取失败: {exc}") from exc
        document = Document(
            filename=source.name,
            path=str(source),
            page_count=len(artifacts),
            pages=[self.adapter.to_page(artifact) for artifact in artifacts],
        )
        logger.info(
            "ContractExtractionV3 completed pages=%d routes=%s layout=%d ocr=%d table=%d",
            len(artifacts),
            dict(route_counts),
            len(artifacts),
            ocr_calls,
            table_calls,
        )
        return ExtractionResult(
            document=document,
            extractor_used=self.name,
            performance={
                "duration_seconds": round(time.perf_counter() - started, 6),
                "routes": dict(route_counts),
                "layout_calls": len(artifacts),
                "ocr_calls": ocr_calls,
                "table_calls": table_calls,
                "hidden_native_chars_ignored": sum(
                    not c.get("native_visible", True)
                    for artifact in artifacts for c in artifact["native_chars"]
                ),
            },
        )
