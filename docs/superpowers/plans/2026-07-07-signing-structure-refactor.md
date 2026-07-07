# 签署结构识别与正文剥离重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有签章区候选识别升级为“签署块识别 + 正文剥离 + 签章区独立对比”，修复封面签约信息表误判和真实签署栏进入正文 diff 的问题。

**Architecture:** 在现有 `backend/app/services/signing_region/` 下增加签署页、签署块和条款文档剥离组件。`SigningRegionStage` 保持流水线位置不变，但先识别 `SigningBlock`，再从块内生成 `SigningRegion`，最后生成剥离签署块后的 `clause_document` 供 `SplitStage` 使用。

**Tech Stack:** Python 3.12、FastAPI 后端模型、Pydantic、PyMuPDF/可选 OpenCV、pytest、ruff、现有 PaddleOCR/PP-Structure 抽取结果。

---

## 范围检查

本计划只覆盖一个子系统：签署结构识别与正文剥离。OpenCV 在本计划中只做可选辅助，不训练模型，不接入 PP-YOLOE/RT-DETR。

## 文件结构

新增文件：

- `backend/app/services/signing_region/block_detector.py`：签署块检测，多信号评分，封面候选排除。
- `backend/app/services/signing_region/clause_document.py`：根据高置信签署块生成条款专用文档。
- `backend/tests/test_signing_structure_detector.py`：签署块检测单元测试。
- `backend/tests/test_signing_clause_document.py`：正文剥离单元测试。

修改文件：

- `backend/app/services/signing_region/models.py`：增加 `SigningPage`、`SigningBlock`、`SigningVisualFeatures`，为 `SigningRegion` 增加 `signing_block_id`。
- `backend/app/services/signing_region/extractor.py`：保留兼容入口，新增从 `SigningBlock` 生成 `SigningRegion` 的入口。
- `backend/app/services/signing_region/matcher.py`：支持跨页签署块/签章区匹配。
- `backend/app/services/signing_region/diff_builder.py`：跨页标题显示原页/新页。
- `backend/app/services/signing_region/visual.py`：实现基础 OpenCV 视觉指纹，失败时稳定降级。
- `backend/app/services/pipeline.py`：在 `PipelineContext` 增加签署页、签署块、条款文档字段。
- `backend/app/services/pipeline_stages.py`：`SigningRegionStage` 写入签署结构和条款文档；`SplitStage` 使用条款文档。
- `backend/tests/test_signing_region_extractor.py`：更新既有签章区测试以适配签署块入口。
- `backend/tests/test_signing_region_pipeline.py`：覆盖流水线剥离、debug 和开关行为。
- `backend/tests/test_signing_region_comparison.py`：覆盖跨页匹配和跨页标题。
- `backend/tests/test_signing_region_visual.py`：覆盖 OpenCV 不可用和 hash 生成。

---

### Task 1: 新增签署结构模型

**Files:**
- Modify: `backend/app/services/signing_region/models.py`
- Test: `backend/tests/test_signing_structure_detector.py`

- [ ] **Step 1: 写模型失败测试**

在 `backend/tests/test_signing_structure_detector.py` 新增：

```python
from app.models import BBox
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningPage,
    SigningPageType,
    SigningVisualFeatures,
)


def test_signing_structure_models_hold_block_page_and_visual_features() -> None:
    visual = SigningVisualFeatures(
        status="ok",
        has_red_seal=True,
        has_handwriting=False,
        visual_hash="abc123",
        confidence=0.8,
        reasons=["red_connected_component"],
    )
    block = SigningBlock(
        block_id="SB-10-1",
        page_no=10,
        bbox=BBox(x0=60, y0=610, x1=520, y1=740),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["paired_parties", "seal_signature_date_cluster"],
        source_block_ids=["p10_b24", "p10_b25"],
        text="甲方：A 乙方：B\n(盖章)\n(签字)\n日期：",
        visual_features=visual,
        exclude_from_clause_diff=True,
    )
    page = SigningPage(
        page_no=10,
        bbox=BBox(x0=0, y0=0, x1=595, y1=842),
        signing_page_type=SigningPageType.MIXED_PAGE,
        confidence=0.72,
        confidence_reasons=["contains_high_confidence_signing_block"],
        block_ids=[block.block_id],
        exclude_full_page_from_clause_diff=False,
    )

    assert page.signing_page_type == SigningPageType.MIXED_PAGE
    assert block.block_role == SigningBlockRole.BOTH_PARTIES
    assert block.visual_features.visual_hash == "abc123"
    assert block.exclude_from_clause_diff is True
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py -q
```

Expected: FAIL，原因是 `SigningBlock` / `SigningPage` 等模型未定义。

- [ ] **Step 3: 增加模型**

在 `backend/app/services/signing_region/models.py` 增加：

```python
class SigningPageType(str, Enum):
    FULL_PAGE = "full_page"
    MIXED_PAGE = "mixed_page"
    CONTINUATION_PAGE = "continuation_page"
    UNKNOWN = "unknown"


class SigningBlockRole(str, Enum):
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    BOTH_PARTIES = "both_parties"
    CONTINUATION = "continuation"
    UNKNOWN = "unknown"


class SigningBlockConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SigningVisualFeatures(BaseModel):
    status: str = "unavailable"
    has_red_seal: bool = False
    has_handwriting: bool = False
    visual_hash: str = ""
    detected_bboxes: list[BBox] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class SigningPage(BaseModel):
    page_no: int
    bbox: BBox
    page_role: str = "body"
    signing_page_type: SigningPageType = SigningPageType.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    exclude_full_page_from_clause_diff: bool = False


class SigningBlock(BaseModel):
    block_id: str
    page_no: int
    bbox: BBox
    block_role: SigningBlockRole = SigningBlockRole.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_level: SigningBlockConfidenceLevel = SigningBlockConfidenceLevel.LOW
    confidence_reasons: list[str] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    text: str = ""
    elements: list[SigningElement] = Field(default_factory=list)
    visual_features: SigningVisualFeatures = Field(default_factory=SigningVisualFeatures)
    exclude_from_clause_diff: bool = False
```

