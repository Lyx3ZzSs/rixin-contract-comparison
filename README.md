# 合同差异审查系统

这是一个前后端分离的合同差异审查 MVP。后端 FastAPI 负责上传两份 PDF 合同、通过可插拔文档识别器提取结构化文本和坐标、按条款识别差异、生成高亮 PDF、差异截图和 PDF 报告；前端 React 工作台负责上传、任务结果预览和产物下载。它面向合同审查流程，不是普通逐字 Diff 工具。

## 功能

- 文档识别默认走 PyMuPDF，扫描件降级 PaddleOCR。
- 文本型 PDF、扫描件 PDF 的结构化文本提取。
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

默认使用自动模式：可复制文本 PDF 优先使用 PyMuPDF 真实字符坐标，无法抽取文本时再降级到 PaddleOCR。

```bash
DOCUMENT_EXTRACTOR=auto
PYMUPDF_MIN_TEXT_CHARS=1
```

也可以显式指定 `pymupdf` 或 `paddleocr`。

接入 PaddleOCR 时配置：

```bash
PADDLEOCR_ACCESS_TOKEN=your_token
PADDLEOCR_MODEL=PP-OCRv5
PADDLEOCR_JOB_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_TIMEOUT_SECONDS=120
PADDLEOCR_POLL_INTERVAL_SECONDS=2
PADDLEOCR_MAX_WAIT_SECONDS=300
SAVE_OCR_RAW_RESULT=true
```

OCR 原始结果会保存到 `storage/ocr/{task_id}`，用于排查识别质量。差异结果来自程序化条款匹配和结构化 diff，不依赖 AI 改写底层差异识别结果。

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
  -F "compare_file=@compare.pdf" \
  -F "enable_ai_analysis=false"
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

- PaddleOCR 需要可用 Access Token，扫描件和图片型 PDF 的识别质量取决于 OCR 返回结果。
- 暂不支持 Word、Excel 和复杂表格深度 diff。
- MVP 使用同步任务、本地 JSON 和本地文件存储。
- 风险统计首期不接入大模型，默认按低风险兼容展示。

## 后续扩展

- 增强表格、金额、日期、主体信息等合同要素结构化抽取。
- 引入异步任务队列和任务进度。
- 用数据库替代本地 JSON。
- 增加规则风险引擎或按需接入大模型审查总结。
