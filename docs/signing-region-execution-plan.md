# 独立签章区对比 — 执行计划 v2

## Context

当前签章对比仅比较印章 OCR 文本（`seal_comparator.py`），缺少"签章区"整体语义概念。目标：将签章区作为独立语义单元提取、对比、独立分组展示。设计文档：`docs/signing-region-independent-comparison-plan.md`

## 关键设计决策（本轮确认）

1. **Stage 写法**：`PipelineStage` 是 `Protocol`，不是基类。Stage 使用**类属性** `name/start_progress/progress`，无 `super().__init__()`。
2. **模型位置**：核心 Pydantic 模型放 `app/models.py`（跨模块使用），内部辅助类型放 `services/signing_region/models.py`。
3. **匹配算法**：签章区数量极少（1-5个），用纯 Python greedy（mutual-best-first + fill），复用 `matcher.py:_select_greedy_candidates` 模式，零外部依赖。
4. **视觉哈希**：本轮跳过。不引入 `opencv-contrib-python` 依赖。
5. **正文污染**：签章区内的 text block（标签、日期等）会在 SplitStage 进入条款分割，造成正文差异流重复污染。解决方案：SigningRegionStage 检测完成后，将签章区内的 block 标记 `enter_clause_compare = False`，利用现有的 `ClauseSplitter._collect_units()` L153 过滤机制自动排除。

---

## Phase 1：签章区检测（模型 + 检测器 + Stage + 掩码）

### Task 1 — 数据模型

**修改**：`backend/app/models.py`

新增 Pydantic 模型（跨模块使用，放在 `app/models.py`）：

```python
class SigningElementType(str, Enum):
    SEAL = "seal"
    SIGNATURE = "signature"
    LABEL = "label"
    DATE_FIELD = "date_field"
    TABLE = "table"

class SigningElement(BaseModel):
    element_type: SigningElementType
    bbox: BBox
    page_no: int
    text: str = ""
    confidence: float = 1.0
    label_role: str = ""

class SigningRegion(BaseModel):
    region_id: str
    page_no: int
    bbox: BBox
    elements: list[SigningElement] = []
    region_type: str = ""
    confidence: float = 0.0
    party_name: str = ""

class SigningRegionDiff(BaseModel):
    region_diff_id: str
    original_region: SigningRegion | None = None
    compare_region: SigningRegion | None = None
    match_method: str = ""
    match_confidence: float = 0.0
    seal_diffs: list[dict] = []
    signature_diffs: list[dict] = []
    label_diffs: list[dict] = []
    date_diffs: list[dict] = []
    structural_diff: str = ""
```

同时修改：
- L15 `DiffSourceType` 追加 `"signing_region"`
- L348 `CompareOptions` 新增 `signing_region_mode: Literal["full","seals_only","off"] = "full"`

**新建**：`backend/app/services/signing_region/models.py`

仅放内部辅助类型（如 match pair、score detail 等，不跨模块引用）：

```python
@dataclass
class RegionMatchPair:
    original: SigningRegion | None
    compare: SigningRegion | None
    score: float = 0.0
    method: str = ""
```

**修改**：`backend/app/services/pipeline.py` — PipelineContext（L75 之后插入）：

```python
signing_regions_original: list = field(default_factory=list)
signing_regions_compare: list = field(default_factory=list)
signing_region_diffs: list = field(default_factory=list)
```

**验证**：

```bash
python -c "from app.models import SigningRegion, SigningElement, SigningRegionDiff; print('OK')"
```

---

### Task 2 — SigningRegionDetector

**新建**：`backend/app/services/signing_region/__init__.py`（空文件）

**新建**：`backend/app/services/signing_region/detector.py`

参考 `matcher.py:_select_greedy_candidates` 的纯 Python 模式，零外部依赖。

