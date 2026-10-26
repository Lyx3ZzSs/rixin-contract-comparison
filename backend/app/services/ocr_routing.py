"""Page-level OCR routing decisions.

The comparison pipeline must not send every PDF through OCR. This module owns
the *decision* half of that: given cheap native page metrics, decide whether a
single page actually needs OCR, and record why.

The decision itself is a pure function (:class:`PageTextQualityEvaluator`) so it
can be unit tested and benchmarked without touching a PDF. Collecting the
metrics (:class:`NativePageMetricsCollector`) is the only I/O here, and running
OCR is somebody else's job — see ``extractors/contract_v3/``.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import fitz

from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.pymupdf import PyMuPDFExtractor
from app.services.table_compare.constants import TABLE_HEADERS

# Why a page was routed to OCR. Kept short and stable: they are aggregated into
# task metrics (``ocr_reason.<reason>``) and read from debug logs.
REASON_NATIVE_OK = "native_text_ok"
REASON_EMPTY_PAGE = "empty_page"
REASON_SPARSE_TEXT = "sparse_native_text"
REASON_SCANNED_PAGE = "scan_image_no_native_text"
REASON_LARGE_IMAGE_LOW_TEXT = "large_image_low_native_text"
REASON_PARTIAL_IMAGE_LOW_TEXT = "partial_image_sparse_native_text"
REASON_CORRUPT_ENCODING = "corrupt_text_encoding"
REASON_MISSING_CHAR_BOXES = "missing_char_boxes"
REASON_TABLE_PAGE = "table_page"
REASON_FORM_LAYOUT = "form_like_layout"
REASON_FIELD_FORM = "field_form_layout"
REASON_MULTI_COLUMN = "multi_column_layout"
REASON_SIGNING_TEXT = "signing_region_text"
REASON_RED_VECTOR = "red_vector_graphics"
REASON_NATIVE_FAILED = "native_extraction_unavailable"

SIGNING_PATTERN = re.compile(
    r"签署页|签字页|签章页|(?:甲方|乙方|买方|卖方).{0,8}(?:盖章|签章)|"
    r"法定代表人.{0,8}(?:签字|签章)|授权代表.{0,8}(?:签字|签章)|"
    r"signaturepage|authorizedrepresentative.{0,12}signature",
    re.IGNORECASE,
)
# Characters that indicate a broken font encoding or a replacement glyph.
SUSPICIOUS_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})


@dataclass(frozen=True)
class PageNativeMetrics:
    """Cheap per-page native signals. No OCR, no rasterization."""

    page_no: int
    width: float
    height: float
    native_char_count: int
    valid_char_count: int
    suspicious_char_count: int
    char_box_count: int
    page_area: float
    image_coverage: float
    largest_image_ratio: float
    image_count: int
    vector_table_count: int
    text_table_like: bool
    line_count: int
    median_line_chars: float
    field_line_count: int
    multi_column: bool
    red_vector: bool
    signing_text: bool

    @property
    def valid_ratio(self) -> float:
        return self.valid_char_count / self.native_char_count if self.native_char_count else 0.0

    @property
    def suspicious_ratio(self) -> float:
        return self.suspicious_char_count / self.native_char_count if self.native_char_count else 0.0

    @property
    def char_box_coverage(self) -> float:
        return self.char_box_count / self.native_char_count if self.native_char_count else 0.0

    @property
    def table_like(self) -> bool:
        return self.vector_table_count > 0 or self.text_table_like


@dataclass(frozen=True)
class OcrDecision:
    """Why one page will (or will not) be sent to OCR."""

    needs_ocr: bool
    reason: str
    native_char_count: int
    printable_ratio: float | None = None
    image_coverage: float | None = None
    quality_score: float | None = None

    def as_log_fields(self, page_no: int) -> dict[str, object]:
        return {
            "page": page_no,
            "route": "ocr" if self.needs_ocr else "native",
            "reason": self.reason,
            "native_chars": self.native_char_count,
            "image_coverage": self.image_coverage,
        }


@dataclass(frozen=True)
class OcrRoutingThresholds:
    """Calibrated against the golden contract corpus (see the routing report).

    ``min_native_chars`` sits at 80: healthy digital contract pages in the corpus
    carry 300-900 characters, while logo/cover/signature pages carry 5-79 and
    scanned pages carry 0. The image thresholds are 0.5 for a full-page raster
    and 0.15 for a partial one; every text page in the corpus measured either
    0.0 or <=0.27 coverage, so the gap is wide.
    """

    min_native_chars: int = 80
    scan_image_coverage: float = 0.5
    partial_image_coverage: float = 0.15
    max_suspicious_char_ratio: float = 0.01
    min_suspicious_chars: int = 8
    min_printable_ratio: float = 0.85
    min_char_box_coverage: float = 0.9
    min_chars_for_char_box_check: int = 40
    # Borderless forms (开工申请单/交工验收单 and similar) are reported as tables by
    # the layout service but have no ruling lines for ``find_tables`` to find. They
    # look like many short lines, which is what this catches.
    form_line_count: int = 15
    form_median_line_chars: int = 8
    # Party/field pages (甲方/乙方/开户行/账号…). Their display headings are not
    # classified on the native path, so heading text leaks into clause bodies and
    # produces one-sided diffs against an OCR-read counterpart.
    field_form_min_field_lines: int = 8
    field_form_min_ratio: float = 0.4
    # Structure-preservation rules. A natively extracted page carries no
    # ``raw_html``/``table_cell_bboxes``/seal blocks, so these page classes are
    # still sent to OCR to keep the downstream table and seal channels alive.
    protect_tables: bool = True
    protect_field_forms: bool = True
    protect_signing_regions: bool = True
    protect_red_vectors: bool = True
    protect_multi_column: bool = True


def thresholds_from_settings(app_settings) -> OcrRoutingThresholds:
    """Map the flat ``Settings`` fields onto the routing thresholds."""
    return OcrRoutingThresholds(
        min_native_chars=app_settings.ocr_routing_min_native_chars,
        scan_image_coverage=app_settings.ocr_routing_scan_image_coverage,
        partial_image_coverage=app_settings.ocr_routing_partial_image_coverage,
        max_suspicious_char_ratio=app_settings.ocr_routing_max_suspicious_char_ratio,
        min_suspicious_chars=app_settings.ocr_routing_min_suspicious_chars,
        min_printable_ratio=app_settings.ocr_routing_min_printable_ratio,
        min_char_box_coverage=app_settings.ocr_routing_min_char_box_coverage,
        protect_tables=app_settings.ocr_routing_protect_tables,
        protect_field_forms=app_settings.ocr_routing_protect_field_forms,
        protect_signing_regions=app_settings.ocr_routing_protect_signing_regions,
        protect_red_vectors=app_settings.ocr_routing_protect_red_vectors,
        protect_multi_column=app_settings.ocr_routing_protect_multi_column,
        form_line_count=app_settings.ocr_routing_form_line_count,
        form_median_line_chars=app_settings.ocr_routing_form_median_line_chars,
        field_form_min_field_lines=app_settings.ocr_routing_field_form_min_field_lines,
        field_form_min_ratio=app_settings.ocr_routing_field_form_min_ratio,
    )


class PageTextQualityEvaluator:
    """Pure page-level OCR router. No I/O, no randomness, fully testable."""

    def __init__(self, thresholds: OcrRoutingThresholds | None = None) -> None:
        self.thresholds = thresholds or OcrRoutingThresholds()

    def evaluate(self, metrics: PageNativeMetrics) -> OcrDecision:
        thresholds = self.thresholds
        image_coverage = metrics.image_coverage
        printable_ratio = metrics.valid_ratio if metrics.native_char_count else None

        def decide(needs_ocr: bool, reason: str) -> OcrDecision:
            return OcrDecision(
                needs_ocr=needs_ocr,
                reason=reason,
                native_char_count=metrics.native_char_count,
                printable_ratio=printable_ratio,
                image_coverage=image_coverage,
                quality_score=self.quality_score(metrics),
            )

        if metrics.native_char_count == 0:
            # A blank page has nothing to recognize; a page-sized raster does.
            if image_coverage >= thresholds.scan_image_coverage:
                return decide(True, REASON_SCANNED_PAGE)
            return decide(False, REASON_EMPTY_PAGE)

        # Broken text layers come first: a garbled page also defeats the text-based
        # detectors below, so its "clean" signals are worthless.
        if (
            metrics.suspicious_char_count >= thresholds.min_suspicious_chars
            and metrics.suspicious_ratio > thresholds.max_suspicious_char_ratio
        ):
            return decide(True, REASON_CORRUPT_ENCODING)
        if metrics.valid_ratio < thresholds.min_printable_ratio:
            return decide(True, REASON_CORRUPT_ENCODING)
        if (
            metrics.native_char_count >= thresholds.min_chars_for_char_box_check
            and metrics.char_box_coverage < thresholds.min_char_box_coverage
        ):
            return decide(True, REASON_MISSING_CHAR_BOXES)

        # Structure protection is checked before the "too little text" branch: a
        # short page that carries a table still needs its table structure.
        if thresholds.protect_tables and metrics.table_like:
            return decide(True, REASON_TABLE_PAGE)
        if thresholds.protect_tables and self._is_form_like(metrics):
            return decide(True, REASON_FORM_LAYOUT)
        if thresholds.protect_field_forms and self._is_field_form(metrics):
            return decide(True, REASON_FIELD_FORM)

        if metrics.native_char_count < thresholds.min_native_chars:
            if image_coverage >= thresholds.scan_image_coverage:
                return decide(True, REASON_LARGE_IMAGE_LOW_TEXT)
            if image_coverage >= thresholds.partial_image_coverage:
                return decide(True, REASON_PARTIAL_IMAGE_LOW_TEXT)
            return decide(False, REASON_SPARSE_TEXT)

        if thresholds.protect_multi_column and metrics.multi_column:
            return decide(True, REASON_MULTI_COLUMN)
        if thresholds.protect_signing_regions and metrics.signing_text:
            return decide(True, REASON_SIGNING_TEXT)
        if thresholds.protect_red_vectors and metrics.red_vector:
            return decide(True, REASON_RED_VECTOR)

        return decide(False, REASON_NATIVE_OK)

    def _is_form_like(self, metrics: PageNativeMetrics) -> bool:
        """Borderless form/table grid rather than prose: many, short lines."""
        return (
            metrics.line_count >= self.thresholds.form_line_count
            and metrics.median_line_chars <= self.thresholds.form_median_line_chars
        )

    def _is_field_form(self, metrics: PageNativeMetrics) -> bool:
        """Mostly ``label: value`` lines, i.e. a party/field form page."""
        if metrics.field_line_count < self.thresholds.field_form_min_field_lines:
            return False
        if not metrics.line_count:
            return False
        return metrics.field_line_count / metrics.line_count >= self.thresholds.field_form_min_ratio

    @staticmethod
    def quality_score(metrics: PageNativeMetrics) -> float | None:
        """0..1 blend of text density, printable ratio and coordinate coverage."""
        if metrics.native_char_count == 0:
            return None
        density = min(1.0, metrics.native_char_count / OcrRoutingThresholds.min_native_chars)
        return round(
            0.4 * density + 0.35 * metrics.valid_ratio + 0.25 * metrics.char_box_coverage,
            4,
        )


def evaluate_document(
    metrics: NativeDocumentMetrics,
    evaluator: PageTextQualityEvaluator,
) -> dict[int, OcrDecision]:
    """Per-page routing decision for one document, ignoring any pair-level gate."""
    return {page.page_no: evaluator.evaluate(page) for page in metrics.pages}


def native_capable_ratio(
    metrics: NativeDocumentMetrics,
    evaluator: PageTextQualityEvaluator,
) -> float:
    """Fraction of a document's pages that could be read without OCR.

    Used as a document-level gate: reading one contract natively while the other
    is OCR-ed compares two different renderings of the same page (a scanned
    checkbox comes back as ``∠`` from OCR and ``/；`` from the PDF text layer),
    which shows up as confirmed diffs for contract text that never changed.
    """
    if not metrics.pages:
        return 0.0
    decisions = evaluate_document(metrics, evaluator)
    readable = sum(1 for decision in decisions.values() if not decision.needs_ocr)
    return readable / len(metrics.pages)


@dataclass
class NativeDocumentMetrics:
    page_count: int
    pages: list[PageNativeMetrics]
    extraction: ExtractionResult | None
    error: str = ""


def is_suspicious_char(char: str) -> bool:
    if char == "�":
        return True
    return unicodedata.category(char) in SUSPICIOUS_CATEGORIES


def _is_valid_char(char: str) -> bool:
    code = ord(char)
    if 0x4E00 <= code <= 0x9FFF:  # CJK unified ideographs
        return True
    if 0x3000 <= code <= 0x303F:  # CJK punctuation
        return True
    if 0xFF00 <= code <= 0xFFEF:  # fullwidth forms
        return True
    if 0x2000 <= code <= 0x206F:  # general punctuation
        return True
    if char.isascii() and char.isprintable():
        return True
    return char.isalpha() or char.isdigit()


def _line_stats(text: str) -> tuple[int, float, int]:
    """Non-empty line count, median line length, and ``label: value`` line count."""
    lengths: list[int] = []
    field_lines = 0
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", "", raw_line)
        if not line:
            continue
        lengths.append(len(line))
        if "：" in line or ":" in line:
            field_lines += 1
    if not lengths:
        return 0, 0.0, 0
    return len(lengths), statistics.median(lengths), field_lines


def _table_like_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    header_hits = sum(header in compact for header in TABLE_HEADERS)
    if header_hits < 3:
        return False
    return len(re.findall(r"\d+(?:\.\d+)?", compact)) >= 2


def _looks_multi_column(page: fitz.Page) -> bool:
    blocks = [
        block
        for block in page.get_text("blocks")
        if len(re.sub(r"\s+", "", str(block[4]))) >= 5
    ]
    for index, left in enumerate(blocks):
        for right in blocks[index + 1 :]:
            overlap = min(left[3], right[3]) - max(left[1], right[1])
            min_height = min(left[3] - left[1], right[3] - right[1])
            if overlap <= 0 or overlap / max(1.0, min_height) < 0.6:
                continue
            gap = max(right[0] - left[2], left[0] - right[2])
            if gap >= max(12.0, page.rect.width * 0.02):
                return True
    return False


def _has_red_vector(drawings: list[dict]) -> bool:
    for drawing in drawings:
        for key in ("color", "fill"):
            value = drawing.get(key)
            if not isinstance(value, (list, tuple)) or len(value) < 3:
                continue
            red, green, blue = (float(component) for component in value[:3])
            if red >= 0.5 and red >= green * 1.5 and red >= blue * 1.5:
                return True
    return False


def _image_coverage(page: fitz.Page, *, grid: int = 64) -> tuple[float, float, int]:
    """Union raster coverage, largest single image ratio, image count.

    Uses a coarse occupancy grid so overlapping images (a stamp on top of a scan)
    are not double counted.
    """
    rect = page.rect
    area = rect.width * rect.height
    infos = page.get_image_info()
    if area <= 0 or not infos:
        return 0.0, 0.0, len(infos)
    cell_w = rect.width / grid
    cell_h = rect.height / grid
    filled = bytearray(grid * grid)
    largest = 0.0
    for info in infos:
        raw = info.get("bbox")
        if not raw:
            continue
        box = fitz.Rect(raw)
        largest = max(largest, max(0.0, box.width) * max(0.0, box.height) / area)
        clipped = box & rect
        if clipped.is_empty:
            continue
        col0 = max(0, min(grid - 1, int((clipped.x0 - rect.x0) / cell_w)))
        col1 = max(0, min(grid - 1, int((clipped.x1 - rect.x0 - 1e-6) / cell_w)))
        row0 = max(0, min(grid - 1, int((clipped.y0 - rect.y0) / cell_h)))
        row1 = max(0, min(grid - 1, int((clipped.y1 - rect.y0 - 1e-6) / cell_h)))
        for row in range(row0, row1 + 1):
            base = row * grid
            for col in range(col0, col1 + 1):
                filled[base + col] = 1
    return sum(filled) / (grid * grid), largest, len(infos)


class NativePageMetricsCollector:
    """Collects :class:`PageNativeMetrics` for every page of a PDF, cheaply.

    Reuses the PyMuPDF extraction that the native fast path would produce anyway,
    so the metrics describe exactly the text that would reach the pipeline.
    """

    def __init__(self, *, with_tables: bool = True, extractor: PyMuPDFExtractor | None = None) -> None:
        self.with_tables = with_tables
        self.extractor = extractor or PyMuPDFExtractor()

    def collect(self, path: str | Path) -> NativeDocumentMetrics:
        path = Path(path)
        extraction: ExtractionResult | None = None
        error = ""
        try:
            extraction = self.extractor.extract(path, task_id=None)
            native_pages = {page.page_no: page for page in extraction.document.pages}
        except DocumentExtractionError as exc:
            native_pages = {}
            error = str(exc)

        counted = self._count_chars(path, native_pages)
        pages: list[PageNativeMetrics] = []
        with fitz.open(path) as pdf:
            for index, page in enumerate(pdf, start=1):
                total, valid, suspicious, boxed = counted.get(index, (0, 0, 0, 0))
                drawings = page.get_drawings() if self.with_tables else []
                tables = 0
                if self.with_tables:
                    try:
                        tables = len(page.find_tables(paths=drawings).tables)
                    except Exception:  # pragma: no cover - defensive, engine quirk
                        tables = 0
                coverage, largest, image_count = _image_coverage(page)
                text = page.get_text("text")
                line_count, median_line_chars, field_line_count = _line_stats(text)
                pages.append(
                    PageNativeMetrics(
                        page_no=index,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        native_char_count=total,
                        valid_char_count=valid,
                        suspicious_char_count=suspicious,
                        char_box_count=boxed,
                        page_area=float(page.rect.width * page.rect.height),
                        image_coverage=coverage,
                        largest_image_ratio=largest,
                        image_count=image_count,
                        vector_table_count=tables,
                        text_table_like=_table_like_text(text),
                        line_count=line_count,
                        median_line_chars=median_line_chars,
                        field_line_count=field_line_count,
                        multi_column=_looks_multi_column(page),
                        red_vector=_has_red_vector(drawings),
                        signing_text=bool(SIGNING_PATTERN.search(re.sub(r"\s+", "", text))),
                    )
                )
        return NativeDocumentMetrics(
            page_count=len(pages),
            pages=pages,
            extraction=extraction,
            error=error,
        )

    @staticmethod
    def _count_chars(
        path: Path,
        native_pages: dict[int, object],
    ) -> dict[int, tuple[int, int, int, int]]:
        """(total, valid, suspicious, boxed) per page, from the native extraction."""
        counted: dict[int, tuple[int, int, int, int]] = {}
        if not native_pages:
            with fitz.open(path) as pdf:
                for index, page in enumerate(pdf, start=1):
                    text = page.get_text("text")
                    chars = [char for char in text if not char.isspace()]
                    counted[index] = (
                        len(chars),
                        sum(1 for char in chars if _is_valid_char(char)),
                        sum(1 for char in chars if is_suspicious_char(char)),
                        0,
                    )
            return counted
        for page_no, page in native_pages.items():
            text_chars = [char for block in page.blocks for char in block.text if not char.isspace()]
            boxed = sum(
                1
                for block in page.blocks
                for box in block.char_boxes
                if box.char and not box.char.isspace()
            )
            counted[page_no] = (
                len(text_chars),
                sum(1 for char in text_chars if _is_valid_char(char)),
                sum(1 for char in text_chars if is_suspicious_char(char)),
                boxed,
            )
        return counted
