# 合同差异审查系统

这是一个前后端分离的合同差异审查 MVP。后端 FastAPI 负责上传两份 PDF 合同、通过可插拔文档识别器提取结构化文本和坐标、按条款识别差异、生成高亮 PDF、差异截图和 PDF 报告；前端 React 工作台负责上传、任务结果预览和产物下载。它面向合同审查流程，不是普通逐字 Diff 工具。

## 功能

- 文档识别默认走 PyMuPDF，扫描件切换到远端 PP-OCRv5。
- 文本型 PDF、扫描件 PDF 的结构化文本提取。
- 合同字段提取统一走远端 PP-OCRv5 + OpenAI-compatible LLM，支持 PDF、Word 和常见图片输入。
- 条款切分和坐标证据绑定。
- 条款编号、标题、正文相似度匹配。
- 新增、删除、修改差异识别。
- 原合同和对比合同高亮 PDF。
- 差异截图和 `合同差异分析报告` PDF。
- React 预览页：左右 PDF 预览、同步滚动、差异点定位和页码标识。

## 安装与启动

安装后端依赖：

```bash
python -m pip install -r requirements.txt
```

启动后端 API：

```bash
python -m uvicorn app.main:app --reload --port 8000
```

启动前端：

```bash
cd frontend
npm install
npm run dev
```

访问地址：

- 前端工作台：`http://127.0.0.1:5173/`
- 后端健康检查：`http://127.0.0.1:8000/health`
- 后端 API 文档：`http://127.0.0.1:8000/docs`

前端当前使用本地写死的测试账号：

- 用户名：`admin`
- 密码：`123456`

如果 `8000` 端口已被占用，可换一个端口：

```bash
python -m uvicorn app.main:app --reload --port 8001
```

也可以直接运行入口文件，适合 PyCharm Run 配置：

```bash
python app/main.py
```