同时给现有 `SigningRegion` 增加字段：

```python
signing_block_id: str = ""
```

- [ ] **Step 4: 运行模型测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/signing_region/models.py backend/tests/test_signing_structure_detector.py
git commit -m "feat: add signing structure models"
```

---

### Task 2: 实现签署块检测器

**Files:**
- Create: `backend/app/services/signing_region/block_detector.py`
- Modify: `backend/tests/test_signing_structure_detector.py`

- [ ] **Step 1: 写封面误检和真实签署块测试**

追加到 `backend/tests/test_signing_structure_detector.py`：

```python
from app.models import BBox, Document, DocumentProfile, Page, PageProfile, TextBlock
from app.services.signing_region.block_detector import SigningBlockDetector


def _bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _block(block_id: str, text: str, bbox: BBox, *, page_no: int = 1, block_type: str = "text") -> TextBlock:
    return TextBlock(block_id=block_id, page_no=page_no, text=text, bbox=bbox, block_type=block_type)


def _document(page: Page, *, page_role: str = "body") -> Document:
    profile = DocumentProfile(
        filename="test.pdf",
        page_count=1,
        page_profiles=[
            PageProfile(
                page_no=page.page_no,
                width=page.width,
                height=page.height,
                page_role=page_role,
                text_block_count=len(page.blocks),
            )
        ],
    )
    return Document(filename="test.pdf", path="test.pdf", page_count=1, pages=[page], profile=profile)


def test_detector_excludes_cover_signing_info_table() -> None:
    page = Page(
        page_no=1,
        width=595,
        height=842,
        blocks=[
            _block("title", "采购合同", _bbox(180, 220, 420, 260), block_type="doc_title"),
            _block(
                "cover_table",
                "甲方\n江苏东大金智信息系统有限公司\n乙方\n国能日新科技股份有限公司\n北京\n签订地点\n签订日期\n2026年4月21日",
                _bbox(90, 560, 505, 690),
                block_type="table",
            ),
        ],
    )

    result = SigningBlockDetector().detect(_document(page, page_role="cover"))

    assert result.blocks == []
    assert result.excluded_candidates[0]["reason"] == "cover_signing_info_table"