```python
import re
from app.models import Document, Page, TextBlock, BBox, SigningRegion, SigningElement, SigningElementType

class SigningRegionDetector:
    PAGE_BOTTOM_RATIO: float = 0.65
    CLUSTER_Y_GAP: float = 60.0
    BBOX_PADDING: float = 20.0

    SIGNING_ANCHORS = re.compile(
        r'甲方|乙方|丙方|买方|卖方|委托方|受托方|'
        r'盖章|签章|签字|签署|'
        r'法定代表人|授权代表|经办人|'
        r'日期|年月日'
    )
    BODY_PREDICATES = re.compile(r'应当|负责|承担|协商|约定|履行|支付|提供|交付')
    NUMBERED_CLAUSE = re.compile(r'第[一二三四五六七八九十百千\d]+条|[（(][一二三四五六七八九十]+[）)]')
    DATE_PATTERN = re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|年月日')

    # 签章标签关键词（用于 label_role 判定）
    LABEL_ROLE_PATTERNS = {
        'party_a': re.compile(r'甲方|买方|委托方'),
        'party_b': re.compile(r'乙方|卖方|受托方'),
        'stamp': re.compile(r'盖章|签章'),
        'sign': re.compile(r'签字|签署|签名'),
        'legal_rep': re.compile(r'法定代表人'),
        'authorized_rep': re.compile(r'授权代表'),
        'date': re.compile(r'日期|年月日'),
    }

    def detect(self, document: Document) -> list[SigningRegion]:
        """主入口"""
        regions: list[SigningRegion] = []
        for page in document.pages:
            candidates = self._collect_candidates(page)
            if not candidates:
                continue
            clusters = self._cluster_by_y(candidates)
            for cluster in clusters:
                region = self._build_region(cluster, page)
                if region is not None:
                    regions.append(region)
        return regions

    def _collect_candidates(self, page: Page) -> list[TextBlock]:
        """筛选候选块：位置 + 信号 + 负面约束"""
        candidates: list[TextBlock] = []
        bottom_threshold = page.height * self.PAGE_BOTTOM_RATIO
        for block in page.blocks:
            # 页面位置过滤
            if block.bbox.y0 < bottom_threshold:
                continue
            # 已有 block_type == "seal" → 直接候选
            if (block.block_type or "").lower() in {"seal", "stamp"}:
                candidates.append(block)
                continue
            # 图像块在底部 → 候选（潜在签名）
            if (block.block_type or "").lower() == "image":
                candidates.append(block)
                continue
            # 文本块需要多信号验证
            text = block.text or ""
            if not text:
                continue
            # 关键词锚点
            if not self.SIGNING_ANCHORS.search(text):
                continue
            # 负面约束：正文谓语
            if self.BODY_PREDICATES.search(text):
                continue
            # 负面约束：编号条款
            if self.NUMBERED_CLAUSE.search(text):
                continue
            # 负面约束：长文本
            if len(text) > 200:
                continue
            candidates.append(block)
        return candidates

    def _cluster_by_y(self, blocks: list[TextBlock]) -> list[list[TextBlock]]:
        """按 y 坐标聚类"""
        sorted_blocks = sorted(blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
        clusters: list[list[TextBlock]] = []
        current: list[TextBlock] = []
        for block in sorted_blocks:
            if not current:
                current.append(block)
                continue
            # 与当前簇最后一个块的 y 间距
            last = current[-1]
            if abs(block.bbox.y0 - last.bbox.y0) <= self.CLUSTER_Y_GAP:
                current.append(block)
            else:
                clusters.append(current)
                current = [block]
        if current:
            clusters.append(current)
        return clusters

    def _classify_element(self, block: TextBlock) -> SigningElementType:
        """单块类型判定"""
        block_type = (block.block_type or "").lower()
        if block_type in {"seal", "stamp"}:
            return SigningElementType.SEAL
        if block_type == "image":
            return SigningElementType.SIGNATURE
        text = block.text or ""
        if self.DATE_PATTERN.search(text) and len(text) <= 30:
            return SigningElementType.DATE_FIELD
        if self.SIGNING_ANCHORS.search(text) and len(text) <= 50:
            return SigningElementType.LABEL
        return SigningElementType.LABEL  # 默认按 label 处理

    def _resolve_label_role(self, text: str) -> str:
        """解析签章标签角色"""
        roles = []
        for role, pattern in self.LABEL_ROLE_PATTERNS.items():
            if pattern.search(text):
                roles.append(role)
        return roles[0] if roles else ""

    def _classify_region(self, elements: list[SigningElement], page_width: float) -> str:
        """区域角色判定"""
        if not elements:
            return "full_width"
        center_x = sum(e.bbox.x0 + e.bbox.x1 for e in elements) / (2 * len(elements))
        if center_x < page_width * 0.33:
            return "left_party"
        if center_x > page_width * 0.67:
            return "right_party"
        return "full_width"

    def _build_region(
        self, blocks: list[TextBlock], page: Page
    ) -> SigningRegion | None:
        """构造 SigningRegion"""
        if not blocks:
            return None
        elements: list[SigningElement] = []
        for block in blocks:
            etype = self._classify_element(block)
            element = SigningElement(
                element_type=etype,
                bbox=block.bbox,
                page_no=block.page_no,
                text=block.text or "",
                label_role=self._resolve_label_role(block.text or ""),
            )
            elements.append(element)

        # 计算 union bbox + padding
        union = BBox(
            x0=min(e.bbox.x0 for e in elements) - self.BBOX_PADDING,
            y0=min(e.bbox.y0 for e in elements) - self.BBOX_PADDING,
            x1=max(e.bbox.x1 for e in elements) + self.BBOX_PADDING,
            y1=max(e.bbox.y1 for e in elements) + self.BBOX_PADDING,
        )
        region_type = self._classify_region(elements, page.width)

        # 推断 party_name
        party_labels = [e.text for e in elements if e.element_type == SigningElementType.LABEL]
        party_name = party_labels[0] if party_labels else ""

        return SigningRegion(
            region_id=f"SR-{page.page_no}-{len(page.blocks)}",
            page_no=page.page_no,
            bbox=union,
            elements=elements,
            region_type=region_type,
            party_name=party_name,
        )
```

