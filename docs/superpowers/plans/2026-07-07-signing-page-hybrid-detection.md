# Signing Page Hybrid Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the confirmed signing-page detection strategy: rule/OCR/layout detection as the primary path, local OpenCV as a visual assistant, and no VLM in the default production flow.

**Architecture:** Complete the rule/bbox safety baseline first, then add a local OpenCV detector that implements the existing `VisualSignatureDetector` protocol. Pipeline fusion remains conservative: high-confidence rule results pass directly, visual detections enrich or promote rule-backed candidates, and visual-only detections stay in debug output unless a rule/layout candidate supports them.

**Tech Stack:** Python 3.12, FastAPI backend, Pydantic settings/models, PyMuPDF, OpenCV headless, pytest.

---

## Prerequisite

Before starting this plan, complete the rule and bbox safety plan at:

```text
docs/superpowers/plans/2026-07-07-signing-region-continuation-bbox.md
```

The prerequisite plan must pass:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py backend/tests/test_signing_region_pipeline.py backend/tests/test_signing_clause_document.py -q
```

Expected:

```text
... passed
```

This hybrid plan assumes the rule detector already handles:

- Top signing-page continuation rows.
- Business/contact/account fields below a `签署页` title.
- Business/contact-only pages after previous-page `以下无正文`.
- Supply/demand signing tables.
- Mid-page signing areas after terminal signing clauses.
- Semantic gating for signing bbox overlap fallback.

---

## File Structure

- Modify: `backend/pyproject.toml`
  - Add `opencv-python-headless` to backend dependencies.
- Modify: `backend/requirements.txt`
  - Add `opencv-python-headless` for non-uv installs.
- Modify: `backend/app/config.py`
  - Add local visual backend and OpenCV threshold/config flags.
  - Validate visual backend values.
- Modify: `.env.example`
  - Document local OpenCV visual defaults and remote detector opt-in.
- Modify: `backend/app/services/signing_region/models.py`
  - Add no required new model classes; existing `VisualDetection` and `VisualDetectionResult` are sufficient.
- Modify: `backend/app/services/signing_region/visual.py`
  - Add `OpenCvVisualSignatureDetector`.
  - Keep `OpenCvSigningRegionFingerprinter` unchanged except for shared render helpers if useful.
- Modify: `backend/app/services/pipeline_stages.py`
  - Use OpenCV as the default visual detector when visual backend is `opencv`.
  - Record `visual_backend`, `opencv_available`, and unmatched `visual_candidates` in debug output.
  - Promote only rule-backed low-confidence candidates when an OpenCV detection overlaps them.
- Modify: `backend/app/services/signing_region/block_detector.py`
  - Ensure low-confidence candidate debug entries include `bbox`, `score`, `reasons`, `text`, and `block_ids`.
- Modify: `backend/tests/test_config.py`
  - Add config validation tests for visual backend and OpenCV defaults.
- Modify: `backend/tests/test_signing_region_visual.py`
  - Add OpenCV unavailable, red-seal, handwriting-density, and region-analysis tests.
- Modify: `backend/tests/test_signing_region_pipeline.py`
  - Add pipeline tests proving OpenCV is default, debug candidates are recorded, visual-only detections do not create final diffs, and visual detections can promote rule-backed candidates.

---

### Task 1: Add Visual Backend Configuration

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Modify: `backend/tests/test_config.py`

- [ ] **Step 1: Add failing config tests**

Append these tests to `backend/tests/test_config.py`:

```python
def test_signing_visual_defaults_to_local_opencv_backend() -> None:
    app_settings = Settings()

    assert app_settings.signing_visual_enabled is True
    assert app_settings.signing_visual_backend == "opencv"
    assert app_settings.signing_opencv_detect_red_seal is True
    assert app_settings.signing_opencv_detect_handwriting is True
    assert app_settings.signing_opencv_scan_candidate_pages is True
    assert app_settings.signing_opencv_min_confidence == 0.55


def test_signing_visual_backend_accepts_supported_values() -> None:
    assert Settings(signing_visual_backend="opencv").signing_visual_backend == "opencv"
    assert Settings(signing_visual_backend="remote").signing_visual_backend == "remote"
    assert Settings(signing_visual_backend="local").signing_visual_backend == "local"
    assert Settings(signing_visual_backend="off").signing_visual_backend == "off"