def test_detector_finds_bottom_mixed_page_signing_block() -> None:
    page = Page(
        page_no=10,
        width=595,
        height=842,
        blocks=[
            _block("body", "14.2甲方在合同履行过程中，要求乙方提供合同约定范围之外的硬件设备。", _bbox(80, 550, 530, 590), page_no=10),
            _block("party", "甲方：江苏东大金智信息系统有限公司  乙方：国能日新科技股份有限公司", _bbox(65, 620, 485, 635), page_no=10),
            _block("seal_a", "(盖章)", _bbox(65, 642, 110, 660), page_no=10),
            _block("seal_b", "(盖章)", _bbox(335, 642, 380, 660), page_no=10),
            _block("rep_a", "法人代表或授权委托人：", _bbox(82, 668, 215, 682), page_no=10),
            _block("rep_b", "法人代表或授权委托人：", _bbox(326, 668, 455, 682), page_no=10),
            _block("sign_a", "(签字)", _bbox(175, 690, 215, 708), page_no=10),
            _block("sign_b", "(签字)", _bbox(390, 690, 430, 708), page_no=10),
            _block("date_a", "日期：", _bbox(84, 713, 122, 730), page_no=10),
            _block("date_b", "日期：", _bbox(325, 713, 362, 730), page_no=10),
        ],
    )

    result = SigningBlockDetector().detect(_document(page))

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.page_no == 10
    assert block.exclude_from_clause_diff is True
    assert block.confidence_level == "high"
    assert "seal_signature_date_cluster" in block.confidence_reasons
    assert set(block.source_block_ids) >= {"party", "seal_a", "rep_a", "sign_a", "date_a"}
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py -q
```

Expected: FAIL，原因是 `block_detector.py` 不存在。

- [ ] **Step 3: 实现检测器最小版本**

创建 `backend/app/services/signing_region/block_detector.py`：

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningPage,
    SigningPageType,
)


PARTY_RE = re.compile(r"甲方|乙方|丙方|丁方")
SEAL_RE = re.compile(r"盖章|签章|公章")
SIGN_RE = re.compile(r"签字|签名")
REPRESENTATIVE_RE = re.compile(r"法定代表人|法人代表|授权代表|授权委托人")
DATE_LABEL_RE = re.compile(r"日期[:：]|签订日期|签署日期")
SIGNING_CONTEXT_RE = re.compile(r"以下无正文|签署页|签字页")
BODY_VERB_RE = re.compile(r"应当|负责|承担|履行|支付|违约|权利|义务|为准|合同经|生效|协商|约定")
NUMBERED_RE = re.compile(r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十百千万0-9]+[、.．]|\d+(?:\.\d+){0,4}[、.．]?)")


@dataclass
class SigningBlockDetectionResult:
    pages: list[SigningPage] = field(default_factory=list)
    blocks: list[SigningBlock] = field(default_factory=list)
    excluded_candidates: list[dict[str, object]] = field(default_factory=list)
    low_confidence_candidates: list[dict[str, object]] = field(default_factory=list)


class SigningBlockDetector:
    bottom_ratio = 0.58
    top_continuation_ratio = 0.24
    cluster_gap = 92.0
    padding = 18.0

    def detect(self, document: Document) -> SigningBlockDetectionResult:
        result = SigningBlockDetectionResult()
        page_roles = self._page_roles(document)
        for page in document.pages:
            blocks = self._detect_page_blocks(page, page_roles.get(page.page_no, "body"), result)
            result.blocks.extend(blocks)
            if blocks:
                result.pages.append(self._to_page(page, page_roles.get(page.page_no, "body"), blocks))
        return result

    def _detect_page_blocks(
        self,
        page: Page,
        page_role: str,
        result: SigningBlockDetectionResult,
    ) -> list[SigningBlock]:
        candidates = [block for block in page.blocks if self._is_candidate(block, page)]
        if not candidates:
            return []
        signing_blocks: list[SigningBlock] = []
        for index, cluster in enumerate(self._cluster(candidates), start=1):
            text = "\n".join(block.text.strip() for block in cluster if block.text.strip())
            bbox = self._padded_union([self._effective_bbox(block) for block in cluster], page)
            score, reasons = self._score_cluster(cluster, page, page_role)
            if self._is_cover_signing_info_table(cluster, page_role, reasons):
                result.excluded_candidates.append({
                    "page_no": page.page_no,
                    "block_ids": [block.block_id for block in cluster],
                    "reason": "cover_signing_info_table",
                    "text": text[:200],
                })
                continue
            if score < 0.5:
                result.low_confidence_candidates.append({
                    "page_no": page.page_no,
                    "block_ids": [block.block_id for block in cluster],
                    "score": score,
                    "reasons": reasons,
                    "text": text[:200],
                })
                continue
            signing_blocks.append(SigningBlock(
                block_id=f"SB-{page.page_no}-{index}",
                page_no=page.page_no,
                bbox=bbox,
                block_role=self._role(text),
                confidence=score,
                confidence_level=self._level(score),
                confidence_reasons=reasons,
                source_block_ids=[block.block_id for block in cluster],
                text=text,
                exclude_from_clause_diff=score >= 0.7,
            ))
        return signing_blocks

    def _is_candidate(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text:
            return False
        bbox = self._effective_bbox(block)
        in_bottom = bbox.y1 >= page.height * self.bottom_ratio
        in_top = bbox.y0 <= page.height * self.top_continuation_ratio
        has_signing_signal = any(pattern.search(text) for pattern in [
            PARTY_RE,
            SEAL_RE,
            SIGN_RE,
            REPRESENTATIVE_RE,
            DATE_LABEL_RE,
            SIGNING_CONTEXT_RE,
        ])
        if not has_signing_signal:
            return False
        if NUMBERED_RE.match(text) and BODY_VERB_RE.search(text):
            return False
        if BODY_VERB_RE.search(text) and len(text) > 45 and not SEAL_RE.search(text) and not SIGN_RE.search(text):
            return False
        return in_bottom or in_top or bool(SIGNING_CONTEXT_RE.search(text))

    def _score_cluster(self, blocks: list[TextBlock], page: Page, page_role: str) -> tuple[float, list[str]]:
        text = self._compact("\n".join(block.text for block in blocks))
        reasons: list[str] = []
        score = 0.0
        if PARTY_RE.search(text) and "甲方" in text and "乙方" in text:
            score += 0.25
            reasons.append("paired_parties")
        elif PARTY_RE.search(text):
            score += 0.1
            reasons.append("party_label")
        signal_count = sum(bool(pattern.search(text)) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE])
        if signal_count >= 3:
            score += 0.35
            reasons.append("seal_signature_date_cluster")
        elif signal_count >= 2:
            score += 0.25
            reasons.append("multiple_signing_labels")
        if SIGNING_CONTEXT_RE.search(text):
            score += 0.2
            reasons.append("signing_page_context")
        if self._cluster_in_bottom(blocks, page):
            score += 0.15
            reasons.append("bottom_signing_position")
        if self._cluster_in_top(blocks, page) and signal_count >= 2:
            score += 0.15
            reasons.append("top_signing_continuation")
        if self._looks_two_column(blocks, page):
            score += 0.1
            reasons.append("two_column_layout")
        if page_role == "cover":
            score -= 0.35
            reasons.append("cover_page_penalty")
        return max(0.0, min(round(score, 2), 1.0)), reasons

    def _is_cover_signing_info_table(self, blocks: list[TextBlock], page_role: str, reasons: list[str]) -> bool:
        if page_role != "cover":
            return False
        text = self._compact("\n".join(block.text for block in blocks))
        has_cover_table = any((block.block_type or "").lower() == "table" for block in blocks)
        has_only_contract_meta = "签订地点" in text or "签订日期" in text
        has_strong_signing = SEAL_RE.search(text) or SIGN_RE.search(text) or REPRESENTATIVE_RE.search(text) or SIGNING_CONTEXT_RE.search(text)
        return has_cover_table and has_only_contract_meta and not has_strong_signing and "cover_page_penalty" in reasons

    def _cluster(self, blocks: list[TextBlock]) -> list[list[TextBlock]]:
        ordered = sorted(blocks, key=lambda block: (block.page_no, self._effective_bbox(block).y0, self._effective_bbox(block).x0))
        clusters: list[list[TextBlock]] = []
        for block in ordered:
            if not clusters:
                clusters.append([block])
                continue
            previous = clusters[-1][-1]
            if self._effective_bbox(block).y0 - self._effective_bbox(previous).y1 <= self.cluster_gap:
                clusters[-1].append(block)
            else:
                clusters.append([block])
        return clusters

    def _to_page(self, page: Page, page_role: str, blocks: list[SigningBlock]) -> SigningPage:
        full_page = len(page.blocks) > 0 and sum(len(block.text.strip()) for block in page.blocks) < 260
        return SigningPage(
            page_no=page.page_no,
            bbox=BBox(x0=0, y0=0, x1=page.width, y1=page.height),
            page_role=page_role,
            signing_page_type=SigningPageType.FULL_PAGE if full_page else SigningPageType.MIXED_PAGE,
            confidence=max(block.confidence for block in blocks),
            confidence_reasons=["contains_high_confidence_signing_block"],
            block_ids=[block.block_id for block in blocks],
            exclude_full_page_from_clause_diff=full_page and all(block.exclude_from_clause_diff for block in blocks),
        )

    @staticmethod
    def _page_roles(document: Document) -> dict[int, str]:
        if document.profile is None:
            return {}
        return {profile.page_no: profile.page_role for profile in document.profile.page_profiles}

    @staticmethod
    def _role(text: str) -> SigningBlockRole:
        compact = re.sub(r"\s+", "", text)
        if "甲方" in compact and "乙方" in compact:
            return SigningBlockRole.BOTH_PARTIES
        if "甲方" in compact:
            return SigningBlockRole.PARTY_A
        if "乙方" in compact:
            return SigningBlockRole.PARTY_B
        return SigningBlockRole.UNKNOWN

    @staticmethod
    def _level(score: float) -> SigningBlockConfidenceLevel:
        if score >= 0.7:
            return SigningBlockConfidenceLevel.HIGH
        if score >= 0.5:
            return SigningBlockConfidenceLevel.MEDIUM
        return SigningBlockConfidenceLevel.LOW

    def _cluster_in_bottom(self, blocks: list[TextBlock], page: Page) -> bool:
        return any(self._effective_bbox(block).y1 >= page.height * self.bottom_ratio for block in blocks)

    def _cluster_in_top(self, blocks: list[TextBlock], page: Page) -> bool:
        return any(self._effective_bbox(block).y0 <= page.height * self.top_continuation_ratio for block in blocks)

    def _looks_two_column(self, blocks: list[TextBlock], page: Page) -> bool:
        centers = [(self._effective_bbox(block).x0 + self._effective_bbox(block).x1) / 2 for block in blocks]
        return bool(centers) and min(centers) < page.width * 0.35 and max(centers) > page.width * 0.6

    def _padded_union(self, bboxes: list[BBox], page: Page) -> BBox:
        x0 = max(0.0, min(bbox.x0 for bbox in bboxes) - self.padding)
        y0 = max(0.0, min(bbox.y0 for bbox in bboxes) - self.padding)
        x1 = min(page.width, max(bbox.x1 for bbox in bboxes) + self.padding)
        y1 = min(page.height, max(bbox.y1 for bbox in bboxes) + self.padding)
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)

    @staticmethod
    def _effective_bbox(block: TextBlock) -> BBox:
        return block.layout_bbox or block.bbox

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text or "")
```

