# 独立签章区对比实施计划

> **给智能代理执行者：** REQUIRED SUB-SKILL: 使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项实施。本计划使用复选框（`- [ ]`）跟踪步骤。

**目标：** 基于现有 PaddleOCR / PP-Structure 主链路，实现独立 `signing_region` 签章区对比，聚合印章、签名、签署日期、签章标签、签署表格和视觉证据，并默认隐藏被覆盖的旧签章碎片差异。

**架构：** 新增 `backend/app/services/signing_region/` 子系统，插入独立 `SigningRegionStage` 到 `PreClauseDiffStage` 之后、`SplitStage` 之前。签章区提取只消费现有 `Document`、已有预条款差异和可选视觉适配器，不替换 OCR/layout 主链路。

**技术栈：** Python 3、Pydantic、FastAPI 表单参数、现有 pipeline stage、PyMuPDF/OpenCV 可选视觉指纹、React/Vite 前端、pytest、Vitest。

---

## 文件结构

新增文件：

- `backend/app/services/signing_region/__init__.py`：导出签章区服务入口。
- `backend/app/services/signing_region/models.py`：内部模型，包括签章区、签章元素、视觉检测、区域对比和覆盖映射。
- `backend/app/services/signing_region/extractor.py`：从现有 `Document` 块提取签章区候选。
- `backend/app/services/signing_region/visual.py`：视觉指纹、远程签名检测适配器、本地未配置适配器。
- `backend/app/services/signing_region/matcher.py`：原版/新版签章区匹配。
- `backend/app/services/signing_region/comparator.py`：按印章、签名、日期、标签、表格和视觉信号对比签章区。
- `backend/app/services/signing_region/diff_builder.py`：把区域对比结果转换为 `DiffItem(source_type="signing_region")`。
- `backend/app/services/signing_region/coverage.py`：计算被签章区覆盖的旧 `seal/table/metadata/header_footer` 差异。
- `backend/tests/test_signing_region_extractor.py`：签章区提取规则测试。
- `backend/tests/test_signing_region_visual.py`：视觉适配器和指纹降级测试。
- `backend/tests/test_signing_region_comparison.py`：匹配、对比、diff builder 测试。
- `backend/tests/test_signing_region_pipeline.py`：pipeline 集成和覆盖旧差异测试。

修改文件：

- `backend/app/models.py`：扩展 `DiffSourceType` 和 `CompareOptions`。
- `backend/app/config.py`：增加视觉签名检测配置。
- `backend/app/services/pipeline.py`：扩展 `PipelineContext`。
- `backend/app/services/pipeline_stages.py`：新增 `SigningRegionStage`，调整 `ClauseDiffStage` 和 `SummaryStage`。
- `backend/app/services/compare_debug.py`：新增 `write_signing_region(...)`。
- `backend/app/api.py`：接收 `signing_region_mode`。
- `backend/app/services/report_generator.py`：增加签章区来源标签和排序。
- `frontend/src/types.ts`：扩展比较选项类型和 source type 兼容。
- `frontend/src/lib/api.ts`：传递 `signing_region_mode`。
- `frontend/src/pages/ResultPage.tsx`：增加签章区分组。
- `frontend/src/components/PdfDocumentViewer.tsx`：增加签章区高亮样式。
- `frontend/src/pages/UploadPage.tsx`：保留文案，但确保提交语义和后端一致。
- 对应前端测试文件补充断言。

## 任务 1：公共模型与配置

**文件：**
- 修改：`backend/app/models.py`
- 修改：`backend/app/config.py`
- 修改：`frontend/src/types.ts`
- 测试：`backend/tests/test_api.py`
- 测试：`frontend/src/lib/api.test.ts`

- [ ] **步骤 1：写后端模型失败测试**

在 `backend/tests/test_api.py` 增加：

```python
def test_compare_options_support_signing_region_mode() -> None:
    from app.models import CompareOptions

    options = CompareOptions(signing_region_mode="off")

    assert options.signing_region_mode == "off"


def test_compare_options_default_signing_region_mode_full() -> None:
    from app.models import CompareOptions

    options = CompareOptions()

    assert options.signing_region_mode == "full"
```

- [ ] **步骤 2：运行失败测试**

运行：

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_api.py::test_compare_options_support_signing_region_mode backend/tests/test_api.py::test_compare_options_default_signing_region_mode_full -q
```

期望：失败，错误包含 `signing_region_mode`，原因是 `CompareOptions` 尚未声明该字段。

- [ ] **步骤 3：修改 `backend/app/models.py`**

把 `DiffSourceType` 改为包含 `signing_region`：

```python
DiffSourceType = Literal["clause", "header_footer", "table", "metadata", "seal", "page", "signing_region"]
```

把 `CompareOptions` 改为：

```python
class CompareOptions(BaseModel):
    ignore_punctuation: bool = False
    ignore_headers_footers: bool = False
    ignore_stamps: bool = False
    signing_region_mode: Literal["full", "off"] = "full"
```

- [ ] **步骤 4：增加后端配置**

在 `backend/app/config.py` 的 `Settings` 中增加：

```python
signing_visual_detector_url: str = ""
signing_visual_detector_timeout: int = 30
signing_visual_local_model_path: str = ""
signing_visual_enabled: bool = True
```

- [ ] **步骤 5：扩展前端类型**

在 `frontend/src/types.ts` 修改：

```typescript
export type SigningRegionMode = "full" | "off";

export interface CompareContractOptions {
  ignoreStamps: boolean;
  ignoreHeadersFooters: boolean;
  signingRegionMode?: SigningRegionMode;
}
```

- [ ] **步骤 6：运行模型测试**

运行：

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_api.py::test_compare_options_support_signing_region_mode backend/tests/test_api.py::test_compare_options_default_signing_region_mode_full -q
```

期望：`2 passed`。

- [ ] **步骤 7：提交**

```bash
git add backend/app/models.py backend/app/config.py frontend/src/types.ts backend/tests/test_api.py
git commit -m "feat: add signing region compare options"
```

## 任务 2：签章区内部模型

**文件：**
- 新建：`backend/app/services/signing_region/__init__.py`
- 新建：`backend/app/services/signing_region/models.py`
- 测试：`backend/tests/test_signing_region_extractor.py`

- [ ] **步骤 1：写模型实例化测试**

新建 `backend/tests/test_signing_region_extractor.py`，先加入：

```python
from app.models import BBox
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionRole,
)


def test_signing_region_model_holds_elements_and_reasons() -> None:
    element = SigningElement(
        element_id="E1",
        element_type=SigningElementType.SEAL,
        page_no=1,
        bbox=BBox(x0=100, y0=650, x1=180, y1=730),
        text="合同专用章",
        confidence=0.9,
        source="layout",
    )
    region = SigningRegion(
        region_id="SR-1-1",
        page_no=1,
        bbox=BBox(x0=80, y0=630, x1=220, y1=760),
        region_role=SigningRegionRole.PARTY_A,
        confidence=0.92,
        confidence_reasons=["seal_block", "signing_label"],
        elements=[element],
    )

    assert region.elements[0].element_type == SigningElementType.SEAL
    assert region.region_role == SigningRegionRole.PARTY_A
    assert "seal_block" in region.confidence_reasons
```