def test_signing_visual_backend_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="SIGNING_VISUAL_BACKEND"):
        Settings(signing_visual_backend="vlm")
```

- [ ] **Step 2: Run config tests and verify they fail**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_config.py::test_signing_visual_defaults_to_local_opencv_backend backend/tests/test_config.py::test_signing_visual_backend_accepts_supported_values backend/tests/test_config.py::test_signing_visual_backend_rejects_unknown_value -q
```

Expected:

```text
FAILED ... AttributeError or validation failure for signing_visual_backend
```

- [ ] **Step 3: Add settings fields**

In `backend/app/config.py`, replace the signing visual detection settings block:

```python
    signing_visual_detector_url: str = ""
    signing_visual_detector_timeout: int = 30
    signing_visual_local_model_path: str = ""
    signing_visual_enabled: bool = True
```

with:

```python
    signing_visual_detector_url: str = ""
    signing_visual_detector_timeout: int = 30
    signing_visual_local_model_path: str = ""
    signing_visual_enabled: bool = True
    signing_visual_backend: str = "opencv"
    signing_opencv_detect_red_seal: bool = True
    signing_opencv_detect_handwriting: bool = True
    signing_opencv_scan_candidate_pages: bool = True
    signing_opencv_min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    signing_opencv_max_candidate_pages: int = Field(default=6, ge=1, le=20)
```

Add this validator after `validate_layout_analysis_mode`:

```python
    @field_validator("signing_visual_backend", mode="before")
    @classmethod
    def validate_signing_visual_backend(cls, value: Any) -> str:
        backend = str(value or "opencv").strip().lower()
        if backend not in {"opencv", "remote", "local", "off"}:
            raise ValueError("SIGNING_VISUAL_BACKEND must be one of: opencv, remote, local, off")
        return backend
```

- [ ] **Step 4: Document environment defaults**

Insert this block into `.env.example` after `DOCUMENT_UNDERSTANDING_ENABLED=true`:

```dotenv

# ---------------------------------------------------------------------------
# 签署页视觉辅助
# ---------------------------------------------------------------------------

# 默认使用本地 OpenCV 做视觉辅助，不依赖外部检测服务。
# 可选值：opencv、remote、local、off。
SIGNING_VISUAL_BACKEND=opencv

# 是否启用签署区视觉辅助。关闭后仍保留规则/OCR/版面结构识别。
SIGNING_VISUAL_ENABLED=true

# OpenCV 辅助检测红章、手写痕迹和候选页。视觉单独命中只进入低置信候选。
SIGNING_OPENCV_DETECT_RED_SEAL=true
SIGNING_OPENCV_DETECT_HANDWRITING=true
SIGNING_OPENCV_SCAN_CANDIDATE_PAGES=true
SIGNING_OPENCV_MIN_CONFIDENCE=0.55
SIGNING_OPENCV_MAX_CANDIDATE_PAGES=6

# 只有 SIGNING_VISUAL_BACKEND=remote 时才配置远程视觉检测服务。
SIGNING_VISUAL_DETECTOR_URL=
SIGNING_VISUAL_DETECTOR_TIMEOUT=30

# 预留本地模型适配器；当前默认链路不依赖该配置。
SIGNING_VISUAL_LOCAL_MODEL_PATH=
```

- [ ] **Step 5: Run config tests**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_config.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 6: Commit config changes**

```bash
git add backend/app/config.py .env.example backend/tests/test_config.py
git commit -m "feat: configure local signing visual backend"
```

---

### Task 2: Add OpenCV Dependency

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add dependency entries**

In `backend/pyproject.toml`, add `opencv-python-headless` to `[project].dependencies` after `pymupdf`:

```toml
    "pymupdf",
    "opencv-python-headless",
```

In `backend/requirements.txt`, add:

```text
opencv-python-headless
```

immediately after:

```text
pymupdf
```

- [ ] **Step 2: Install dependencies if needed**

Run:

```bash
cd backend && uv sync
```

Expected:

```text
Resolved ... packages
```

If the environment does not use `uv sync`, run:

```bash
cd backend && uv pip install -e .
```

Expected:

```text
Successfully installed ...
```

- [ ] **Step 3: Verify OpenCV import**

Run:

```bash
env -u VIRTUAL_ENV uv run python -c "import cv2; print(cv2.__version__)"
```

Expected:

```text
<opencv version>
```