- [ ] **Step 4: 运行检测器测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/signing_region/block_detector.py backend/tests/test_signing_structure_detector.py
git commit -m "feat: detect signing blocks"
```

---

### Task 3: 从签署块生成签章区并保持兼容

**Files:**
- Modify: `backend/app/services/signing_region/extractor.py`
- Modify: `backend/tests/test_signing_region_extractor.py`
- Test: `backend/tests/test_signing_structure_detector.py`

- [ ] **Step 1: 写块内签章区生成测试**

追加到 `backend/tests/test_signing_structure_detector.py`：

```python
from app.services.signing_region.extractor import SigningRegionExtractor


def test_extractor_builds_region_from_detected_signing_block() -> None:
    page = Page(
        page_no=10,
        width=595,
        height=842,
        blocks=[
            _block("party", "甲方：A公司  乙方：B公司", _bbox(65, 620, 485, 635), page_no=10),
            _block("seal_a", "(盖章)", _bbox(65, 642, 110, 660), page_no=10),
            _block("sign_a", "(签字)", _bbox(175, 690, 215, 708), page_no=10),
            _block("date_a", "日期：", _bbox(84, 713, 122, 730), page_no=10),
        ],
    )
    detection = SigningBlockDetector().detect(_document(page))

    regions = SigningRegionExtractor().extract_from_blocks(detection.blocks)

    assert len(regions) == 1
    assert regions[0].signing_block_id == "SB-10-1"
    assert regions[0].page_no == 10
    assert "盖章" in regions[0].elements[0].text or "签字" in regions[0].elements[0].text
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py::test_extractor_builds_region_from_detected_signing_block -q
```

Expected: FAIL，原因是 `extract_from_blocks` 不存在。

- [ ] **Step 3: 实现 `extract_from_blocks`**

在 `backend/app/services/signing_region/extractor.py` 中增加导入和方法：

```python
from app.services.signing_region.models import SigningBlock
```

在 `SigningRegionExtractor` 内新增：

```python
    def extract_from_blocks(self, blocks: list[SigningBlock]) -> list[SigningRegion]:
        regions: list[SigningRegion] = []
        for index, block in enumerate(blocks, start=1):
            elements = block.elements or [
                SigningElement(
                    element_id=f"{block.block_id}-summary",
                    element_type=SigningElementType.SIGNING_TABLE,
                    page_no=block.page_no,
                    bbox=block.bbox,
                    text=block.text,
                    confidence=block.confidence,
                    source="inferred",
                    raw_ref={"source_block_ids": block.source_block_ids},
                )
            ]
            regions.append(SigningRegion(
                region_id=f"SR-{block.page_no}-{index}",
                signing_block_id=block.block_id,
                page_no=block.page_no,
                bbox=block.bbox,
                region_role=self._role_from_block(block),
                confidence=block.confidence,
                confidence_reasons=list(block.confidence_reasons),
                elements=elements,
            ))
        return regions

    @staticmethod
    def _role_from_block(block: SigningBlock) -> SigningRegionRole:
        if block.block_role.value == "party_a":
            return SigningRegionRole.PARTY_A
        if block.block_role.value == "party_b":
            return SigningRegionRole.PARTY_B
        if block.block_role.value == "both_parties":
            return SigningRegionRole.BOTH_PARTIES
        return SigningRegionRole.UNKNOWN
```

暂时保留现有 `extract(document)`，避免破坏旧测试。

- [ ] **Step 4: 运行相关测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_structure_detector.py backend/tests/test_signing_region_extractor.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/signing_region/extractor.py backend/tests/test_signing_structure_detector.py backend/tests/test_signing_region_extractor.py
git commit -m "feat: build signing regions from signing blocks"
```