- [ ] **步骤 2：运行失败测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_extractor.py::test_signing_region_model_holds_elements_and_reasons -q
```

期望：失败，提示 `No module named app.services.signing_region`。

- [ ] **步骤 3：创建模型文件**

新建 `backend/app/services/signing_region/models.py`：

```python
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import BBox, DiffType


class SigningElementType(str, Enum):
    SEAL = "seal"
    SIGNATURE = "signature"
    LABEL = "label"
    DATE_FIELD = "date_field"
    SIGNING_TABLE = "signing_table"
    VISUAL_AREA = "visual_area"


class SigningRegionRole(str, Enum):
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    BOTH_PARTIES = "both_parties"
    SIGNATURE_PAGE = "signature_page"
    UNKNOWN = "unknown"


SigningElementSource = Literal["layout", "ocr", "visual_model", "visual_fingerprint", "inferred"]


class SigningElement(BaseModel):
    element_id: str
    element_type: SigningElementType
    page_no: int
    bbox: BBox
    text: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: SigningElementSource = "layout"
    visual_hash: str = ""
    model_name: str = ""
    raw_ref: dict[str, Any] = Field(default_factory=dict)


class SigningRegion(BaseModel):
    region_id: str
    page_no: int
    bbox: BBox
    region_role: SigningRegionRole = SigningRegionRole.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    elements: list[SigningElement] = Field(default_factory=list)


class VisualDetection(BaseModel):
    page_no: int
    bbox: BBox
    label: str = "signature"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model_name: str = ""
    raw_data: dict[str, Any] = Field(default_factory=dict)


class VisualDetectionResult(BaseModel):
    available: bool = True
    model_name: str = ""
    detections: list[VisualDetection] = Field(default_factory=list)
    error: str = ""


class SigningRegionComparison(BaseModel):
    comparison_id: str
    diff_type: DiffType | None = None
    original_region: SigningRegion | None = None
    compare_region: SigningRegion | None = None
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    seal_changes: list[dict[str, Any]] = Field(default_factory=list)
    signature_changes: list[dict[str, Any]] = Field(default_factory=list)
    date_changes: list[dict[str, Any]] = Field(default_factory=list)
    label_changes: list[dict[str, Any]] = Field(default_factory=list)
    table_changes: list[dict[str, Any]] = Field(default_factory=list)
    visual_changes: list[dict[str, Any]] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)


class SigningCoverageEntry(BaseModel):
    signing_region_diff_id: str
    covered_diff_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
```

新建 `backend/app/services/signing_region/__init__.py`：

```python
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionComparison,
    SigningRegionRole,
    VisualDetection,
    VisualDetectionResult,
)

__all__ = [
    "SigningElement",
    "SigningElementType",
    "SigningRegion",
    "SigningRegionComparison",
    "SigningRegionRole",
    "VisualDetection",
    "VisualDetectionResult",
]
```

- [ ] **步骤 4：运行模型测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_extractor.py::test_signing_region_model_holds_elements_and_reasons -q
```

期望：`1 passed`。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/signing_region backend/tests/test_signing_region_extractor.py
git commit -m "feat: add signing region models"
```

## 任务 3：签章区提取器

**文件：**
- 新建：`backend/app/services/signing_region/extractor.py`
- 修改：`backend/tests/test_signing_region_extractor.py`

- [ ] **步骤 1：写检测测试**

在 `backend/tests/test_signing_region_extractor.py` 追加：

```python
from app.models import Document, Page, TextBlock
from app.services.signing_region.extractor import SigningRegionExtractor


def _bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _block(
    block_id: str,
    text: str,
    bbox: BBox,
    *,
    block_type: str = "text",
    page_no: int = 1,
) -> TextBlock:
    return TextBlock(block_id=block_id, page_no=page_no, text=text, bbox=bbox, block_type=block_type)


def _document(blocks: list[TextBlock], *, page_no: int = 1) -> Document:
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(page_no=page_no, width=595, height=842, blocks=blocks)],
    )


def test_extractor_detects_seal_label_and_date_region() -> None:
    doc = _document([
        _block("label", "甲方（盖章）：", _bbox(60, 650, 170, 675)),
        _block("seal", "合同专用章", _bbox(80, 680, 190, 780), block_type="seal"),
        _block("date", "签订日期：2026年5月6日", _bbox(60, 790, 240, 815)),
    ])

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert {element.element_type.value for element in regions[0].elements} >= {"seal", "label", "date_field"}
    assert regions[0].confidence >= 0.7


def test_extractor_rejects_keyword_only_body_text() -> None:
    doc = _document([
        _block("body", "13.2 对本合同的修改以双方签章的书面协议为准。甲方应当配合乙方履行义务。", _bbox(60, 680, 520, 720)),
    ])

    regions = SigningRegionExtractor().extract(doc)

    assert regions == []


def test_extractor_rejects_single_bottom_keyword() -> None:
    doc = _document([
        _block("footer_word", "甲方", _bbox(60, 760, 90, 780)),
    ])

    regions = SigningRegionExtractor().extract(doc)

    assert regions == []


def test_extractor_detects_form_like_signature_page_without_seal_block() -> None:
    doc = _document([
        _block("context", "以下无正文，为签署页", _bbox(60, 520, 240, 545)),
        _block("party_a", "甲方：__________    乙方：__________", _bbox(60, 650, 460, 675)),
        _block("sign", "授权代表（签字）：__________", _bbox(60, 700, 280, 725)),
        _block("date", "日期：____年__月__日", _bbox(60, 750, 260, 775)),
    ])

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert regions[0].confidence >= 0.5
```

- [ ] **步骤 2：运行失败测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_extractor.py -q
```

期望：新增提取器相关测试失败，提示模块不存在。

- [ ] **步骤 3：实现提取器**

新建 `backend/app/services/signing_region/extractor.py`：