- [ ] **Step 4: Commit dependency changes**

```bash
git add backend/pyproject.toml backend/requirements.txt backend/uv.lock
git commit -m "build: add opencv dependency for signing detection"
```

---

### Task 3: Implement Local OpenCV Visual Detector

**Files:**
- Modify: `backend/app/services/signing_region/visual.py`
- Modify: `backend/tests/test_signing_region_visual.py`

- [ ] **Step 1: Add OpenCV detector tests**

Append these tests to `backend/tests/test_signing_region_visual.py`:

```python
def test_opencv_visual_detector_reports_unavailable_when_pdf_missing() -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    detector = OpenCvVisualSignatureDetector()

    result = detector.detect(Path("missing.pdf"), [_region()], task_id="task-1")

    assert result.available is False
    assert result.error == "pdf_missing"
    assert result.detections == []


def test_opencv_visual_detector_detects_red_seal_in_region(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    image[80:155, 90:170] = [0, 0, 255]

    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.model_name == "opencv"
    assert len(result.detections) == 1
    assert result.detections[0].label == "seal"
    assert result.detections[0].confidence >= 0.55
    assert result.detections[0].raw_data["red_pixel_ratio"] > 0


def test_opencv_visual_detector_detects_handwriting_density(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)
    for offset in range(0, 80, 8):
        image[100 + offset : 104 + offset, 40 + offset : 150 + offset] = [20, 20, 20]

    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert len(result.detections) == 1
    assert result.detections[0].label == "signature"
    assert result.detections[0].confidence >= 0.55
    assert result.detections[0].raw_data["dark_pixel_ratio"] > 0


def test_opencv_visual_detector_keeps_blank_region_as_no_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import numpy as np

    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    image = np.full((240, 240, 3), 255, dtype=np.uint8)

    detector = OpenCvVisualSignatureDetector()
    monkeypatch.setattr(detector, "_render_region", lambda *_args: image)

    result = detector.detect(pdf_path, [_region()], task_id="task-1")

    assert result.available is True
    assert result.detections == []
```