---

### Task 4: 生成剥离签署块后的条款文档

**Files:**
- Create: `backend/app/services/signing_region/clause_document.py`
- Create: `backend/tests/test_signing_clause_document.py`

- [ ] **Step 1: 写条款文档剥离测试**

创建 `backend/tests/test_signing_clause_document.py`：

```python
from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
from app.services.signing_region.models import SigningBlock, SigningBlockConfidenceLevel, SigningBlockRole


def _bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def test_builder_removes_high_confidence_signing_block_blocks_only() -> None:
    body = TextBlock(block_id="body", page_no=10, text="14.2 正文条款", bbox=_bbox(80, 550, 520, 590))
    signing = TextBlock(block_id="signing", page_no=10, text="甲方：A 乙方：B\n(盖章)\n(签字)\n日期：", bbox=_bbox(65, 620, 485, 730))
    page = Page(page_no=10, width=595, height=842, blocks=[body, signing])
    doc = Document(filename="x.pdf", path="x.pdf", page_count=1, pages=[page])
    block = SigningBlock(
        block_id="SB-10-1",
        page_no=10,
        bbox=_bbox(60, 610, 500, 740),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["test"],
        source_block_ids=["signing"],
        text=signing.text,
        exclude_from_clause_diff=True,
    )

    result = SigningClauseDocumentBuilder().build(doc, [block])

    assert [b.block_id for b in result.document.pages[0].blocks] == ["body"]
    assert result.excluded_block_ids == ["signing"]
    assert result.entries[0]["reason"] == "high_confidence_signing_block"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_clause_document.py -q
```

Expected: FAIL，原因是 `clause_document.py` 不存在。

- [ ] **Step 3: 实现条款文档构建器**

创建 `backend/app/services/signing_region/clause_document.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import SigningBlock


@dataclass
class SigningClauseDocumentResult:
    document: Document
    excluded_block_ids: list[str] = field(default_factory=list)
    entries: list[dict[str, Any]] = field(default_factory=list)


class SigningClauseDocumentBuilder:
    overlap_threshold = 0.55

    def build(self, document: Document, signing_blocks: list[SigningBlock]) -> SigningClauseDocumentResult:
        blocks_by_page: dict[int, list[SigningBlock]] = {}
        for block in signing_blocks:
            if block.exclude_from_clause_diff:
                blocks_by_page.setdefault(block.page_no, []).append(block)

        excluded: list[str] = []
        entries: list[dict[str, Any]] = []
        new_pages: list[Page] = []
        for page in document.pages:
            signing_page_blocks = blocks_by_page.get(page.page_no, [])
            kept_blocks: list[TextBlock] = []
            for text_block in page.blocks:
                matched = self._matching_signing_block(text_block, signing_page_blocks)
                if matched is None:
                    kept_blocks.append(text_block)
                    continue
                excluded.append(text_block.block_id)
                entries.append({
                    "page_no": page.page_no,
                    "block_id": text_block.block_id,
                    "signing_block_id": matched.block_id,
                    "reason": "high_confidence_signing_block",
                })
            new_pages.append(page.model_copy(update={"blocks": kept_blocks}))

        return SigningClauseDocumentResult(
            document=document.model_copy(update={"pages": new_pages}),
            excluded_block_ids=excluded,
            entries=entries,
        )

    def _matching_signing_block(self, text_block: TextBlock, signing_blocks: list[SigningBlock]) -> SigningBlock | None:
        for signing_block in signing_blocks:
            if text_block.block_id in signing_block.source_block_ids:
                return signing_block
            if self._overlap_ratio(text_block.bbox, signing_block.bbox) >= self.overlap_threshold:
                return signing_block
        return None

    @staticmethod
    def _overlap_ratio(a: BBox, b: BBox) -> float:
        x0 = max(a.x0, b.x0)
        y0 = max(a.y0, b.y0)
        x1 = min(a.x1, b.x1)
        y1 = min(a.y1, b.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        base = max(1.0, (a.x1 - a.x0) * (a.y1 - a.y0))
        return inter / base
```

- [ ] **Step 4: 运行条款文档测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_clause_document.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/signing_region/clause_document.py backend/tests/test_signing_clause_document.py
git commit -m "feat: build clause document without signing blocks"
```

---

### Task 5: 流水线接入签署块和条款文档

**Files:**
- Modify: `backend/app/services/pipeline.py`
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: 写流水线剥离测试**

追加到 `backend/tests/test_signing_region_pipeline.py`：

```python
def test_signing_stage_sets_clause_documents_without_signing_blocks(tmp_path: Path) -> None:
    original = Document(
        filename="o.pdf",
        path="o.pdf",
        page_count=1,
        pages=[
            Page(page_no=10, width=595, height=842, blocks=[
                TextBlock(block_id="body", page_no=10, text="14.2 正文条款", bbox=BBox(x0=80, y0=550, x1=520, y1=590)),
                TextBlock(block_id="sign", page_no=10, text="甲方：A 乙方：B\n(盖章)\n(签字)\n日期：", bbox=BBox(x0=65, y0=620, x1=485, y1=730)),
            ])
        ],
    )
    compare = original.model_copy(deep=True)
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

    SigningRegionStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"), visual_enabled=False).execute(ctx)

    assert ctx.clause_document_original is not None
    assert [block.block_id for block in ctx.clause_document_original.pages[0].blocks] == ["body"]
    assert ctx.signing_region_debug["clause_exclusion"]["original"][0]["block_id"] == "sign"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_stage_sets_clause_documents_without_signing_blocks -q