```python
from __future__ import annotations

import re
from collections import defaultdict

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionRole,
)


SIGNING_ANCHOR_RE = re.compile(r"甲方|乙方|丙方|丁方|盖章|签章|签字|签署|签订日期|签署日期|法定代表人|授权代表|年月日")
BODY_RE = re.compile(r"应当|负责|承担|履行|支付|违约|权利|义务|为准|合同经|生效|协商|约定")
NUMBERED_RE = re.compile(r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十百千万0-9]+[、.．]|\d+(?:\.\d+){0,4}[、.．]?)")
DATE_RE = re.compile(r"\d{4}\s*年\s*\d{0,2}\s*月\s*\d{0,2}\s*日|年\s*月\s*日|____?年")
VISUAL_TYPES = {"seal", "stamp", "image", "figure", "table"}


class SigningRegionExtractor:
    bottom_ratio = 0.62
    cluster_gap = 90.0
    padding = 20.0

    def extract(self, document: Document) -> list[SigningRegion]:
        regions: list[SigningRegion] = []
        for page in document.pages:
            page_regions = self._extract_page(page)
            regions.extend(page_regions)
        return regions

    def _extract_page(self, page: Page) -> list[SigningRegion]:
        candidates = [block for block in page.blocks if self._is_candidate(block, page)]
        if not candidates:
            return []
        clusters = self._cluster(candidates)
        regions: list[SigningRegion] = []
        for index, blocks in enumerate(clusters, start=1):
            elements = [self._to_element(block, index) for block in blocks]
            confidence, reasons = self._confidence(blocks, page)
            if confidence < 0.5:
                continue
            bbox = self._padded_union([element.bbox for element in elements], page)
            regions.append(SigningRegion(
                region_id=f"SR-{page.page_no}-{index}",
                page_no=page.page_no,
                bbox=bbox,
                region_role=self._role(blocks, page),
                confidence=confidence,
                confidence_reasons=reasons,
                elements=elements,
            ))
        return regions

    def _is_candidate(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        block_type = (block.block_type or "").lower()
        in_bottom = page.height > 0 and block.bbox.y0 >= page.height * self.bottom_ratio
        has_visual = block_type in VISUAL_TYPES
        has_anchor = bool(SIGNING_ANCHOR_RE.search(text))
        form_like = self._form_like(block.text or "")
        signing_context = "以下无正文" in text or "签署页" in text or "签字页" in text
        if has_visual and in_bottom:
            return True
        if not in_bottom and not signing_context:
            return False
        if not has_anchor and not form_like and not signing_context:
            return False
        if has_anchor and not (has_visual or form_like or signing_context):
            return False
        if len(text) > 120 and not has_visual:
            return False
        if NUMBERED_RE.match(text) or BODY_RE.search(text):
            return has_visual and form_like
        return True

    def _cluster(self, blocks: list[TextBlock]) -> list[list[TextBlock]]:
        ordered = sorted(blocks, key=lambda block: (block.page_no, block.bbox.y0, block.bbox.x0))
        clusters: list[list[TextBlock]] = []
        for block in ordered:
            if not clusters:
                clusters.append([block])
                continue
            previous = clusters[-1][-1]
            if block.bbox.y0 - previous.bbox.y1 <= self.cluster_gap:
                clusters[-1].append(block)
            else:
                clusters.append([block])
        return clusters

    def _to_element(self, block: TextBlock, region_index: int) -> SigningElement:
        block_type = (block.block_type or "").lower()
        element_type = self._element_type(block)
        return SigningElement(
            element_id=f"{block.block_id}-signing-{region_index}",
            element_type=element_type,
            page_no=block.page_no,
            bbox=block.layout_bbox or block.bbox,
            text=block.text,
            confidence=block.confidence if block.confidence is not None else 0.8,
            source="layout" if block_type in VISUAL_TYPES else "ocr",
            raw_ref={"block_id": block.block_id, "block_type": block.block_type},
        )

    def _element_type(self, block: TextBlock) -> SigningElementType:
        block_type = (block.block_type or "").lower()
        text = self._compact(block.text)
        if block_type in {"seal", "stamp"}:
            return SigningElementType.SEAL
        if block_type in {"image", "figure"}:
            return SigningElementType.SIGNATURE
        if block_type == "table":
            return SigningElementType.SIGNING_TABLE
        if DATE_RE.search(text):
            return SigningElementType.DATE_FIELD
        if SIGNING_ANCHOR_RE.search(text):
            return SigningElementType.LABEL
        return SigningElementType.VISUAL_AREA

    def _confidence(self, blocks: list[TextBlock], page: Page) -> tuple[float, list[str]]:
        del page
        reasons: list[str] = []
        texts = "".join(block.text or "" for block in blocks)
        block_types = {(block.block_type or "").lower() for block in blocks}
        score = 0.0
        if block_types & {"seal", "stamp"}:
            score += 0.4
            reasons.append("seal_block")
        if block_types & {"image", "figure", "table"}:
            score += 0.25
            reasons.append("visual_or_table_block")
        if SIGNING_ANCHOR_RE.search(texts):
            score += 0.2
            reasons.append("signing_label")
        if self._form_like(texts):
            score += 0.2
            reasons.append("form_like")
        if "以下无正文" in texts or "签署页" in texts or "签字页" in texts:
            score += 0.2
            reasons.append("signing_page_context")
        return min(score, 1.0), reasons

    def _role(self, blocks: list[TextBlock], page: Page) -> SigningRegionRole:
        text = self._compact("".join(block.text or "" for block in blocks))
        if "甲方" in text and "乙方" in text:
            return SigningRegionRole.BOTH_PARTIES
        if "甲方" in text:
            return SigningRegionRole.PARTY_A
        if "乙方" in text:
            return SigningRegionRole.PARTY_B
        x0 = min(block.bbox.x0 for block in blocks)
        x1 = max(block.bbox.x1 for block in blocks)
        if page.width > 0 and x0 < page.width * 0.25 and x1 > page.width * 0.75:
            return SigningRegionRole.BOTH_PARTIES
        return SigningRegionRole.UNKNOWN

    def _padded_union(self, bboxes: list[BBox], page: Page) -> BBox:
        return BBox(
            x0=max(0.0, min(bbox.x0 for bbox in bboxes) - self.padding),
            y0=max(0.0, min(bbox.y0 for bbox in bboxes) - self.padding),
            x1=min(page.width, max(bbox.x1 for bbox in bboxes) + self.padding),
            y1=min(page.height, max(bbox.y1 for bbox in bboxes) + self.padding),
        )

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @staticmethod
    def _form_like(text: str) -> bool:
        return (text or "").count("：") + (text or "").count(":") >= 2 or bool(re.search(r"[_＿—-]{2,}", text or ""))
```

- [ ] **步骤 4：运行提取器测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_extractor.py -q
```

期望：全部通过。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/signing_region/extractor.py backend/tests/test_signing_region_extractor.py
git commit -m "feat: extract signing regions from layout blocks"
```

## 任务 4：视觉适配器与指纹降级

**文件：**
- 新建：`backend/app/services/signing_region/visual.py`
- 新建：`backend/tests/test_signing_region_visual.py`

- [ ] **步骤 1：写视觉适配器测试**

新建 `backend/tests/test_signing_region_visual.py`：