如果后端不是运行在 `http://127.0.0.1:8000`，在前端 `.env` 中配置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8001
```

## 文档识别配置

合同字段提取功能使用远端 PP-OCRv5 先抽取 OCR 文本，再调用 OpenAI-compatible LLM 完成字段抽取。支持 `.pdf`、`.doc`、`.docx`、`.png`、`.jpg`、`.jpeg`、`.bmp`；Word 文件会先通过 LibreOffice 转为 PDF 后提交 PP-OCRv5。

```bash
PPOCRV5_URL=https://your-ppocrv5-host/
PPOCRV5_ACCESS_TOKEN=
PPOCRV5_TIMEOUT_SECONDS=600
PPOCRV5_RETURN_WORD_BOX=true
PPOCRV5_USE_DOC_ORIENTATION_CLASSIFY=false
PPOCRV5_USE_DOC_UNWARPING=false
PPOCRV5_USE_TEXTLINE_ORIENTATION=false
PPOCRV5_TEXT_REC_SCORE_THRESH=0.0
AI_LLM_BASE_URL=https://api.example.com/v1
AI_LLM_API_KEY=your_api_key
AI_LLM_MODEL=your-model
AI_EXTRACTION_TIMEOUT_SECONDS=120
SAVE_EXTRACTION_RAW_RESULT=true
EXTRACTION_MAX_DOCUMENT_SIZE_MB=60
EXTRACTION_MAX_IMAGE_SIZE_MB=5
```

字段抽取会复用 `EXTRACTION_TASK_DESCRIPTION`、`EXTRACTION_OUTPUT_FORMAT`、`EXTRACTION_RULES_STR` 和 `EXTRACTION_FEW_SHOT_DEMO` 作为 LLM 提示词约束。OCR 和 LLM 原始结果会保存到 `storage/ocr/{task_id}`，用于排查识别质量和字段抽取质量。

默认使用自动模式：可复制文本 PDF 优先使用 PyMuPDF 真实字符坐标，无法抽取文本或文本量不足时切换到结构化 OCR。扫描件链路优先使用 PP-Structure 做版面/表格结构识别，并使用 PP-OCRv5 提供真实文本、行坐标和词/字符坐标；如果 PP-Structure 不可用，会回退到仅 PP-OCRv5 文本抽取，并跳过结构化表格比对。

```bash
DOCUMENT_EXTRACTOR=auto
PYMUPDF_MIN_TEXT_CHARS=1
ALIGN_STRUCTURED_EXTRACTION=true
```

也可以显式指定 `pymupdf`、`ppocrv5`、`ppstructure_ocr_hybrid`、`vl_ocr_hybrid` 或 `paddleocr_vl`。`ppstructure_ocr_hybrid` 会同时调用 `{PPSTRUCTURE_URL}/layout-parsing` 和 `{PPOCRV5_URL}/ocr`，最终对比文本以 PP-OCRv5 为准；`vl_ocr_hybrid` 是实验/可选链路，会同时调用 `{PADDLEOCR_VL_URL}/layout-parsing` 和 `{PPOCRV5_URL}/ocr`。

`ALIGN_STRUCTURED_EXTRACTION=true` 时，如果同一对比任务中一侧已经切换到结构化抽取、另一侧仍为 PyMuPDF，系统会将 PyMuPDF 侧重新用结构化抽取器处理，以保持表格边界一致，避免表格文本被当作正文条款参与比对。

接入默认扫描件识别链路时配置：

```bash
PPSTRUCTURE_URL=https://your-ppstructure-host/
PPSTRUCTURE_TIMEOUT_SECONDS=600
PPSTRUCTURE_USE_DOC_ORIENTATION_CLASSIFY=false
PPSTRUCTURE_USE_DOC_UNWARPING=false
PPSTRUCTURE_USE_TEXTLINE_ORIENTATION=false
PPSTRUCTURE_USE_TABLE_RECOGNITION=true
PPSTRUCTURE_USE_SEAL_RECOGNITION=false
PPSTRUCTURE_USE_REGION_DETECTION=true
PPSTRUCTURE_FORMAT_BLOCK_CONTENT=true
PPOCRV5_URL=https://your-ppocrv5-host/
PPOCRV5_TIMEOUT_SECONDS=600
PPOCRV5_RETURN_WORD_BOX=true
PPOCRV5_USE_DOC_ORIENTATION_CLASSIFY=false
PPOCRV5_USE_DOC_UNWARPING=false
PPOCRV5_USE_TEXTLINE_ORIENTATION=false
PPOCRV5_TEXT_REC_SCORE_THRESH=0.0
SAVE_OCR_RAW_RESULT=true
```

如需显式启用实验性的 PaddleOCR-VL-1.5 版面结构链路，再额外配置：

```bash
PADDLEOCR_VL_URL=https://your-paddleocr-vl-host/
PADDLEOCR_VL_TIMEOUT_SECONDS=600
PADDLEOCR_VL_PAGE_MODE=true
PADDLEOCR_VL_RETRY_COUNT=1
PADDLEOCR_VL_USE_DOC_ORIENTATION_CLASSIFY=false
PADDLEOCR_VL_USE_DOC_UNWARPING=false
PADDLEOCR_VL_USE_LAYOUT_DETECTION=true
PADDLEOCR_VL_USE_CHART_RECOGNITION=false
PADDLEOCR_VL_USE_SEAL_RECOGNITION=false
PADDLEOCR_VL_USE_OCR_FOR_IMAGE_BLOCK=true
PADDLEOCR_VL_FORMAT_BLOCK_CONTENT=true
PADDLEOCR_VL_MERGE_LAYOUT_BLOCKS=true
PADDLEOCR_VL_PRETTIFY_MARKDOWN=false
#PADDLEOCR_VL_MAX_PIXELS=0
#PADDLEOCR_VL_MAX_NEW_TOKENS=0
HYBRID_LAYOUT_OVERLAP_THRESHOLD=0.5
HYBRID_LAYOUT_CENTER_FALLBACK=true
HYBRID_SAVE_MERGED_RAW=true
```

OCR 原始结果会保存到 `storage/ocr/{task_id}`，用于排查识别质量。默认扫描件链路不依赖 VL；系统会用 PP-Structure 的结构区域标记 PP-OCRv5 文字行，不再基于 OCR 文本关键词推断表格区域。显式启用 `vl_ocr_hybrid` 时，默认 `PADDLEOCR_VL_PAGE_MODE=true`，VL 会按页提交远端并合并结果，避免整份 PDF 一次请求导致超时；单页超时时错误信息会包含页码。差异结果来自程序化条款匹配和结构化 diff，不依赖 AI 改写底层差异识别结果。

接入 OpenAI-compatible 合同风险分析模型时配置：

```bash
AI_LLM_BASE_URL=https://api.example.com/v1
AI_LLM_API_KEY=your_api_key
AI_LLM_MODEL=your-model
AI_ANALYSIS_TIMEOUT_SECONDS=30
AI_SYSTEM_PROMPT=你是一名资深合同审查专家...
REPORT_MAX_SCREENSHOT_PAGES=10
```

点击导出报告或请求 `/api/compare/{task_id}/report` 时，系统不会调用大模型分析，会生成包含基础信息、审计统计改动点表格和“合同差异”高亮截图的差异分析报告。

## API 示例

```bash
curl -X POST "http://127.0.0.1:8000/api/compare" \
  -F "original_file=@original.pdf" \
  -F "compare_file=@compare.pdf"
```

响应包含：

- `task_id`
- `diff_count`
- `high_risk_count`
- `report_url`
- `report_filename`
- `original_highlight_pdf_url`
- `compare_highlight_pdf_url`

查询任务：

```bash
curl "http://127.0.0.1:8000/api/compare/{task_id}"
curl "http://127.0.0.1:8000/api/compare/{task_id}/diffs"
```

下载产物：

```bash
curl -O -J "http://127.0.0.1:8000/api/compare/{task_id}/report"
curl -O -J "http://127.0.0.1:8000/api/compare/{task_id}/highlight/original"
curl -O -J "http://127.0.0.1:8000/api/compare/{task_id}/highlight/compare"
```

前端结果页：

```text
http://127.0.0.1:5173/tasks/{task_id}
```

## 字体

报告会优先探测系统中文字体。若生成的 PDF 中文显示异常，请在 `.env` 中配置：

```bash
REPORT_FONT_PATH=/path/to/your/chinese-font.ttf
```

## 测试

```bash
python -m pytest
cd frontend && npm test && npm run build
```

## 当前限制

- 扫描件默认抽取需要配置可用的 `PPOCRV5_URL`；实验性的 `vl_ocr_hybrid` 才需要同时配置 `PADDLEOCR_VL_URL`。
- 合同字段提取的 Word 支持依赖 LibreOffice；合同对比仍暂不支持 Word、Excel 和复杂表格深度 diff。
- MVP 使用同步任务、本地 JSON 和本地文件存储。
- 风险统计首期不接入大模型，默认按低风险兼容展示。

## 后续扩展

- 增强表格、金额、日期、主体信息等合同要素结构化抽取。
- 引入异步任务队列和任务进度。
- 用数据库替代本地 JSON。
- 增加规则风险引擎或按需接入大模型审查总结。
