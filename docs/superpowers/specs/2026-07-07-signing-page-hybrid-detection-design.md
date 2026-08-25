# 签署页混合识别设计

## 背景

当前项目已经具备独立 `signing_region` 对比链路，包括签署块识别、签署区域抽取、签署区域匹配、差异构建、正文剥离和调试产物。但近期多个真实任务暴露出签署页识别仍有两类问题：

- 漏检：签署页 OCR 只保留部分字段，例如仅有 `签署页` 标题加地址/联系人/开户行，或下一页只剩地址/联系人/电话/统一社会信用代码。
- 误框：签署区 bbox padding 或候选区域过宽，覆盖到普通正文条款，例如 `14.2` 条款尾句。

这些问题本质上不是单一 OCR 或单一视觉模型能解决的，而是需要将语义规则、版面结构、视觉辅助和质量闭环组合起来。成熟合同对比系统通常采用可解释的多信号融合，而不是把签署页识别完全交给一个黑盒模型。

## 已确认决策

1. 签署页识别主链路采用规则/OCR/版面结构，不依赖外部检测服务。
2. 本地 OpenCV 作为视觉辅助，用于红章、签字痕迹、表格线、视觉密度和扫描缺损等候选增强。
3. VLM 不进入默认生产链路；只保留为后续低置信离线复核或人工辅助分析的可选方向。
4. 当前 `SIGNING_VISUAL_DETECTOR_URL` 不作为必需配置；本地 `OpenCvVisualSignatureDetector` 可以替代远程 detector。
5. OpenCV 单独命中不能直接生成最终签署区差异，必须和规则/OCR/版面证据融合。
6. 所有签署区判断必须能在 `debug/signing_region.json` 中解释，包括触发规则、视觉信号、置信度和被排除原因。

## 目标

- 提升签署页和签署块召回率，覆盖正文页底部、页首延续、独立签署页、跨页下半部分签署字段等形态。
- 减少误框和正文误剥离，避免把普通条款、封面签约信息、页眉页脚和页码纳入签署区。
- 引入本地 OpenCV 视觉辅助，但不增加外部服务部署要求。
- 为后续可选的检测模型或 VLM 留出接口，但默认链路不依赖它们。
- 让每个用户可见签署区差异都能回溯到 OCR block、视觉候选、bbox 和置信度原因。

## 非目标

- 不引入强制外部签署检测服务。
- 不在本阶段训练 YOLO、RT-DETR、PP-YOLOE 或其他检测模型。
- 不把 VLM 用于默认对比流程。
- 不实现电子签章证书链验签。
- 不用固定任务 ID、页码、公司名或合同编号写硬编码规则。
- 不让 OpenCV 替代 OCR/版面语义判断。

## 推荐架构

签署页识别采用三层融合：

```text
Document OCR/Layout
  |
  v
Rule/OCR/Layout Detector
  - 签署页标题
  - 以下无正文
  - 甲乙方/供需方/委托方/受托方
  - 盖章/签字/授权代表/日期
  - 地址/联系人/电话/开户行/账号/统一社会信用代码
  - 底部/顶部/跨页/双栏/表单结构
  |
  v
OpenCV Visual Assistant
  - 红章颜色区域
  - 手写签名/笔迹密度
  - 表格线/分栏结构
  - 候选页视觉密度
  - 已识别区域视觉 hash
  |
  v
Fusion Scorer
  - 高置信直接通过
  - 中低置信经视觉增强后通过
  - 视觉单独命中进入低置信候选
  - 低置信和冲突样本进入 debug/复核
  |
  v
SigningBlock / SigningRegion / Clause Exclusion
```

## 规则/OCR/版面主链路

规则层仍是主判定来源，因为它最可解释、可复现，也最适合处理中文合同签署页的语义字段。

### 正向信号