---

### Task 3 — SigningRegionStage + 块掩码 + 测试

**修改**：`backend/app/services/pipeline_stages.py`

新增 `SigningRegionStage`（注意：Protocol 模式，用类属性，不用 `super().__init__()`）：

```python
class SigningRegionStage:
    name = "签章区域检测中"
    start_progress = 37
    progress = 40

    def __init__(self) -> None:
        self.detector = SigningRegionDetector()

    def execute(self, ctx: PipelineContext) -> None:
        mode = ctx.task.compare_options.signing_region_mode
        if mode == "off":
            ctx.signing_regions_original = []
            ctx.signing_regions_compare = []
            ctx.signing_region_diffs = []
            return

        # Step 1: 检测签章区
        doc_orig = ctx.original_extraction.document
        doc_comp = ctx.compare_extraction.document
        ctx.signing_regions_original = self.detector.detect(doc_orig)
        ctx.signing_regions_compare = self.detector.detect(doc_comp)

        # Step 2: 块掩码 — 将签章区内的 block 标记为不进入条款分割
        #    利用 ClauseSplitter._collect_units() L153 的 enter_clause_compare 过滤
        self._mask_signing_blocks(doc_orig, ctx.signing_regions_original)
        self._mask_signing_blocks(doc_comp, ctx.signing_regions_compare)

        # Step 3: 如果 mode == "seals_only"，Phase 2 不做完整对比
        #    (在 Phase 2 的 execute 中处理)
        _emit_progress(ctx, 40, self.name, "signing_region_detection_done")

    def _mask_signing_blocks(
        self, document: Document, regions: list[SigningRegion]
    ) -> None:
        """将签章区内的所有 block 标记为不进入条款分割"""
        if not regions:
            return
        for page in document.pages:
            page_regions = [r for r in regions if r.page_no == page.page_no]
            if not page_regions:
                continue
            for block in page.blocks:
                for region in page_regions:
                    if self._bbox_overlap(block.bbox, region.bbox):
                        block.enter_clause_compare = False
                        break

    @staticmethod
    def _bbox_overlap(a: BBox, b: BBox) -> bool:
        """判断两个 bbox 是否有重叠"""
        return not (a.x1 < b.x0 or b.x1 < a.x0 or a.y1 < b.y0 or b.y1 < a.y0)
```