- [ ] **Step 2: Run visual tests and verify they fail**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_visual.py::test_opencv_visual_detector_reports_unavailable_when_pdf_missing backend/tests/test_signing_region_visual.py::test_opencv_visual_detector_detects_red_seal_in_region backend/tests/test_signing_region_visual.py::test_opencv_visual_detector_detects_handwriting_density backend/tests/test_signing_region_visual.py::test_opencv_visual_detector_keeps_blank_region_as_no_detection -q
```

Expected:

```text
FAILED ... cannot import name 'OpenCvVisualSignatureDetector'
```

- [ ] **Step 3: Add OpenCV detector implementation**

In `backend/app/services/signing_region/visual.py`, add this class before `OpenCvSigningRegionFingerprinter`:

```python
class OpenCvVisualSignatureDetector:
    def __init__(
        self,
        *,
        detect_red_seal: bool | None = None,
        detect_handwriting: bool | None = None,
        min_confidence: float | None = None,
    ) -> None:
        self.detect_red_seal = (
            settings.signing_opencv_detect_red_seal
            if detect_red_seal is None
            else detect_red_seal
        )
        self.detect_handwriting = (
            settings.signing_opencv_detect_handwriting
            if detect_handwriting is None
            else detect_handwriting
        )
        self.min_confidence = (
            settings.signing_opencv_min_confidence
            if min_confidence is None
            else min_confidence
        )

    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        del task_id
        if not pdf_path.exists():
            return VisualDetectionResult(available=False, model_name="opencv", error="pdf_missing")
        dependencies = self._dependencies()
        if dependencies is None:
            return VisualDetectionResult(available=False, model_name="opencv", error="opencv_unavailable")

        detections: list[VisualDetection] = []
        for region in regions:
            image = self._render_region(pdf_path, region)
            if image is None:
                continue
            detection = self._detect_region(region, image)
            if detection is not None and detection.confidence >= self.min_confidence:
                detections.append(detection)

        return VisualDetectionResult(
            available=True,
            model_name="opencv",
            detections=detections,
        )

    def _detect_region(self, region: SigningRegion, image) -> VisualDetection | None:
        metrics = self._visual_metrics(image)
        label = ""
        confidence = 0.0
        reasons: list[str] = []

        if self.detect_red_seal and metrics["red_pixel_ratio"] >= 0.01:
            label = "seal"
            confidence = min(0.95, 0.55 + metrics["red_pixel_ratio"] * 8.0)
            reasons.append("red_seal_pixels")

        if self.detect_handwriting and metrics["dark_pixel_ratio"] >= 0.015:
            handwriting_confidence = min(0.9, 0.5 + metrics["dark_pixel_ratio"] * 5.0)
            if handwriting_confidence > confidence:
                label = "signature"
                confidence = handwriting_confidence
            reasons.append("dark_stroke_density")

        if not label:
            return None

        raw_data = {
            **metrics,
            "reasons": reasons,
            "source_region_id": region.region_id,
        }
        return VisualDetection(
            page_no=region.page_no,
            bbox=region.bbox,
            label=label,
            confidence=round(confidence, 3),
            model_name="opencv",
            raw_data=raw_data,
        )

    def _visual_metrics(self, image) -> dict[str, float]:
        dependencies = self._dependencies()
        if dependencies is None:
            return {"red_pixel_ratio": 0.0, "dark_pixel_ratio": 0.0}
        cv2, np = dependencies
        if image.size == 0:
            return {"red_pixel_ratio": 0.0, "dark_pixel_ratio": 0.0}

        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        lower_red_1 = np.array([0, 50, 40])
        upper_red_1 = np.array([12, 255, 255])
        lower_red_2 = np.array([170, 50, 40])
        upper_red_2 = np.array([180, 255, 255])
        red_mask = cv2.inRange(hsv, lower_red_1, upper_red_1) | cv2.inRange(hsv, lower_red_2, upper_red_2)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        dark_mask = gray < 80

        total = max(1, image.shape[0] * image.shape[1])
        return {
            "red_pixel_ratio": float(np.count_nonzero(red_mask) / total),
            "dark_pixel_ratio": float(np.count_nonzero(dark_mask) / total),
        }

    def _render_region(self, pdf_path: Path, region: SigningRegion):
        try:
            import fitz
        except Exception:
            return None

        doc = None
        try:
            doc = fitz.open(pdf_path)
            if region.page_no < 1 or region.page_no > len(doc):
                return None
            page = doc[region.page_no - 1]
            rect = fitz.Rect(region.bbox.x0, region.bbox.y0, region.bbox.x1, region.bbox.y1)
            pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            dependencies = self._dependencies()
            if dependencies is None:
                return None
            _cv2, np = dependencies
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                image = image[:, :, :3]
            return image[:, :, ::-1].copy()
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    @staticmethod
    def _dependencies():
        try:
            import cv2
            import numpy as np
        except Exception:
            return None
        return cv2, np
```

- [ ] **Step 4: Run visual tests**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_visual.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 5: Commit OpenCV detector**

```bash
git add backend/app/services/signing_region/visual.py backend/tests/test_signing_region_visual.py
git commit -m "feat: add local opencv signing visual detector"
```

---

### Task 4: Use OpenCV as the Default Visual Detector

**Files:**
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: Add default detector pipeline test**

Append this test to `backend/tests/test_signing_region_pipeline.py`:

```python
def test_signing_region_stage_uses_opencv_detector_by_default(monkeypatch, tmp_path: Path) -> None:
    from app.services.signing_region.visual import OpenCvVisualSignatureDetector

    monkeypatch.setattr("app.config.settings.signing_visual_backend", "opencv")
    monkeypatch.setattr("app.config.settings.signing_visual_enabled", True)

    stage = SigningRegionStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"))

    assert isinstance(stage.visual_detector, OpenCvVisualSignatureDetector)
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    stage.block_detector = _NoCandidateDetector()
    stage.execute(ctx)

    assert ctx.signing_region_debug["configuration"]["visual_backend"] == "opencv"
    assert ctx.signing_region_debug["configuration"]["visual_detector"] == "OpenCvVisualSignatureDetector"
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_uses_opencv_detector_by_default -q
```

Expected:

```text
FAILED ... visual_detector is None or configuration lacks visual_backend
```

- [ ] **Step 3: Import OpenCV detector**

In `backend/app/services/pipeline_stages.py`, update the signing visual import block so it includes `OpenCvVisualSignatureDetector`:

```python
from app.services.signing_region.visual import (
    LocalCpuVisualSignatureDetector,
    OpenCvSigningRegionFingerprinter,
    OpenCvVisualSignatureDetector,
    RemoteVisualSignatureDetector,
    VisualSignatureDetector,
)
```

- [ ] **Step 4: Update `_default_visual_detector`**

Replace `_default_visual_detector` with:

```python
    @staticmethod
    def _default_visual_detector() -> VisualSignatureDetector | None:
        backend = settings.signing_visual_backend
        if backend == "off":
            return None
        if backend == "remote":
            return RemoteVisualSignatureDetector()
        if backend == "local":
            return LocalCpuVisualSignatureDetector()
        if backend == "opencv":
            return OpenCvVisualSignatureDetector()
        if settings.signing_visual_detector_url.strip():
            return RemoteVisualSignatureDetector()
        if settings.signing_visual_local_model_path.strip():
            return LocalCpuVisualSignatureDetector()
        return OpenCvVisualSignatureDetector()