```

Expected: FAIL，原因是 `PipelineContext.clause_document_original` 不存在或 stage 未设置。

- [ ] **Step 3: 扩展 `PipelineContext`**

在 `backend/app/services/pipeline.py` 的 `PipelineContext` 增加字段：

```python
    signing_pages_original: list[Any] = field(default_factory=list)
    signing_pages_compare: list[Any] = field(default_factory=list)
    signing_blocks_original: list[Any] = field(default_factory=list)
    signing_blocks_compare: list[Any] = field(default_factory=list)
    clause_document_original: Any | None = None
    clause_document_compare: Any | None = None
```

- [ ] **Step 4: 接入 `SigningRegionStage`**

在 `backend/app/services/pipeline_stages.py` 中导入：

```python
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
```

在 `SigningRegionStage.__init__` 中增加：

```python
        self.block_detector = SigningBlockDetector()
        self.clause_document_builder = SigningClauseDocumentBuilder()
```

将 `execute()` 中原来的：

```python
        original_regions = self.extractor.extract(extractions.original.document)
        compare_regions = self.extractor.extract(extractions.compare.document)
```

替换为：

```python
        original_structure = self.block_detector.detect(extractions.original.document)
        compare_structure = self.block_detector.detect(extractions.compare.document)
        original_regions = self.extractor.extract_from_blocks(original_structure.blocks)
        compare_regions = self.extractor.extract_from_blocks(compare_structure.blocks)
        original_clause_doc = self.clause_document_builder.build(extractions.original.document, original_structure.blocks)
        compare_clause_doc = self.clause_document_builder.build(extractions.compare.document, compare_structure.blocks)
        ctx.signing_pages_original = original_structure.pages
        ctx.signing_pages_compare = compare_structure.pages
        ctx.signing_blocks_original = original_structure.blocks
        ctx.signing_blocks_compare = compare_structure.blocks
        ctx.clause_document_original = original_clause_doc.document
        ctx.clause_document_compare = compare_clause_doc.document
```

在 debug payload 中增加：

```python
            "signing_pages": {
                "original": _jsonable(original_structure.pages),
                "compare": _jsonable(compare_structure.pages),
            },
            "signing_blocks": {
                "original": _jsonable(original_structure.blocks),
                "compare": _jsonable(compare_structure.blocks),
            },
            "excluded_candidates": {
                "original": original_structure.excluded_candidates,
                "compare": compare_structure.excluded_candidates,
            },
            "low_confidence_candidates": {
                "original": original_structure.low_confidence_candidates,
                "compare": compare_structure.low_confidence_candidates,
            },
            "clause_exclusion": {
                "original": original_clause_doc.entries,
                "compare": compare_clause_doc.entries,
            },
```

- [ ] **Step 5: 运行流水线签署测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py -q
```

Expected: PASS 或只出现断言需要按新 debug 字段调整。若旧测试期望 `original_regions` 仍存在，应保留该字段，不删除。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/pipeline.py backend/app/services/pipeline_stages.py backend/tests/test_signing_region_pipeline.py
git commit -m "feat: wire signing blocks into pipeline"
```

---

### Task 6: SplitStage 使用剥离后的条款文档

**Files:**
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: 写 SplitStage 使用 clause document 测试**

追加到 `backend/tests/test_signing_region_pipeline.py`：

```python
def test_split_stage_uses_clause_documents_when_available(tmp_path: Path) -> None:
    class _Splitter:
        def __init__(self) -> None:
            self.seen_filenames: list[str] = []

        def split(self, document: Document, prefix: str):
            self.seen_filenames.append(document.filename)
            return []

    ctx = _ctx(tmp_path)
    original = Document(filename="original-full.pdf", path="o.pdf", page_count=1, pages=[])
    compare = Document(filename="compare-full.pdf", path="c.pdf", page_count=1, pages=[])
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")
    ctx.clause_document_original = Document(filename="original-clause.pdf", path="o.pdf", page_count=1, pages=[])
    ctx.clause_document_compare = Document(filename="compare-clause.pdf", path="c.pdf", page_count=1, pages=[])
    splitter = _Splitter()
    stage = SplitStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"))
    stage.splitter = splitter

    stage.execute(ctx)

    assert splitter.seen_filenames == ["original-clause.pdf", "compare-clause.pdf"]
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_split_stage_uses_clause_documents_when_available -q
```

Expected: FAIL，当前 `SplitStage` 使用 extraction.document。

- [ ] **Step 3: 修改 `SplitStage.execute`**

在 `backend/app/services/pipeline_stages.py` 中将：

```python
        original_doc = extractions.original.document
        compare_doc = extractions.compare.document
```

替换为：

```python
        original_doc = ctx.clause_document_original or extractions.original.document
        compare_doc = ctx.clause_document_compare or extractions.compare.document
```

- [ ] **Step 4: 运行 SplitStage 测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_split_stage_uses_clause_documents_when_available -q
```

Expected: PASS。

- [ ] **Step 5: 运行 pipeline 相关测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py backend/tests/test_pipeline.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/pipeline_stages.py backend/tests/test_signing_region_pipeline.py
git commit -m "feat: split clauses from signing-stripped documents"
```

---

### Task 7: 支持跨页签署匹配和跨页标题

**Files:**
- Modify: `backend/app/services/signing_region/matcher.py`
- Modify: `backend/app/services/signing_region/diff_builder.py`
- Modify: `backend/tests/test_signing_region_comparison.py`

- [ ] **Step 1: 写跨页匹配测试**

追加到 `backend/tests/test_signing_region_comparison.py`：

```python
def test_matcher_pairs_adjacent_page_signing_blocks_by_text_role_even_when_position_moves() -> None:
    original = [_region("O1", "甲方：A 乙方：B (盖章) (签字) 日期：", page_no=10, x0=60)]
    compare = [_region("C1", "甲方：A 乙方：B (盖章) (签字) 日期：2026.", page_no=11, x0=60)]
    original[0].signing_block_id = "SB-10-1"
    compare[0].signing_block_id = "SB-11-1"
    original[0].bbox = BBox(x0=60, y0=620, x1=520, y1=740)
    compare[0].bbox = BBox(x0=60, y0=60, x1=520, y1=180)

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs[0][0] is original[0]
    assert pairs[0][1] is compare[0]
    assert pairs[0][2] >= 0.55