```python
from pathlib import Path

from app.models import BBox
from app.services.signing_region.models import SigningRegion
from app.services.signing_region.visual import LocalCpuVisualSignatureDetector, RemoteVisualSignatureDetector


def _region() -> SigningRegion:
    return SigningRegion(
        region_id="SR-1-1",
        page_no=1,
        bbox=BBox(x0=100, y0=600, x1=300, y1=780),
        confidence=0.8,
        confidence_reasons=["test"],
    )


def test_local_visual_detector_without_model_is_unavailable() -> None:
    detector = LocalCpuVisualSignatureDetector(model_path="")

    result = detector.detect(Path("sample.pdf"), [_region()], task_id="task-1")

    assert result.available is False
    assert result.error == "local_model_not_configured"
    assert result.detections == []


def test_remote_visual_detector_without_url_is_unavailable() -> None:
    detector = RemoteVisualSignatureDetector(base_url="")

    result = detector.detect(Path("sample.pdf"), [_region()], task_id="task-1")

    assert result.available is False
    assert result.error == "remote_url_not_configured"
```

- [ ] **步骤 2：运行失败测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_visual.py -q
```

期望：失败，提示 `app.services.signing_region.visual` 不存在。

- [ ] **步骤 3：实现视觉适配器**

新建 `backend/app/services/signing_region/visual.py`：

```python
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Protocol

import httpx

from app.config import settings
from app.models import BBox
from app.services.signing_region.models import SigningRegion, VisualDetection, VisualDetectionResult

logger = logging.getLogger(__name__)


class VisualSignatureDetector(Protocol):
    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult: ...


class LocalCpuVisualSignatureDetector:
    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path if model_path is not None else settings.signing_visual_local_model_path

    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        del pdf_path, regions, task_id
        if not self.model_path:
            return VisualDetectionResult(available=False, error="local_model_not_configured")
        return VisualDetectionResult(available=False, error="local_model_unavailable")


class RemoteVisualSignatureDetector:
    def __init__(self, base_url: str | None = None, timeout: int | None = None) -> None:
        self.base_url = (base_url if base_url is not None else settings.signing_visual_detector_url).strip().rstrip("/")
        self.timeout = timeout if timeout is not None else settings.signing_visual_detector_timeout

    def detect(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        if not self.base_url:
            return VisualDetectionResult(available=False, error="remote_url_not_configured")
        payload = {
            "task_id": task_id,
            "pdf_path": str(pdf_path),
            "regions": [
                {
                    "region_id": region.region_id,
                    "page_no": region.page_no,
                    "bbox": region.bbox.model_dump(),
                }
                for region in regions
            ],
        }
        try:
            response = httpx.post(f"{self.base_url}/detect-signatures", json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Signing visual detector failed: %s", exc)
            return VisualDetectionResult(available=False, error="remote_call_failed")
        detections: list[VisualDetection] = []
        for item in data.get("detections", []):
            bbox = item.get("bbox") or {}
            detections.append(VisualDetection(
                page_no=int(item.get("page_no", 0)),
                bbox=BBox(**bbox),
                label=str(item.get("label") or "signature"),
                confidence=float(item.get("confidence") or 0.0),
                model_name=str(data.get("model_name") or item.get("model_name") or "remote"),
                raw_data=item,
            ))
        return VisualDetectionResult(
            available=True,
            model_name=str(data.get("model_name") or "remote"),
            detections=detections,
        )


class OpenCvSigningRegionFingerprinter:
    def fingerprint_region(self, pdf_path: Path, region: SigningRegion) -> dict[str, str | float]:
        del pdf_path, region
        return {"status": "unavailable", "reason": "fingerprint_not_configured"}
```

- [ ] **步骤 4：运行视觉测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_visual.py -q
```

期望：`2 passed`。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/signing_region/visual.py backend/tests/test_signing_region_visual.py
git commit -m "feat: add signing visual detector adapters"
```

## 任务 5：匹配、对比和 DiffItem 构建

**文件：**
- 新建：`backend/app/services/signing_region/matcher.py`
- 新建：`backend/app/services/signing_region/comparator.py`
- 新建：`backend/app/services/signing_region/diff_builder.py`
- 新建：`backend/tests/test_signing_region_comparison.py`

- [ ] **步骤 1：写匹配与对比测试**

新建 `backend/tests/test_signing_region_comparison.py`：

```python
from app.models import BBox
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
from app.services.signing_region.matcher import SigningRegionMatcher
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion, SigningRegionRole


def _region(region_id: str, text: str, *, page_no: int = 1, x0: float = 60) -> SigningRegion:
    return SigningRegion(
        region_id=region_id,
        page_no=page_no,
        bbox=BBox(x0=x0, y0=650, x1=x0 + 160, y1=780),
        region_role=SigningRegionRole.PARTY_A,
        confidence=0.9,
        confidence_reasons=["test"],
        elements=[
            SigningElement(
                element_id=f"{region_id}-seal",
                element_type=SigningElementType.SEAL,
                page_no=page_no,
                bbox=BBox(x0=x0 + 20, y0=680, x1=x0 + 120, y1=760),
                text=text,
                confidence=0.9,
                source="layout",
            )
        ],
    )


def test_matcher_pairs_regions_by_page_and_role() -> None:
    original = [_region("O1", "A公司")]
    compare = [_region("C1", "A公司")]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], compare[0], 1.0)]


def test_comparator_detects_seal_text_change() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "B公司"), match_confidence=0.9)

    assert comparison.diff_type == "MODIFY"
    assert comparison.seal_changes[0]["original_text"] == "A公司"
    assert comparison.seal_changes[0]["compare_text"] == "B公司"
    assert "SIGNING_SEAL_CHANGE" in comparison.review_flags


def test_diff_builder_outputs_signing_region_diff() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "B公司"), match_confidence=0.9)

    diffs = SigningRegionDiffBuilder().build_diffs([comparison], start_index=3)

    assert len(diffs) == 1
    assert diffs[0].diff_id == "D003"
    assert diffs[0].source_type == "signing_region"
    assert "A公司" in diffs[0].original_text
    assert "B公司" in diffs[0].compare_text
    assert diffs[0].original_evidence[0].method == "signing_region"
```

- [ ] **步骤 2：运行失败测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_comparison.py -q
```

期望：失败，提示 matcher/comparator/diff_builder 模块不存在。

- [ ] **步骤 3：实现 matcher**

新建 `backend/app/services/signing_region/matcher.py`：