```

- [ ] **Step 5: Update debug configuration**

In `_debug_configuration`, add `visual_backend` and `opencv_available`:

```python
    def _debug_configuration(self) -> dict[str, Any]:
        return {
            "visual_enabled": self.visual_enabled,
            "visual_backend": settings.signing_visual_backend,
            "visual_confidence_threshold": self.visual_confidence_threshold,
            "visual_detector": type(self.visual_detector).__name__ if self.visual_detector is not None else "",
            "visual_detector_url_configured": bool(settings.signing_visual_detector_url.strip()),
            "visual_local_model_configured": bool(settings.signing_visual_local_model_path.strip()),
            "visual_detector_timeout": settings.signing_visual_detector_timeout,
            "visual_fingerprinter": type(self.visual_fingerprinter).__name__ if self.visual_fingerprinter is not None else "",
            "opencv_available": OpenCvVisualSignatureDetector._dependencies() is not None,
        }
```

- [ ] **Step 6: Run pipeline test**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_uses_opencv_detector_by_default -q
```

Expected:

```text
... passed
```

- [ ] **Step 7: Commit default detector wiring**

```bash
git add backend/app/services/pipeline_stages.py backend/tests/test_signing_region_pipeline.py
git commit -m "feat: use opencv as default signing visual detector"
```

---

### Task 5: Record Visual Candidates Without Creating Visual-Only Diffs

**Files:**
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: Add visual-only debug test**

Append this test to `backend/tests/test_signing_region_pipeline.py`:

```python
def test_signing_region_stage_records_visual_only_candidates_without_final_diff(tmp_path: Path) -> None:
    class _VisualOnlyDetector:
        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            detections = [
                VisualDetection(
                    page_no=1,
                    bbox=BBox(x0=80, y0=650, x1=180, y1=760),
                    label="seal",
                    confidence=0.88,
                    model_name="opencv",
                    raw_data={"reasons": ["red_seal_pixels"]},
                )
            ]
            return VisualDetectionResult(available=True, model_name="opencv", detections=detections)

    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_VisualOnlyDetector(),
        visual_enabled=True,
    )
    stage.block_detector = _NoCandidateDetector()
    stage.extractor = type(
        "_NoFallbackExtractor",
        (),
        {
            "extract_from_blocks": lambda self, blocks: [],
            "extract": lambda self, document: [],
        },
    )()

    stage.execute(ctx)

    assert ctx.signing_region_diffs == []
    assert ctx.signing_region_debug["visual_adapter_status"]["original"]["available"] is True
    assert ctx.signing_region_debug["visual_candidates"]["original"][0]["label"] == "seal"
    assert ctx.signing_region_debug["visual_candidates"]["original"][0]["used_for_promotion"] is False
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_records_visual_only_candidates_without_final_diff -q
```

Expected:

```text
FAILED ... KeyError: 'visual_candidates'
```

- [ ] **Step 3: Add visual candidate extraction**

In `_collect_visual`, initialize `_visual_candidates`:

```python
            "_visual_candidates": [],
```

After `detection_result.available` handling, add:

```python
        status["_visual_candidates"] = [
            {
                "page_no": detection.page_no,
                "bbox": detection.bbox.model_dump(mode="json"),
                "label": detection.label,
                "confidence": detection.confidence,
                "model_name": detection.model_name or detection_result.model_name,
                "reasons": detection.raw_data.get("reasons", []),
                "used_for_promotion": False,
            }
            for detection in detection_result.detections
        ]
```