同步调整 `PreClauseDiffStage`：`start_progress=36, progress=37`（原 `progress=40`）。

**修改**：`backend/app/services/pipeline.py` — `_default_stages()`，`PreClauseDiffStage()` 之后插入 `SigningRegionStage()`。

**新建**：`backend/tests/test_signing_region_detection.py`

复用 `test_seal_comparator.py` 的 `_block()` / `_document()` 辅助函数模式。测试用例（12 个）：

| # | 场景 | 预期 |
|---|------|------|
| 1 | 底部"甲方(盖章)"+seal 块 | 1 个 SigningRegion，含 label+seal |
| 2 | 正文中"甲方应当履行" | 无 SigningRegion |
| 3 | "第一条 甲方权利义务"含关键词 | 无 SigningRegion |
| 4 | 同页左右两列签章 | 2 个 SigningRegion，left_party+right_party |
| 5 | 完整签署页（标签+日期+印章） | 含 5 种元素 |
| 6 | 纯正文页面 | 无 SigningRegion |
| 7 | 底部日期字段 | DATE_FIELD 元素 |
| 8 | 底部图像块 | SIGNATURE 元素 |
| 9 | block_type="seal" 孤立在底部 | 仍检测为 SEAL 元素 |
| 10 | 长文本(>200)+关键词在底部 | 不识别为签章区 |
| 11 | 多页文档仅末页签章 | 仅末页检测到 |
| 12 | **块掩码验证**：签章区 block 的 enter_clause_compare 被设为 False | `block.enter_clause_compare is False` |

**验证**：

```bash
python -m pytest backend/tests/test_signing_region_detection.py -v  # 12 passed
```

---

## Phase 2：签章区对比

### Task 4 — SigningRegionMatcher（纯 Python greedy）

**新建**：`backend/app/services/signing_region/matcher.py`

复用 `matcher.py:_select_greedy_candidates` 的模式，零外部依赖：

```python
class SigningRegionMatcher:
    IOU_THRESHOLD: float = 0.2

    def match(
        self,
        original: list[SigningRegion],
        compare: list[SigningRegion],
    ) -> list[tuple[SigningRegion | None, SigningRegion | None]]:
        # 1. 按 page_no 分组（允许 ±1 页偏移）
        # 2. 对每组内所有区域对计算得分：
        #    score = iou × 0.4 + role_match × 0.3 + label_sim × 0.3
        # 3. 排序 + mutual-best-first greedy
        # 4. 填充剩余（单边独占）
        # 5. 未匹配 → (region, None) 或 (None, region)

    def _score_pair(self, a: SigningRegion, b: SigningRegion) -> float:
        iou = _bbox_iou(a.bbox, b.bbox)
        role = 1.0 if a.region_type == b.region_type else 0.0
        label = self._label_similarity(a, b)
        return iou * 0.4 + role * 0.3 + label * 0.3

    def _label_similarity(self, a: SigningRegion, b: SigningRegion) -> float:
        # 使用 difflib.SequenceMatcher 比较 party_name
```

签章区数量极少（1-5个），greedy O(n²) 完全足够。

---

### Task 5 — SigningRegionComparator + DiffBuilder

**新建**：`backend/app/services/signing_region/comparator.py`

```python
class SigningRegionComparator:
    def compare(self, orig: SigningRegion, comp: SigningRegion) -> SigningRegionDiff:
        # 5 维对比：
        # 印章：按 bbox IoU 配对 → 比较 OCR 文本（复用 seal_comparator 的文本标准化）
        # 签名：比较 signature 元素数量
        # 标签：按 label_role 匹配 → 比较文本
        # 日期：标准化日期值比较
        # 结构：元素数量/类型分布/bbo 位置变化摘要
```