def test_diff_builder_title_shows_original_and_compare_pages_for_cross_page_match() -> None:
    comparison = SigningRegionComparator().compare(
        _region("O1", "日期：", page_no=10),
        _region("C1", "日期：2026.", page_no=11),
        match_confidence=0.8,
    )

    diff = SigningRegionDiffBuilder().build_diffs([comparison], start_index=1)[0]

    assert diff.title == "签署区（原第10页 / 新第11页）"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_comparison.py::test_matcher_pairs_adjacent_page_signing_blocks_by_text_role_even_when_position_moves backend/tests/test_signing_region_comparison.py::test_diff_builder_title_shows_original_and_compare_pages_for_cross_page_match -q
```

Expected: FAIL，当前 matcher 要求位置强相似，title 只显示单页。

- [ ] **Step 3: 修改 matcher 评分**

在 `backend/app/services/signing_region/matcher.py` 中：

```python
    def _score(self, original: SigningRegion, compare: SigningRegion) -> float:
        page_score = self._page_score(original, compare)
        if page_score == 0.0:
            return 0.0
        text_score = self._text_structure_score(original, compare)
        iou_score = self._iou(original, compare)
        position_score = self._position_score(original, compare)
        role_score = 1.0 if original.region_role == compare.region_role else 0.4
        if iou_score == 0.0 and position_score < self.strong_position_threshold and text_score < 0.7:
            return 0.0
        return round(page_score * 0.25 + role_score * 0.2 + iou_score * 0.15 + position_score * 0.1 + text_score * 0.3, 4)

    @staticmethod
    def _text_structure_score(original: SigningRegion, compare: SigningRegion) -> float:
        def tokens(region: SigningRegion) -> set[str]:
            text = "".join(element.text for element in region.elements)
            result: set[str] = set()
            for token in ("甲方", "乙方", "盖章", "签字", "日期", "法人", "授权"):
                if token in text:
                    result.add(token)
            return result

        left = tokens(original)
        right = tokens(compare)
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)
```

- [ ] **Step 4: 修改 diff title**

在 `backend/app/services/signing_region/diff_builder.py` 中替换 `_title`：

```python
    @staticmethod
    def _title(comparison: SigningRegionComparison) -> str:
        original = comparison.original_region
        compare = comparison.compare_region
        if original is not None and compare is not None and original.page_no != compare.page_no:
            return f"签署区（原第{original.page_no}页 / 新第{compare.page_no}页）"
        region = original or compare
        page_no = region.page_no if region is not None else 0
        return f"签署区（第{page_no}页）"
```

- [ ] **Step 5: 运行匹配测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_comparison.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/signing_region/matcher.py backend/app/services/signing_region/diff_builder.py backend/tests/test_signing_region_comparison.py
git commit -m "feat: match signing regions across adjacent pages"
```

---

### Task 8: OpenCV 视觉辅助稳定降级和基础 hash

**Files:**
- Modify: `backend/app/services/signing_region/visual.py`
- Modify: `backend/tests/test_signing_region_visual.py`

- [ ] **Step 1: 写 OpenCV 不可用降级测试**

追加到 `backend/tests/test_signing_region_visual.py`：

```python
from pathlib import Path

from app.models import BBox
from app.services.signing_region.models import SigningRegion
from app.services.signing_region.visual import OpenCvSigningRegionFingerprinter


def test_opencv_fingerprinter_returns_unavailable_for_missing_pdf() -> None:
    region = SigningRegion(
        region_id="SR-1",
        page_no=1,
        bbox=BBox(x0=60, y0=620, x1=520, y1=740),
        confidence=0.9,
    )

    result = OpenCvSigningRegionFingerprinter().fingerprint_region(Path("missing.pdf"), region)

    assert result["status"] == "unavailable"
    assert result["reason"] == "pdf_missing"
```

- [ ] **Step 2: 运行测试确认当前实现失败**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_visual.py::test_opencv_fingerprinter_returns_unavailable_for_missing_pdf -q
```

Expected: 当前返回 `fingerprint_not_configured`，测试 FAIL。

- [ ] **Step 3: 实现基础降级和 hash**

在 `backend/app/services/signing_region/visual.py` 中替换 `OpenCvSigningRegionFingerprinter.fingerprint_region`：

```python
    def fingerprint_region(self, pdf_path: Path, region: SigningRegion) -> dict[str, str | float | bool]:
        if not pdf_path.exists():
            return {"status": "unavailable", "reason": "pdf_missing"}
        try:
            import fitz
        except Exception:
            return {"status": "unavailable", "reason": "pymupdf_unavailable"}
        try:
            doc = fitz.open(pdf_path)
            if region.page_no < 1 or region.page_no > len(doc):
                return {"status": "unavailable", "reason": "page_out_of_range"}
            page = doc[region.page_no - 1]
            rect = fitz.Rect(region.bbox.x0, region.bbox.y0, region.bbox.x1, region.bbox.y1)
            pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            payload = pix.samples
        except Exception:
            return {"status": "unavailable", "reason": "render_failed"}
        finally:
            try:
                doc.close()
            except Exception:
                pass
        import hashlib

        digest = hashlib.sha256(payload).hexdigest()[:24]
        return {
            "status": "ok",
            "hash": digest,
            "visual_hash": digest,
            "width": float(pix.width),
            "height": float(pix.height),
        }
```

不要在本任务中强依赖 `cv2`；红章检测可在后续小步加入，避免环境差异阻塞主流程。

- [ ] **Step 4: 运行视觉测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_visual.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/signing_region/visual.py backend/tests/test_signing_region_visual.py
git commit -m "feat: add signing region visual fingerprint"
```