In `ctx.signing_region_debug`, add:

```python
            "visual_candidates": {
                "original": visual_status["original"].get("_visual_candidates", []),
                "compare": visual_status["compare"].get("_visual_candidates", []),
            },
```

- [ ] **Step 4: Ensure visual status hides private keys only**

Keep `_debug_visual_status` unchanged; it already strips keys beginning with `_`, so `_visual_candidates` stays out of `visual_adapter_status` and appears only in the top-level debug field added above.

- [ ] **Step 5: Run test**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_records_visual_only_candidates_without_final_diff -q
```

Expected:

```text
... passed
```

- [ ] **Step 6: Commit visual candidate debug**

```bash
git add backend/app/services/pipeline_stages.py backend/tests/test_signing_region_pipeline.py
git commit -m "feat: record signing visual candidates"
```

---

### Task 6: Promote Only Rule-Backed Low-Confidence Candidates

**Files:**
- Modify: `backend/app/services/signing_region/block_detector.py`
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: Ensure low-confidence candidates expose bbox**

In `backend/app/services/signing_region/block_detector.py`, update the low-confidence candidate payload inside `_detect_page_blocks`:

```python
                result.low_confidence_candidates.append({
                    "page_no": page.page_no,
                    "block_ids": [block.block_id for block in cluster],
                    "bbox": bbox.model_dump(mode="json"),
                    "score": score,
                    "reasons": reasons,
                    "text": text[:200],
                })
```

- [ ] **Step 2: Add promotion test**

Append this test to `backend/tests/test_signing_region_pipeline.py`:

```python
def test_signing_region_stage_promotes_rule_backed_candidate_with_visual_support(tmp_path: Path) -> None:
    class _LowConfidenceDetector:
        def detect(self, _document: Document) -> SigningBlockDetectionResult:
            return SigningBlockDetectionResult(
                low_confidence_candidates=[
                    {
                        "page_no": 2,
                        "block_ids": ["candidate"],
                        "bbox": {"x0": 80, "y0": 620, "x1": 520, "y1": 760},
                        "score": 0.42,
                        "reasons": ["signing_page_context", "business_signing_form_fields"],
                        "text": "签署页\n地址：A\n联系人：B\n电话：C\n统一社会信用代码：D",
                    }
                ]
            )

    class _VisualDetector:
        def detect(self, _pdf_path: Path, regions, _task_id: str) -> VisualDetectionResult:
            assert regions
            region = regions[0]
            return VisualDetectionResult(
                available=True,
                model_name="opencv",
                detections=[
                    VisualDetection(
                        page_no=region.page_no,
                        bbox=region.bbox,
                        label="visual_area",
                        confidence=0.82,
                        model_name="opencv",
                        raw_data={"reasons": ["table_or_stroke_density"]},
                    )
                ],
            )

    document = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="candidate",
                        page_no=2,
                        text="签署页\n地址：A\n联系人：B\n电话：C\n统一社会信用代码：D",
                        bbox=BBox(x0=80, y0=620, x1=520, y1=760),
                    )
                ],
            )
        ],
    )
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=document, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=document.model_copy(deep=True), extractor_used="test")
    stage = SigningRegionStage(
        artifact_store=_TestArtifactStore(tmp_path / "artifacts"),
        visual_detector=_VisualDetector(),
        visual_enabled=True,
    )
    stage.block_detector = _LowConfidenceDetector()

    stage.execute(ctx)

    assert {block.page_no for block in ctx.signing_blocks_original} == {2}
    assert ctx.signing_blocks_original[0].confidence_level == "medium"
    assert "visual_candidate_promoted" in ctx.signing_blocks_original[0].confidence_reasons
    assert ctx.signing_region_debug["visual_candidates"]["original"][0]["used_for_promotion"] is True
```

- [ ] **Step 3: Run test and verify it fails**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_promotes_rule_backed_candidate_with_visual_support -q
```

Expected:

```text
FAILED ... signing_blocks_original is empty
```

- [ ] **Step 4: Import signing block models**

In `backend/app/services/pipeline_stages.py`, ensure these imports are available from `app.services.signing_region.models`:

```python
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningRegion,
```

- [ ] **Step 5: Add candidate region builder**

Add this method to `SigningRegionStage` before `_collect_visual`:

```python
    @staticmethod
    def _candidate_regions_from_low_confidence(candidates: list[dict[str, Any]]) -> list[SigningRegion]:
        regions: list[SigningRegion] = []
        for index, candidate in enumerate(candidates, start=1):
            bbox_payload = candidate.get("bbox")
            if not isinstance(bbox_payload, dict):
                continue
            try:
                bbox = BBox(**bbox_payload)
                page_no = int(candidate.get("page_no", 0))
            except (TypeError, ValueError):
                continue
            if page_no <= 0:
                continue
            regions.append(SigningRegion(
                region_id=f"LC-{page_no}-{index}",
                page_no=page_no,
                bbox=bbox,
                confidence=float(candidate.get("score") or 0.0),
                confidence_reasons=list(candidate.get("reasons") or []),
            ))
        return regions
```

- [ ] **Step 6: Pass candidate regions into visual collection**

Before `visual_status = { ... }`, add:

```python
        original_candidate_regions = self._candidate_regions_from_low_confidence(
            original_structure.low_confidence_candidates
        )
        compare_candidate_regions = self._candidate_regions_from_low_confidence(
            compare_structure.low_confidence_candidates
        )
```

Update both `_collect_visual` calls to pass candidate regions:

```python
                candidate_regions=original_candidate_regions,
```

and:

```python
                candidate_regions=compare_candidate_regions,
```

Update `_collect_visual` signature:

```python
        candidate_regions: list[SigningRegion] | None = None,
```

At the start of `_collect_visual`, after status initialization, add:

```python
        candidate_regions = candidate_regions or []
        detector_regions = [*regions, *candidate_regions]
```

Replace:

```python
        detection_result = self._detect_visual(pdf_path, regions, task_id)
```

with:

```python
        detection_result = self._detect_visual(pdf_path, detector_regions, task_id)
```

- [ ] **Step 7: Add promotion method**

Add this method before `_candidate_regions_from_low_confidence`:

```python
    def _promote_visual_supported_candidates(
        self,
        structure: SigningBlockDetectionResult,
        visual_status: dict[str, Any],
    ) -> None:
        promoted: list[SigningBlock] = []
        candidates = structure.low_confidence_candidates
        visual_candidates = visual_status.get("_visual_candidates", [])
        for candidate in candidates:
            if float(candidate.get("score") or 0.0) < 0.35:
                continue
            bbox_payload = candidate.get("bbox")
            if not isinstance(bbox_payload, dict):
                continue
            try:
                bbox = BBox(**bbox_payload)
                page_no = int(candidate.get("page_no", 0))
            except (TypeError, ValueError):
                continue

            matched_visual = self._matching_visual_candidate(page_no, bbox, visual_candidates)
            if matched_visual is None:
                continue

            reasons = list(candidate.get("reasons") or [])
            if not self._candidate_has_rule_support(reasons):
                continue

            matched_visual["used_for_promotion"] = True
            score = min(0.69, max(0.5, float(candidate.get("score") or 0.0) + 0.15))
            promoted.append(SigningBlock(
                block_id=f"SB-VISUAL-{page_no}-{len(promoted) + 1}",
                page_no=page_no,
                bbox=bbox,
                block_role=SigningBlockRole.UNKNOWN,
                confidence=round(score, 2),
                confidence_level=SigningBlockConfidenceLevel.MEDIUM,
                confidence_reasons=[*reasons, "visual_candidate_promoted"],
                source_block_ids=list(candidate.get("block_ids") or []),
                text=str(candidate.get("text") or ""),
                exclude_from_clause_diff=False,
            ))

        structure.blocks.extend(promoted)

    @staticmethod
    def _candidate_has_rule_support(reasons: list[str]) -> bool:
        return any(
            reason in reasons
            for reason in [
                "signing_page_context",
                "business_signing_form_fields",
                "page_signing_context_business_fields",
                "previous_page_signing_context_business_fields",
                "terminal_signing_clause_context",
                "paired_parties",
            ]
        )

    @staticmethod
    def _matching_visual_candidate(
        page_no: int,
        bbox: BBox,
        visual_candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for candidate in visual_candidates:
            if int(candidate.get("page_no") or 0) != page_no:
                continue
            candidate_bbox = candidate.get("bbox")
            if not isinstance(candidate_bbox, dict):
                continue
            try:
                visual_bbox = BBox(**candidate_bbox)
            except (TypeError, ValueError):
                continue
            if SigningRegionStage._overlap_ratio(bbox, visual_bbox) >= 0.2:
                return candidate
        return None
```