```python
from __future__ import annotations

from app.services.signing_region.models import SigningRegion


class SigningRegionMatcher:
    threshold = 0.35

    def match(
        self,
        original: list[SigningRegion],
        compare: list[SigningRegion],
    ) -> list[tuple[SigningRegion | None, SigningRegion | None, float]]:
        pairs: list[tuple[SigningRegion | None, SigningRegion | None, float]] = []
        used_compare: set[int] = set()
        for orig in original:
            best_index = -1
            best_score = 0.0
            for index, comp in enumerate(compare):
                if index in used_compare:
                    continue
                score = self._score(orig, comp)
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index >= 0 and best_score >= self.threshold:
                used_compare.add(best_index)
                pairs.append((orig, compare[best_index], best_score))
            else:
                pairs.append((orig, None, 0.0))
        for index, comp in enumerate(compare):
            if index not in used_compare:
                pairs.append((None, comp, 0.0))
        return pairs

    def _score(self, original: SigningRegion, compare: SigningRegion) -> float:
        page_score = 1.0 if original.page_no == compare.page_no else (0.5 if abs(original.page_no - compare.page_no) <= 1 else 0.0)
        role_score = 1.0 if original.region_role == compare.region_role else 0.4
        iou_score = self._iou(original, compare)
        return round(page_score * 0.4 + role_score * 0.3 + iou_score * 0.3, 4)

    @staticmethod
    def _iou(original: SigningRegion, compare: SigningRegion) -> float:
        x0 = max(original.bbox.x0, compare.bbox.x0)
        y0 = max(original.bbox.y0, compare.bbox.y0)
        x1 = min(original.bbox.x1, compare.bbox.x1)
        y1 = min(original.bbox.y1, compare.bbox.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        orig_area = max(1.0, (original.bbox.x1 - original.bbox.x0) * (original.bbox.y1 - original.bbox.y0))
        comp_area = max(1.0, (compare.bbox.x1 - compare.bbox.x0) * (compare.bbox.y1 - compare.bbox.y0))
        return inter / (orig_area + comp_area - inter)
```

- [ ] **步骤 4：实现 comparator**

新建 `backend/app/services/signing_region/comparator.py`：

```python
from __future__ import annotations

from app.services.signing_region.models import (
    SigningElementType,
    SigningRegion,
    SigningRegionComparison,
)


class SigningRegionComparator:
    def compare(
        self,
        original: SigningRegion | None,
        compare: SigningRegion | None,
        *,
        match_confidence: float = 0.0,
    ) -> SigningRegionComparison:
        comparison = SigningRegionComparison(
            comparison_id=self._comparison_id(original, compare),
            original_region=original,
            compare_region=compare,
            match_confidence=match_confidence,
        )
        if original is None and compare is not None:
            comparison.diff_type = "ADD"
            comparison.signature_changes.append({"type": "ADD", "detail": "新增签章区"})
            comparison.review_flags.append("SIGNING_VISUAL_CHANGE")
            return comparison
        if compare is None and original is not None:
            comparison.diff_type = "DELETE"
            comparison.signature_changes.append({"type": "DELETE", "detail": "删除签章区"})
            comparison.review_flags.append("SIGNING_VISUAL_CHANGE")
            return comparison
        if original is None or compare is None:
            return comparison
        self._compare_seals(original, compare, comparison)
        if comparison.seal_changes or comparison.signature_changes or comparison.date_changes or comparison.label_changes or comparison.table_changes or comparison.visual_changes:
            comparison.diff_type = "MODIFY"
        return comparison

    def _compare_seals(self, original: SigningRegion, compare: SigningRegion, comparison: SigningRegionComparison) -> None:
        original_text = " ".join(element.text.strip() for element in original.elements if element.element_type == SigningElementType.SEAL and element.text.strip())
        compare_text = " ".join(element.text.strip() for element in compare.elements if element.element_type == SigningElementType.SEAL and element.text.strip())
        if original_text != compare_text:
            comparison.seal_changes.append({
                "type": "MODIFY",
                "original_text": original_text,
                "compare_text": compare_text,
            })
            comparison.review_flags.append("SIGNING_SEAL_CHANGE")

    @staticmethod
    def _comparison_id(original: SigningRegion | None, compare: SigningRegion | None) -> str:
        left = original.region_id if original is not None else "NONE"
        right = compare.region_id if compare is not None else "NONE"
        return f"{left}__{right}"
```

- [ ] **步骤 5：实现 diff builder**

新建 `backend/app/services/signing_region/diff_builder.py`：

```python
from __future__ import annotations

from app.models import DiffItem, EvidenceBox, TextRange
from app.services.signing_region.models import SigningRegion, SigningRegionComparison
from app.utils.id_utils import generate_diff_id


class SigningRegionDiffBuilder:
    def build_diffs(self, comparisons: list[SigningRegionComparison], start_index: int = 1) -> list[DiffItem]:
        diffs: list[DiffItem] = []
        next_index = start_index
        for comparison in comparisons:
            if comparison.diff_type is None:
                continue
            diff = self._to_diff(comparison, next_index)
            diffs.append(diff)
            next_index += 1
        return diffs

    def _to_diff(self, comparison: SigningRegionComparison, index: int) -> DiffItem:
        original_text = self._summary(comparison.original_region)
        compare_text = self._summary(comparison.compare_region)
        changed_text = self._readable_change(comparison)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=comparison.diff_type or "MODIFY",
            title=self._title(comparison),
            original_text=original_text,
            compare_text=compare_text,
            original_snippet=original_text,
            compare_snippet=compare_text,
            readable_change=changed_text,
            source_type="signing_region",
            section_type="signature",
            review_flags=list(dict.fromkeys(comparison.review_flags)),
            original_evidence=[self._evidence(comparison.original_region, comparison.diff_type)] if comparison.original_region else [],
            compare_evidence=[self._evidence(comparison.compare_region, comparison.diff_type)] if comparison.compare_region else [],
            original_change_ranges=[TextRange(start=0, end=len(original_text), highlight_type=comparison.diff_type or "MODIFY")] if original_text else [],
            compare_change_ranges=[TextRange(start=0, end=len(compare_text), highlight_type=comparison.diff_type or "MODIFY")] if compare_text else [],
        )

    @staticmethod
    def _summary(region: SigningRegion | None) -> str:
        if region is None:
            return ""
        parts = [element.text.strip() for element in region.elements if element.text.strip()]
        return "；".join(parts)

    @staticmethod
    def _title(comparison: SigningRegionComparison) -> str:
        region = comparison.original_region or comparison.compare_region
        page_no = region.page_no if region is not None else 0
        return f"签章区（第{page_no}页）"

    @staticmethod
    def _readable_change(comparison: SigningRegionComparison) -> str:
        messages: list[str] = []
        for change in comparison.seal_changes:
            messages.append(f"印章文字变化：{change.get('original_text', '')} → {change.get('compare_text', '')}")
        for change in comparison.signature_changes:
            messages.append(str(change.get("detail") or "签章区变化"))
        return "；".join(messages) or "签章区发生变化"

    @staticmethod
    def _evidence(region: SigningRegion | None, highlight_type: str | None) -> EvidenceBox:
        assert region is not None
        return EvidenceBox(
            page_no=region.page_no,
            bbox=region.bbox,
            method="signing_region",
            text=SigningRegionDiffBuilder._summary(region)[:300],
            highlight_type=highlight_type,
            confidence=region.confidence,
            evidence_quality="HIGH" if region.confidence >= 0.75 else "MEDIUM",
        )
```