---

### Task 9: 回归当前任务形态的端到端单元测试

**Files:**
- Modify: `backend/tests/test_signing_region_pipeline.py`

- [ ] **Step 1: 写任务形态回归测试**

追加到 `backend/tests/test_signing_region_pipeline.py`：

```python
def test_signing_pipeline_excludes_cover_table_and_strips_real_signing_blocks(tmp_path: Path) -> None:
    cover_table = TextBlock(
        block_id="cover_table",
        page_no=1,
        text="甲方\n江苏东大金智信息系统有限公司\n乙方\n国能日新科技股份有限公司\n北京\n签订地点\n签订日期\n2026年4月21日",
        bbox=BBox(x0=90, y0=560, x1=505, y1=690),
        block_type="table",
    )
    original_signing = TextBlock(
        block_id="o_signing",
        page_no=10,
        text="甲方：江苏东大金智信息系统有限公司 乙方：国能日新科技股份有限公司\n(盖章)\n法人代表或授权委托人：\n(签字)\n日期：",
        bbox=BBox(x0=65, y0=620, x1=485, y1=730),
    )
    compare_signing = TextBlock(
        block_id="c_signing",
        page_no=11,
        text="甲方：江苏东达金智信息系统有限公司 乙方：国能日新科技股份有限公司\n(盖章)\n法人代表或\n日期：2026.",
        bbox=BBox(x0=60, y0=60, x1=500, y1=180),
    )
    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=10,
        pages=[
            Page(page_no=1, width=595, height=842, blocks=[cover_table]),
            Page(page_no=10, width=595, height=842, blocks=[
                TextBlock(block_id="o_body", page_no=10, text="14.2 正文条款", bbox=BBox(x0=80, y0=550, x1=520, y1=590)),
                original_signing,
            ]),
        ],
        profile=DocumentProfile(
            filename="original.pdf",
            page_count=10,
            page_profiles=[
                PageProfile(page_no=1, width=595, height=842, page_role="cover"),
                PageProfile(page_no=10, width=595, height=842, page_role="body"),
            ],
        ),
    )
    compare = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=11,
        pages=[
            Page(page_no=1, width=595, height=842, blocks=[cover_table.model_copy(update={"block_id": "compare_cover_table"})]),
            Page(page_no=11, width=595, height=842, blocks=[compare_signing]),
        ],
        profile=DocumentProfile(
            filename="compare.pdf",
            page_count=11,
            page_profiles=[
                PageProfile(page_no=1, width=595, height=842, page_role="cover"),
                PageProfile(page_no=11, width=595, height=842, page_role="body"),
            ],
        ),
    )
    ctx = _ctx(tmp_path)
    ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
    ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

    SigningRegionStage(artifact_store=_TestArtifactStore(tmp_path / "artifacts"), visual_enabled=False).execute(ctx)

    assert all(region.page_no != 1 for region in ctx.signing_regions_original)
    assert ctx.signing_blocks_original[0].page_no == 10
    assert ctx.signing_blocks_compare[0].page_no == 11
    assert "cover_signing_info_table" in {
        item["reason"] for item in ctx.signing_region_debug["excluded_candidates"]["original"]
    }
    assert [block.block_id for block in ctx.clause_document_original.pages[1].blocks] == ["o_body"]
```

- [ ] **Step 2: 运行回归测试**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_pipeline.py::test_signing_pipeline_excludes_cover_table_and_strips_real_signing_blocks -q
```

Expected: PASS。

- [ ] **Step 3: 运行签章测试全集**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests/test_signing_region_extractor.py backend/tests/test_signing_region_comparison.py backend/tests/test_signing_region_pipeline.py backend/tests/test_signing_region_visual.py backend/tests/test_signing_structure_detector.py backend/tests/test_signing_clause_document.py -q
```

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add backend/tests/test_signing_region_pipeline.py
git commit -m "test: cover signing block regression"
```

---

### Task 10: 全量验证和修正

**Files:**
- Modify only files required by failing tests.

- [ ] **Step 1: 运行后端测试全集**

Run:

```bash
env -u VIRTUAL_ENV uv run pytest backend/tests -q
```

Expected: PASS。

- [ ] **Step 2: 运行 ruff**

Run:

```bash
env -u VIRTUAL_ENV uv run ruff check backend/app backend/tests
```

Expected: `All checks passed!`

- [ ] **Step 3: 运行前端测试**

Run:

```bash
cd frontend && npm test -- --run
```

Expected: PASS。

- [ ] **Step 4: 运行前端构建**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS。

- [ ] **Step 5: 修复验证中暴露的问题**

如果某个测试失败，只修改对应功能文件和测试文件。不要改动无关 Docker、前端环境配置或 `storage/` 产物。

- [ ] **Step 6: 最终提交**

如果 Step 5 有修复：

```bash
git add backend/app/services/signing_region backend/app/services/pipeline.py backend/app/services/pipeline_stages.py backend/tests
git commit -m "fix: stabilize signing structure comparison"
```

如果 Step 5 没有修复，不创建空提交。

---

## 自查清单

- [ ] spec 中“封面签约信息表排除”由 Task 2 和 Task 9 覆盖。
- [ ] spec 中“真实签署块识别”由 Task 2 和 Task 9 覆盖。
- [ ] spec 中“正文剥离”由 Task 4、Task 5、Task 6 覆盖。
- [ ] spec 中“跨页匹配”由 Task 7 覆盖。
- [ ] spec 中“OpenCV 不需要训练数据，稳定降级”由 Task 8 覆盖。
- [ ] spec 中“debug 可解释”由 Task 5 和 Task 9 覆盖。
- [ ] 没有引入 Docling、Surya、LayoutParser 或训练模型依赖。
- [ ] 没有针对任务 ID、页码、公司名称写硬编码逻辑。