**新建**：`backend/app/services/signing_region/diff_builder.py`

```python
class SigningRegionDiffBuilder:
    def build_diffs(
        self,
        region_diffs: list[SigningRegionDiff],
        start_index: int,
    ) -> list[DiffItem]:
        # SigningRegionDiff → DiffItem 转换
        # source_type = "signing_region"
        # title = f"签章区（第{page_no}页-{party_name}）"
        # readable_change = 汇总子差异描述
        # 无实质性差异 → 跳过
```

---

### Task 6 — SigningRegionStage 完整实现 + 对比测试

**修改**：`backend/app/services/pipeline_stages.py` — SigningRegionStage.execute 补充对比逻辑：

```python
def execute(self, ctx: PipelineContext) -> None:
    # ... Step 1-2 (检测+掩码，同 Task 3) ...

    if mode == "seals_only":
        ctx.signing_region_diffs = []
        return

    # Step 3: 匹配
    matcher = SigningRegionMatcher()
    pairs = matcher.match(ctx.signing_regions_original, ctx.signing_regions_compare)

    # Step 4: 对比
    comparator = SigningRegionComparator()
    region_diffs = []
    for orig, comp in pairs:
        if orig is None:
            region_diffs.append(SigningRegionDiff(
                region_diff_id=_make_id(),
                compare_region=comp,
                match_method="unmatched_add",
            ))
        elif comp is None:
            region_diffs.append(SigningRegionDiff(
                region_diff_id=_make_id(),
                original_region=orig,
                match_method="unmatched_delete",
            ))
        else:
            region_diffs.append(comparator.compare(orig, comp))

    # Step 5: 转 DiffItem
    pre_count = (
        len(ctx.header_footer_diffs) + len(ctx.metadata_diffs)
        + len(ctx.table_diffs) + len(ctx.seal_diffs)
    )
    builder = SigningRegionDiffBuilder()
    ctx.signing_region_diffs = builder.build_diffs(region_diffs, pre_count + 1)
```

**修改**：`backend/app/services/pipeline_stages.py` — ClauseDiffStage

- `pre_clause_count` 加入 `len(ctx.signing_region_diffs)`
- 合并列表追加 `*ctx.signing_region_diffs`

**修改**：`backend/app/services/pipeline_stages.py` — SummaryStage._filter_compare_option_diffs

```python
if task.compare_options.signing_region_mode == "off":
    excluded_source_types.add("signing_region")
```

**新建**：`backend/tests/test_signing_region_comparison.py`

12 个测试用例，模式同 Phase 1：

| # | 场景 | 预期 |
|---|------|------|
| 1 | 印章文字相同 | 无 MODIFY diff |
| 2 | 甲方印章文字变更 | 1 MODIFY diff，seal 子差异 |
| 3 | 新版新增印章 | 1 MODIFY diff，seal ADD |
| 4 | 新版新增签名 | 1 MODIFY diff，signature ADD |
| 5 | 日期空白→已填写 | 1 MODIFY diff，date 子差异 |
| 6 | 标签文字变更 | 1 MODIFY diff，label 子差异 |
| 7 | 仅原版有签章区 | DELETE diff |
| 8 | 仅新版有签章区 | ADD diff |
| 9 | 完全相同 | 无 diff |
| 10 | ±1 页跨页匹配 | 成功匹配 |
| 11 | 结构变化 | structural_diff 非空 |
| 12 | mode="off" | 空列表 |

**验证**：

```bash
python -m pytest backend/tests/test_signing_region_comparison.py -v       # 12 passed
python -m pytest backend/tests/test_seal_comparator.py -v                 # 必须全部通过
python -m pytest backend/tests/ -v --ignore=backend/tests/test_run_quality_regression.py  # 无回归
```

---

## Phase 3：前端分组展示

### Task 7 — SIGNING 分组 + 高亮样式

**修改**：`frontend/src/pages/ResultPage.tsx`（4 处）