- [ ] **步骤 6：运行对比测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_comparison.py -q
```

期望：全部通过。

- [ ] **步骤 7：提交**

```bash
git add backend/app/services/signing_region/matcher.py backend/app/services/signing_region/comparator.py backend/app/services/signing_region/diff_builder.py backend/tests/test_signing_region_comparison.py
git commit -m "feat: compare signing regions"
```

## 任务 6：旧差异覆盖映射

**文件：**
- 新建：`backend/app/services/signing_region/coverage.py`
- 修改：`backend/tests/test_signing_region_comparison.py`

- [ ] **步骤 1：写覆盖测试**

在 `backend/tests/test_signing_region_comparison.py` 追加：

```python
from app.models import DiffItem, EvidenceBox
from app.services.signing_region.coverage import SigningRegionCoverageBuilder


def test_coverage_hides_overlapping_seal_diff() -> None:
    region_diff = SigningRegionDiffBuilder().build_diffs([
        SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "B公司"), match_confidence=0.9)
    ], start_index=10)[0]
    legacy = DiffItem(
        diff_id="D002",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第1页）",
        compare_text="B公司",
        compare_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=90, y0=680, x1=180, y1=760), method="seal_region", text="B公司")
        ],
    )

    result = SigningRegionCoverageBuilder().build([region_diff], [legacy])

    assert result.covered_diff_ids == {"D002"}
    assert result.entries[0].signing_region_diff_id == "D010"
```

- [ ] **步骤 2：运行失败测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_comparison.py::test_coverage_hides_overlapping_seal_diff -q
```

期望：失败，提示 `coverage` 模块不存在。

- [ ] **步骤 3：实现 coverage**

新建 `backend/app/services/signing_region/coverage.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field

from app.models import BBox, DiffItem
from app.services.signing_region.models import SigningCoverageEntry


LEGACY_SOURCES = {"seal", "table", "metadata", "header_footer"}
SIGNING_TEXT_MARKERS = ("签", "章", "甲方", "乙方", "法定代表", "授权代表", "日期")


@dataclass
class SigningCoverageResult:
    entries: list[SigningCoverageEntry] = field(default_factory=list)
    covered_diff_ids: set[str] = field(default_factory=set)


class SigningRegionCoverageBuilder:
    def build(self, signing_region_diffs: list[DiffItem], legacy_diffs: list[DiffItem]) -> SigningCoverageResult:
        result = SigningCoverageResult()
        for signing_diff in signing_region_diffs:
            covered: list[str] = []
            reasons: dict[str, str] = {}
            signing_boxes = [evidence.bbox for evidence in [*signing_diff.original_evidence, *signing_diff.compare_evidence]]
            for legacy in legacy_diffs:
                if legacy.source_type not in LEGACY_SOURCES:
                    continue
                if not self._looks_signing_related(legacy):
                    continue
                legacy_boxes = [evidence.bbox for evidence in [*legacy.original_evidence, *legacy.compare_evidence]]
                if self._any_overlap(signing_boxes, legacy_boxes):
                    covered.append(legacy.diff_id)
                    reasons[legacy.diff_id] = "overlaps_confirmed_signing_region"
                    result.covered_diff_ids.add(legacy.diff_id)
            if covered:
                result.entries.append(SigningCoverageEntry(
                    signing_region_diff_id=signing_diff.diff_id,
                    covered_diff_ids=covered,
                    reasons=reasons,
                ))
        return result

    @staticmethod
    def _looks_signing_related(diff: DiffItem) -> bool:
        if diff.source_type == "seal":
            return True
        text = f"{diff.title} {diff.original_text} {diff.compare_text} {' '.join(diff.review_flags)}"
        return any(marker in text for marker in SIGNING_TEXT_MARKERS)

    @staticmethod
    def _any_overlap(left: list[BBox], right: list[BBox]) -> bool:
        return any(SigningRegionCoverageBuilder._overlap_ratio(a, b) >= 0.2 for a in left for b in right)

    @staticmethod
    def _overlap_ratio(a: BBox, b: BBox) -> float:
        x0 = max(a.x0, b.x0)
        y0 = max(a.y0, b.y0)
        x1 = min(a.x1, b.x1)
        y1 = min(a.y1, b.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        base = max(1.0, (b.x1 - b.x0) * (b.y1 - b.y0))
        return inter / base
```

- [ ] **步骤 4：运行覆盖测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_comparison.py::test_coverage_hides_overlapping_seal_diff -q
```

期望：`1 passed`。

- [ ] **步骤 5：提交**

```bash
git add backend/app/services/signing_region/coverage.py backend/tests/test_signing_region_comparison.py
git commit -m "feat: map legacy diffs to signing regions"
```

## 任务 7：Pipeline 集成与调试产物

**文件：**
- 修改：`backend/app/services/pipeline.py`
- 修改：`backend/app/services/pipeline_stages.py`
- 修改：`backend/app/services/compare_debug.py`
- 新建：`backend/tests/test_signing_region_pipeline.py`

- [ ] **步骤 1：写 pipeline 集成测试**

新建 `backend/tests/test_signing_region_pipeline.py`：

```python
from pathlib import Path

from app.models import BBox, CompareOptions, CompareTask, Document, Page, TextBlock
from app.services.extractors.base import ExtractionResult
from app.services.pipeline import PipelineContext
from app.services.pipeline_stages import PreClauseDiffStage, SigningRegionStage


def _doc(seal_text: str) -> Document:
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(
            page_no=1,
            width=595,
            height=842,
            blocks=[
                TextBlock(block_id="label", page_no=1, text="甲方（盖章）：", bbox=BBox(x0=60, y0=650, x1=170, y1=675)),
                TextBlock(block_id="seal", page_no=1, text=seal_text, bbox=BBox(x0=80, y0=680, x1=190, y1=780), block_type="seal"),
            ],
        )],
    )


def _ctx(tmp_path: Path) -> PipelineContext:
    return PipelineContext(
        task=CompareTask(task_id="task-signing", compare_options=CompareOptions()),
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )


def test_signing_region_stage_builds_diff_and_covers_seal(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=_doc("A公司"), extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=_doc("B公司"), extractor_used="test")

    PreClauseDiffStage().execute(ctx)
    SigningRegionStage().execute(ctx)

    assert len(ctx.signing_region_diffs) == 1
    assert ctx.signing_region_diffs[0].source_type == "signing_region"
    assert ctx.signing_region_covered_diff_ids
```

- [ ] **步骤 2：运行失败测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py -q
```

期望：失败，提示 `SigningRegionStage` 不存在或 context 字段不存在。

- [ ] **步骤 3：扩展 `PipelineContext`**

在 `backend/app/services/pipeline.py` 的 `PipelineContext` 中加入：

```python
    signing_regions_original: list[Any] = field(default_factory=list)
    signing_regions_compare: list[Any] = field(default_factory=list)
    signing_region_diffs: list[DiffItem] = field(default_factory=list)
    signing_region_covered_diff_ids: set[str] = field(default_factory=set)
    signing_region_debug: dict[str, Any] = field(default_factory=dict)
```

- [ ] **步骤 4：扩展 `CompareDebugWriter`**

在 `backend/app/services/compare_debug.py` 加入方法：