- 签署上下文：`签署页`、`签字页`、`以下无正文`、`本合同经双方签字盖章后生效`。
- 角色标签：`甲方`、`乙方`、`丙方`、`丁方`、`供方`、`需方`、`买方`、`卖方`、`采购方`、`供货方`、`委托方`、`受托方`。
- 签署动作：`盖章`、`签章`、`公章`、`签字`、`签名`、`法定代表人`、`授权代表`、`授权委托人`。
- 日期字段：`日期：`、`签订日期`、`签署日期`。
- 业务签署字段：`单位名称`、`地址`、`联系人`、`电话`、`传真`、`邮箱`、`开户行`、`账号`、`税号`、`纳税人识别号`、`统一社会信用代码`。
- 空间结构：页面底部签署栏、页首签署延续、双栏对称、表格式签署字段、上一页 `以下无正文` 后的近末页业务字段页。

### 负向信号

- 封面签约信息表不能仅因有甲乙方和签订日期就识别为签署区。
- 普通条款长句不能仅因包含 `签字`、`盖章`、`甲方`、`乙方` 就识别为签署区。
- 页眉、页脚、页码、目录、附件标题不能纳入签署区 bbox。
- bbox overlap fallback 必须带语义门控，不能因为 padding 覆盖普通正文就剥离正文 block。

### 典型覆盖形态

- 完整签署页：标题、甲乙方、盖章、授权代表、日期、地址等字段完整。
- 顶部签署页延续：上半页为签署栏，下方字段间隔较大。
- 业务字段退化页：只有 `签署页` 标题和地址/联系人/开户行/账号等字段。
- 跨页下半部分：上一页 `以下无正文`，下一页只剩地址/联系人/电话/信用代码。
- 供需方签署表：OCR 只露出 `供方`/`需方` 与业务表单字段。
- 终止条款后中部签署区：签署区位于最终页中部，不在页面底部。

## OpenCV 本地视觉辅助

新增 `OpenCvVisualSignatureDetector`，实现现有 `VisualSignatureDetector` 协议，但不需要远程服务。

### 输入

OpenCV detector 应支持两类输入：

- 已识别 `SigningRegion`：用于视觉 hash、红章/签字检测和差异增强。
- 候选页或候选区域：用于辅助召回漏掉的签署页，特别是末页、近末页、`以下无正文` 后一页、规则低置信候选页。

### 输出

输出仍复用 `VisualDetectionResult`：

```text
VisualDetection
  page_no
  bbox
  label: seal | signature | signing_table | visual_area
  confidence
  model_name: opencv
  raw_data
```

### 可实现的 OpenCV 信号

- 红章检测：HSV/颜色阈值提取红色连通域，过滤过小噪声和页边污点。
- 签字/手写痕迹：灰度二值化、连通域、细线密度、非印刷文本区域密度。
- 表格/签署栏结构：水平线/竖线检测、双栏布局、候选区域空白间隔。
- 扫描质量提示：大面积空白、页面裁切、倾斜、上半页缺损。
- 区域视觉 hash：对已识别签署区裁剪后生成稳定 hash，用于新旧版本视觉变化判断。

### 融合规则

- 规则高置信：直接生成 `SigningBlock`，OpenCV 只补充视觉元素。
- 规则中置信 + OpenCV 命中红章/签字/表格线：提升置信度。
- 规则低置信 + OpenCV 强命中 + 近末页/签署上下文：进入候选或生成中置信签署块。
- OpenCV 单独命中但无语义/版面支撑：只写入 `low_confidence_candidates`，不生成最终差异。
- OpenCV 无结果或不可用：主链路降级为规则识别，任务不失败。

## VLM 策略

VLM 暂不进入默认主链路。

原因：

- 成本和延迟高，不适合每个合同任务默认调用。
- 对合同和客户数据有额外隐私与合规风险。
- 输出 bbox 的稳定性和可复现性通常弱于规则 + 视觉算法。
- 对当前已发现问题帮助有限：现有问题主要是规则覆盖、bbox 边界和跨页上下文，而不是“人类看不懂页面”。
- 难以直接写出稳定的单元测试和回归断言。

允许的后续用途：


## 配置建议