- [ ] **Step 8: Call promotion before region extraction is finalized**

After `visual_status = {...}` and before `_apply_visual_enrichment_when_comparable`, add:

```python
        self._promote_visual_supported_candidates(original_structure, visual_status["original"])
        self._promote_visual_supported_candidates(compare_structure, visual_status["compare"])
        if original_candidate_regions or compare_candidate_regions:
            original_regions, original_legacy_fallback = self._extract_regions_from_structure(
                extractions.original.document,
                original_structure,
            )
            compare_regions, compare_legacy_fallback = self._extract_regions_from_structure(
                extractions.compare.document,
                compare_structure,
            )
```

This second extraction is intentional: promotion appends `SigningBlock` objects after the first extraction attempt.

- [ ] **Step 9: Run promotion test**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_region_stage_promotes_rule_backed_candidate_with_visual_support -q
```

Expected:

```text
... passed
```

- [ ] **Step 10: Run signing pipeline tests**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 11: Commit visual fusion**

```bash
git add backend/app/services/signing_region/block_detector.py backend/app/services/pipeline_stages.py backend/tests/test_signing_region_pipeline.py
git commit -m "feat: fuse opencv signing candidates with rule detector"
```

---

### Task 7: Verify Hybrid Signing Detection

**Files:**
- No code files.

- [ ] **Step 1: Run focused signing tests**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py backend/tests/test_signing_region_visual.py backend/tests/test_signing_region_pipeline.py backend/tests/test_signing_clause_document.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 2: Run backend syntax check**

Run:

```bash
cd backend && python -m compileall app tests
```

Expected:

```text
no syntax errors
```

- [ ] **Step 3: Run backend lint check**

Run:

```bash
cd backend && python -m ruff check .
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected:

```text
no output
```

- [ ] **Step 5: Rerun reported signing tasks**

Only run this step when it is acceptable to rewrite local task artifacts. Use the rerun commands from:

```text
docs/superpowers/plans/2026-07-07-signing-region-continuation-bbox.md
```

At minimum rerun:

```text
dc69d8f4-9b14-420a-9e8a-161c6c5646a5
75db9356-c540-4a02-919e-44c0dd8f5f2b
3154a3a4-109f-4927-adec-e95c63e6ea31
6219c7d7-8df7-4b74-b57c-44edccad2b5f
3627991f-2426-4a82-ade2-d02237954df2
```

Expected:

```text
Known signing pages are detected on both sides where the document content exists.
The 14.2 clause text is not excluded by signing bbox overlap.
debug/signing_region.json includes visual_backend, opencv_available, visual_candidates, and promotion status.
```

- [ ] **Step 6: Final commit if verification changed lockfiles or docs**

```bash
git status --short
git add backend/uv.lock
git commit -m "test: verify hybrid signing detection"
```

Skip the final commit if `git status --short` shows no verification-related file changes.

---

## Risk Controls

- Do not add VLM calls, SDKs, API keys, or model prompts in this implementation.
- Do not let OpenCV-only detections create final `SigningRegion` diffs.
- Do not make `SIGNING_VISUAL_DETECTOR_URL` mandatory.
- Keep OpenCV unavailable as a soft failure: record `opencv_unavailable`, then continue rule-only processing.
- Promoted visual candidates must have a rule-backed low-confidence candidate and overlapping visual detection.
- Promoted candidates should be medium confidence and `exclude_from_clause_diff=False` unless later tests prove the region is safe to strip from clause text.
- Keep exact bbox evidence and source block ids in debug output for all promotions.

## Self-Review

- Spec coverage: The plan covers the confirmed architecture: rule baseline, local OpenCV detector, no default VLM, default local backend config, debug output, conservative fusion, and regression verification.
- Placeholder scan: There are no placeholder markers, vague tasks, or open-ended implementation steps.
- Type consistency: New code uses existing `VisualDetection`, `VisualDetectionResult`, `SigningRegion`, `SigningBlock`, `BBox`, and `SigningBlockDetectionResult` models.
- Test coverage: Config tests cover defaults and validation; visual tests cover unavailable, red seal, handwriting density, and blank regions; pipeline tests cover default OpenCV wiring, visual-only debug behavior, and rule-backed visual promotion.