```python
    def write_signing_region(self, task_id: str, payload: dict[str, Any]) -> str:
        return str(self._write_json(task_id, "signing_region.json", payload))
```

- [ ] **步骤 5：新增 `SigningRegionStage`**

在 `backend/app/services/pipeline_stages.py` 的 `PreClauseDiffStage` 后加入：

```python
class SigningRegionStage:
    name = "签章区域识别中"
    start_progress = 40
    progress = 42

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        if ctx.task.compare_options.ignore_stamps or ctx.task.compare_options.signing_region_mode == "off":
            ctx.signing_region_diffs = []
            ctx.signing_region_covered_diff_ids = set()
            ctx.signing_region_debug = {"skipped": True}
            return

        from app.services.signing_region.comparator import SigningRegionComparator
        from app.services.signing_region.coverage import SigningRegionCoverageBuilder
        from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
        from app.services.signing_region.extractor import SigningRegionExtractor
        from app.services.signing_region.matcher import SigningRegionMatcher

        extractions = ctx.require_extractions()
        extractor = SigningRegionExtractor()
        original_regions = extractor.extract(extractions.original.document)
        compare_regions = extractor.extract(extractions.compare.document)
        ctx.signing_regions_original = original_regions
        ctx.signing_regions_compare = compare_regions

        pairs = SigningRegionMatcher().match(original_regions, compare_regions)
        comparator = SigningRegionComparator()
        comparisons = [
            comparator.compare(original, compare, match_confidence=confidence)
            for original, compare, confidence in pairs
        ]
        pre_count = len(ctx.header_footer_diffs) + len(ctx.metadata_diffs) + len(ctx.table_diffs) + len(ctx.seal_diffs)
        ctx.signing_region_diffs = SigningRegionDiffBuilder().build_diffs(comparisons, start_index=pre_count + 1)
        coverage = SigningRegionCoverageBuilder().build(
            ctx.signing_region_diffs,
            [*ctx.header_footer_diffs, *ctx.metadata_diffs, *ctx.table_diffs, *ctx.seal_diffs],
        )
        ctx.signing_region_covered_diff_ids = coverage.covered_diff_ids
        ctx.signing_region_debug = {
            "original_regions": [region.model_dump() for region in original_regions],
            "compare_regions": [region.model_dump() for region in compare_regions],
            "comparisons": [comparison.model_dump() for comparison in comparisons],
            "covered_legacy_diffs": [entry.model_dump() for entry in coverage.entries],
        }
        _write_debug_artifact(
            ctx.task,
            "signing_region",
            lambda: self.debug_writer.write_signing_region(ctx.task.task_id, ctx.signing_region_debug),
        )
```

- [ ] **步骤 6：把 stage 接入 pipeline**

在 `backend/app/services/compare_service.py` 的 imports 和 stages 中加入 `SigningRegionStage`，位置在 `PreClauseDiffStage(...)` 之后。

在 `backend/app/services/pipeline.py` 的 `_default_stages()` imports 和列表中加入 `SigningRegionStage()`，位置同上。

- [ ] **步骤 7：调整 `ClauseDiffStage` 合并顺序**

在 `ClauseDiffStage.execute()` 中把 `pre_clause_count` 改为计入 `len(ctx.signing_region_diffs)`，并在 `diffs` 列表中 `*ctx.seal_diffs` 后加入：

```python
            *ctx.signing_region_diffs,
```

- [ ] **步骤 8：调整 `SummaryStage` 过滤**

修改 `_filter_compare_option_diffs`：

```python
def _filter_compare_option_diffs(task: CompareTask, diffs: list[DiffItem]) -> list[DiffItem]:
    excluded_source_types: set[str] = set()
    if task.compare_options.ignore_stamps:
        excluded_source_types.update({"seal", "signing_region"})
    if task.compare_options.signing_region_mode == "off":
        excluded_source_types.add("signing_region")
    if task.compare_options.ignore_headers_footers:
        excluded_source_types.add("header_footer")
    filtered = [diff for diff in diffs if diff.source_type not in excluded_source_types]
    return filtered
```

在 `SummaryStage.execute()` 调用过滤前，先排除 `ctx.signing_region_covered_diff_ids`：

```python
        visible_diffs = [diff for diff in ctx.require_diffs() if diff.diff_id not in ctx.signing_region_covered_diff_ids]
        task.diffs, dedupe_remap = _dedupe_final_diffs(_filter_compare_option_diffs(task, visible_diffs))
```

- [ ] **步骤 9：运行 pipeline 集成测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py -q
```

期望：全部通过。

- [ ] **步骤 10：提交**

```bash
git add backend/app/services/pipeline.py backend/app/services/pipeline_stages.py backend/app/services/compare_debug.py backend/app/services/compare_service.py backend/tests/test_signing_region_pipeline.py
git commit -m "feat: integrate signing region stage"
```

## 任务 8：API、报告和前端展示

**文件：**
- 修改：`backend/app/api.py`
- 修改：`backend/app/services/report_generator.py`
- 修改：`frontend/src/lib/api.ts`
- 修改：`frontend/src/pages/ResultPage.tsx`
- 修改：`frontend/src/components/PdfDocumentViewer.tsx`
- 修改：`frontend/src/pages/UploadPage.tsx`
- 测试：`frontend/src/lib/api.test.ts`
- 测试：`frontend/src/pages/ResultPage.test.tsx`
- 测试：`frontend/src/components/PdfDocumentViewer.test.tsx`
- 测试：`frontend/src/pages/UploadPage.test.tsx`

- [ ] **步骤 1：后端 API 接收 `signing_region_mode`**

在 `backend/app/api.py` 的 `compare_contracts` 参数加入：

```python
    signing_region_mode: str = Form("full"),
```

构造 `CompareOptions` 改为：

```python
    compare_options = CompareOptions(
        ignore_stamps=ignore_stamps,
        ignore_headers_footers=ignore_headers_footers,
        signing_region_mode=signing_region_mode,
    )
```

- [ ] **步骤 2：报告来源标签**

在 `backend/app/services/report_generator.py` 中扩展：

```python
_SOURCE_TYPE_ORDER = {"clause": 0, "page": 1, "signing_region": 2, "table": 3, "seal": 4, "header_footer": 5, "metadata": 6}
_SOURCE_TYPE_LABELS = {
    "clause": "条款",
    "page": "页面",
    "signing_region": "签章区",
    "table": "表格",
    "metadata": "封面",
    "seal": "印章",
    "header_footer": "页眉页脚",
}
```

- [ ] **步骤 3：前端 API 测试**

在 `frontend/src/lib/api.test.ts` 增加：

```typescript
it("sends signing region mode when provided", async () => {
  const original = new File(["a"], "original.pdf", { type: "application/pdf" });
  const compare = new File(["b"], "compare.pdf", { type: "application/pdf" });
  mockFetchJson({ task_id: "task-1" });

  await compareContracts(original, compare, {
    ignoreStamps: false,
    ignoreHeadersFooters: false,
    signingRegionMode: "off",
  });

  const body = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1].body as FormData;
  expect(body.get("signing_region_mode")).toBe("off");
});
```

- [ ] **步骤 4：前端 API 实现**

在 `frontend/src/lib/api.ts` 的 `compareContracts` 中加入：

```typescript
  if (options?.signingRegionMode) {
    formData.append("signing_region_mode", options.signingRegionMode);
  }