- L387 `AuditGroup` 追加 `"SIGNING"`
- L389-393 `auditGroupLabels` 追加 `SIGNING: "签章区差异"`
- L584 `auditGroup()` 追加 `if (diff.source_type === "signing_region") return "SIGNING";`
- L623 `groupOrder` 追加 `"SIGNING"`，顺序：`["MAIN", "SIGNING", "STRUCTURAL", "OTHER"]`

**修改**：`frontend/src/components/PdfDocumentViewer.tsx`

- `markKind` 类型追加 `"signing_region"`
- `highlightMarkKind()` 追加对应 judgment

**验证**：

```bash
cd frontend && npm test && npm run build
```

---

## Phase 4：精细化

### Task 8 — 配置开关 + 质量守卫

**修改**：`backend/app/api.py` — compare 端点传递 `signing_region_mode`

**修改**：`backend/app/services/diff_quality.py` — 签章区质量守卫（可选，延后）

**修改**：`frontend/src/pages/UploadPage.tsx` — 签章区模式选择 UI（可选）

**验证**：全量测试通过。

> **跳过视觉哈希**：不引入 `opencv-contrib-python`。签章区对比不依赖图像特征即可交付核心价值。

---

## 文件变更汇总

| 文件 | 操作 | 预估行数 |
|------|:--:|:--------:|
| `backend/app/models.py` | 修改 | ~80 |
| `backend/app/services/pipeline.py` | 修改 | ~10 |
| `backend/app/services/pipeline_stages.py` | 修改 | ~100 |
| `backend/app/services/signing_region/__init__.py` | 新建 | 0 |
| `backend/app/services/signing_region/models.py` | 新建 | ~20 |
| `backend/app/services/signing_region/detector.py` | 新建 | ~150 |
| `backend/app/services/signing_region/matcher.py` | 新建 | ~80 |
| `backend/app/services/signing_region/comparator.py` | 新建 | ~120 |
| `backend/app/services/signing_region/diff_builder.py` | 新建 | ~80 |
| `frontend/src/pages/ResultPage.tsx` | 修改 | ~10 |
| `frontend/src/components/PdfDocumentViewer.tsx` | 修改 | ~5 |
| `backend/tests/test_signing_region_detection.py` | 新建 | ~200 |
| `backend/tests/test_signing_region_comparison.py` | 新建 | ~250 |

---

## 验证检查清单

### Phase 1
- [ ] `python -c "from app.models import SigningRegion, SigningElement; print('OK')"`
- [ ] `python -m pytest backend/tests/test_signing_region_detection.py -v` — 12 passed
- [ ] 签章区内的 block 标记 `enter_clause_compare = False`（测试 #12 验证）

### Phase 2
- [ ] `python -m pytest backend/tests/test_signing_region_comparison.py -v` — 12 passed
- [ ] `python -m pytest backend/tests/test_seal_comparator.py -v` — 全部通过
- [ ] `python -m pytest backend/tests/ -v` — 无回归

### Phase 3
- [ ] `cd frontend && npm test && npm run build` — 通过

### Phase 4
- [ ] `python -m pytest backend/tests/ -v` — 全部通过
- [ ] 手动测试：上传含签章区的合同对，签章区差异独立分组，正文中无重复

---

## 依赖关系

```
Task 1 (模型)
  ├── Task 2 (检测器)
  │     └── Task 3 (Stage+掩码+测试)
  │           └── Task 4 (匹配器)
  │                 └── Task 5 (比较器+DiffBuilder)
  │                       └── Task 6 (Stage完整+管线集成+对比测试)
  │                             └── Task 7 (前端)
  │                                   └── Task 8 (精细化)
```

## 对外部依赖的承诺

| 依赖 | 决策 |
|------|------|
| RapidFuzz | **不引入**，用标准库 `difflib` |
| 匈牙利算法库 | **不引入**，纯 Python greedy（复用现有模式） |
| opencv-contrib-python | **不引入**，视觉哈希跳过 |
| 新增 PyPI 包 | **零新增** |