保留现有配置：

```text
SIGNING_VISUAL_ENABLED=true
SIGNING_VISUAL_DETECTOR_URL=
SIGNING_VISUAL_LOCAL_MODEL_PATH=
```

新增或明确本地 OpenCV 配置：

```text
SIGNING_VISUAL_BACKEND=opencv
SIGNING_OPENCV_DETECT_RED_SEAL=true
SIGNING_OPENCV_DETECT_HANDWRITING=true
SIGNING_OPENCV_SCAN_CANDIDATE_PAGES=true
SIGNING_OPENCV_MIN_CONFIDENCE=0.55
```

默认行为：

- 未配置远程 URL 时，不报 `visual_detector_not_configured` 作为异常；默认启用本地 OpenCV detector。
- 如果运行环境缺少 OpenCV 或 PyMuPDF，则记录 `opencv_unavailable`，主链路继续。
- `SIGNING_VISUAL_BACKEND=remote` 时才使用 `SIGNING_VISUAL_DETECTOR_URL`。

## 调试产物

`debug/signing_region.json` 应包含：

```text
configuration
  visual_enabled
  visual_backend
  visual_detector
  opencv_available

visual_adapter_status
  available
  error
  detection_count
  fingerprint_count

visual_candidates
  side
  page_no
  bbox
  label
  confidence
  reasons
  used_for_promotion

low_confidence_candidates
  rule_score
  visual_score
  fusion_score
  rejection_reason
```

## 测试策略

测试分三层：

1. 规则单元测试  
   覆盖现有真实失败形态：短 bbox、业务字段页、供需方签署表、终止条款后签署区、跨页下半部分。

2. OpenCV 单元测试  
   使用合成图片或渲染页局部，验证红章、手写痕迹、表格线、空白页和噪声过滤。

3. 融合管线测试  
   验证规则候选和 OpenCV 视觉候选如何提升、保留或拒绝，确保视觉单独命中不会误生成签署区。

关键断言：

- 漏检样本的签署块被识别。
- 普通正文不被签署 bbox padding 剥离。
- 封面签约信息表不被误识别为签署区。
- OpenCV 不可用时任务仍完成。
- VLM 不在默认测试链路中被调用。

## 分阶段实施

### 阶段 1：规则主链路补强

先实现当前计划中的签署区延续和业务字段识别，解决已知真实任务。

输出：

- 更完整的 `SigningBlockDetector`。
- 更严格的正文剥离 bbox fallback。
- 覆盖真实样本的回归测试。

### 阶段 2：本地 OpenCV detector

新增 `OpenCvVisualSignatureDetector`，先支持候选页扫描和已识别 region 的视觉增强。

输出：

- 红章/签字/表格线/视觉密度候选。
- `visual_candidates` 调试产物。
- OpenCV 不可用降级测试。

### 阶段 3：融合评分

将规则分和视觉分合并，明确提升、保留、拒绝策略。

输出：

- `rule_score`、`visual_score`、`fusion_score`。
- 视觉辅助召回低置信签署页。
- 误检保护测试。

### 阶段 4：复核与数据闭环


输出：


## 风险控制

- 不默认整页高亮，除非页面被高置信判定为完整签署页。
- OpenCV 单独命中不直接剥离正文。
- 业务字段必须结合签署上下文、近末页位置、双栏结构或跨页上下文。
- 所有 bbox 扩展都必须排除页眉、页脚、页码和正文长句。
- 所有不可用视觉能力都只降级，不阻断任务。
- VLM 只作为显式开启的辅助能力，不进入默认生产链路。

## 成功标准

- 已报告的签署页漏检任务在重跑后能识别对应签署块。
- `14.2` 等正文条款不会因签署 bbox padding 被误剥离。
- 视觉能力关闭或不可用时，规则链路仍可完成任务。
- OpenCV 启用后能增加红章/签字/表格视觉信号，但不会单独造成误检。
- 调试产物能解释每个签署区为何通过、为何被提升、为何被拒绝。