```

- [ ] **步骤 5：结果页分组测试**

在 `frontend/src/pages/ResultPage.test.tsx` 增加一个签章区 diff fixture，并断言页面出现 `签章区差异`。差异对象最小内容：

```typescript
{
  diff_id: "D900",
  diff_type: "MODIFY",
  source_type: "signing_region",
  title: "签章区（第1页）",
  original_text: "甲方（盖章）：A公司",
  compare_text: "甲方（盖章）：B公司",
  readable_change: "印章文字变化：A公司 → B公司",
  review_flags: ["SIGNING_SEAL_CHANGE"],
  original_evidence: [],
  compare_evidence: [],
}
```

- [ ] **步骤 6：结果页实现**

在 `frontend/src/pages/ResultPage.tsx`：

```typescript
type AuditGroup = "MAIN" | "SIGNING" | "STRUCTURAL" | "OTHER";
```

`auditGroupLabels` 增加：

```typescript
SIGNING: "签章区差异",
```

`auditGroup()` 开头加入：

```typescript
  if (diff.source_type === "signing_region") {
    return "SIGNING";
  }
```

`groupOrder` 改为：

```typescript
const groupOrder: AuditGroup[] = ["MAIN", "SIGNING", "STRUCTURAL", "OTHER"];
```

- [ ] **步骤 7：PDF 高亮实现**

在 `frontend/src/components/PdfDocumentViewer.tsx` 中扩展 `PageHighlight["markKind"]`：

```typescript
markKind: "fallback" | "seal" | "table" | "text" | "signing-region";
```

在 `highlightMarkKind()` 中把签章区证据方法映射为独立样式，位置放在 `seal_region` 判断之前：

```typescript
if (
  method === "signing_region"
  || method === "signing_region_element"
  || method === "signing_region_visual"
) {
  return "signing-region";
}
```

在 `frontend/src/styles.css` 的 PDF 高亮样式区增加：

```css
.pdf-highlight-mark.signing-region rect {
  fill-opacity: 0.12;
  stroke: rgba(124, 58, 237, 0.72);
  stroke-dasharray: 6 4;
}

.pdf-highlight-mark.active.signing-region rect,
.pdf-highlight-mark.signing-region:hover rect,
.pdf-highlight-mark.signing-region:focus-visible rect {
  fill-opacity: 0.2;
  stroke: rgba(124, 58, 237, 0.95);
  stroke-width: 1.6;
  filter: none;
}
```

- [ ] **步骤 8：上传页语义保持**

`frontend/src/pages/UploadPage.tsx` 保持当前文案，不新增复杂选择器。把 `handleSubmit()` 中的提交调用改为：

```typescript
const payload = await compareContracts(originalFile, compareFile, {
  ignoreStamps,
  ignoreHeadersFooters,
  signingRegionMode: ignoreStamps ? "off" : "full",
});
```

- [ ] **步骤 9：运行前端测试**

```bash
cd frontend && npm test -- --run
```

期望：前端测试通过。

- [ ] **步骤 10：运行前端构建**

```bash
cd frontend && npm run build
```

期望：构建成功。

- [ ] **步骤 11：提交**

```bash
git add backend/app/api.py backend/app/services/report_generator.py frontend/src/lib/api.ts frontend/src/pages/ResultPage.tsx frontend/src/components/PdfDocumentViewer.tsx frontend/src/pages/UploadPage.tsx frontend/src/lib/api.test.ts frontend/src/pages/ResultPage.test.tsx frontend/src/components/PdfDocumentViewer.test.tsx frontend/src/pages/UploadPage.test.tsx
git commit -m "feat: show signing region diffs"
```

## 任务 9：回归验证与质量门禁

**文件：**
- 修改：`backend/tests/test_pipeline.py`

- [ ] **步骤 1：增加 `ignore_stamps` 集成测试**

在 `backend/tests/test_pipeline.py` 的 `TestPreClauseDiffStage` 附近加入：

```python
def test_ignore_stamps_hides_signing_region_diffs_in_summary(tmp_path: Path) -> None:
    from app.services.pipeline_stages import SummaryStage

    ctx = make_ctx(tmp_path)
    ctx.task.compare_options = CompareOptions(ignore_stamps=True)
    ctx.diffs = [
        DiffItem(diff_id="D001", diff_type="MODIFY", source_type="signing_region", title="签章区"),
        DiffItem(diff_id="D002", diff_type="ADD", source_type="seal", title="印章区域"),
        DiffItem(diff_id="D003", diff_type="MODIFY", source_type="clause", title="正文"),
    ]

    SummaryStage().execute(ctx)

    assert [diff.diff_id for diff in ctx.task.diffs] == ["D003"]
```

- [ ] **步骤 2：运行后端签章测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_extractor.py backend/tests/test_signing_region_visual.py backend/tests/test_signing_region_comparison.py backend/tests/test_signing_region_pipeline.py -q
```

期望：全部通过。

- [ ] **步骤 3：运行关键回归测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_seal_comparator.py backend/tests/test_pipeline.py backend/tests/test_page_diff.py -q
```

期望：全部通过。

- [ ] **步骤 4：运行后端全量测试**

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests -q
```

期望：全部通过。失败时停止执行并记录失败测试名称、命令和首个断言错误；不得修改未在本计划列出的文件。

- [ ] **步骤 5：运行后端静态检查**

```bash
env -u VIRTUAL_ENV uv run ruff check backend/app backend/tests
```

期望：无 lint 错误。

- [ ] **步骤 6：运行前端测试和构建**

```bash
cd frontend && npm test -- --run
cd frontend && npm run build
```

期望：测试与构建均通过。

- [ ] **步骤 7：提交回归补充**

```bash
git add backend/tests/test_pipeline.py
git commit -m "test: cover signing region compare options"
```

## 自检清单

- [ ] 设计文档中的“固定使用 PaddleOCR / PP-Structure，不接 Docling/Surya fallback”已由本计划保持。
- [ ] 关键词不能单独召回签章区：任务 3 的 `test_extractor_rejects_single_bottom_keyword` 覆盖。
- [ ] 旧签章碎片默认隐藏：任务 6 和任务 7 覆盖。
- [ ] 视觉/签名模型不可用不失败：任务 4 覆盖。
- [ ] `ignore_stamps` 同时隐藏 `seal` 与 `signing_region`：任务 7 和任务 9 覆盖。
- [ ] 前端独立分组和高亮：任务 8 覆盖。
- [ ] 每个任务都有测试、运行命令和提交步骤。
