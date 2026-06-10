# 国能日新 · 合同智能审查平台

这是一个前后端分离的国能日新 · 合同智能审查平台 MVP。后端 FastAPI 负责上传两份 PDF 合同、通过可插拔文档识别器提取结构化文本和坐标、按条款识别差异、生成 PDF 报告；前端 React 工作台负责上传、原始 PDF 在线预览、差异高亮渲染和产物下载。它面向合同审查流程，不是普通逐字 Diff 工具。

## 功能

- 文档识别默认走 PyMuPDF，扫描件切换到远端 PP-OCRv5。
- 文本型 PDF、扫描件 PDF 的结构化文本提取。
- 合同字段提取统一走远端 PP-OCRv5 + OpenAI-compatible LLM，支持 PDF、Word 和常见图片输入。
- 条款切分和坐标证据绑定。
- 条款编号、标题、正文相似度匹配。
- 新增、删除、修改差异识别。
- React 预览页：左右原始 PDF 预览、前端差异高亮、同步滚动、差异点定位和页码标识。
- `合同差异分析报告` PDF。

## 安装与启动

准备后端环境变量：

```bash
cp .env.example .env
```

`.env.example` 使用安全占位值；接入扫描件 OCR、结构化版面识别或合同字段提取时，再按实际环境填写 `PPOCRV5_URL`、`PPSTRUCTURE_URL` 和 `AI_LLM_*`。

安装后端依赖：

```bash
cd backend
python -m pip install -r requirements.txt
```

项目同时提供 `backend/pyproject.toml`，新环境也可以使用可编辑安装：

```bash
cd backend
python -m pip install -e ".[dev]"
```

启动后端 API：

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

准备并启动前端：

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

访问地址：

- 前端工作台：`http://127.0.0.1:5173/`
- 后端健康检查：`http://127.0.0.1:8000/health`
- 后端 API 文档：`http://127.0.0.1:8000/docs`

如果 `8000` 端口已被占用，可换一个端口：

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8001
```

也可以直接运行入口文件，适合 PyCharm Run 配置：

```bash
cd backend
python app/main.py
```

如果后端不是运行在 `http://127.0.0.1:8000`，在前端 `.env` 中配置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8001
```

## 文档识别配置

后端运行配置集中在仓库根目录 `.env`，模板见 `.env.example`；也可在 `backend/.env` 放后端本地覆盖。代码读取和校验入口在 `backend/app/config.py`，默认提示词在 `backend/app/config_defaults.py`。

## 架构边界

后端代码统一在 `backend/` 下，并按 HTTP 适配、应用编排、基础设施适配和文档处理服务分层。`backend/app/api*.py` 只负责请求/响应和 HTTP 错误映射；`backend/app/application/` 负责任务创建与后台提交；`backend/app/infrastructure/` 封装可替换任务仓储、产物路径防护和后台执行器；`backend/app/services/` 保留合同解析、对比、证据定位、风险分析和报告生成能力。更多说明见 `docs/architecture.md`。

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

字段抽取会复用 `EXTRACTION_TASK_DESCRIPTION`、`EXTRACTION_OUTPUT_FORMAT`、`EXTRACTION_RULES_STR` 和 `EXTRACTION_FEW_SHOT_DEMO` 作为 LLM 提示词约束。OCR 和 LLM 原始结果会保存到 `storage/tasks/{task_id}/ocr`，用于排查识别质量和字段抽取质量。

通用文档提取默认使用自动模式：可复制文本 PDF 优先使用 PyMuPDF 真实字符坐标，无法抽取文本或文本量不足时切换到结构化 OCR。该通用链路在 PP-Structure 不可用时可回退到仅 PP-OCRv5 文本抽取。

```bash
DOCUMENT_EXTRACTOR=auto
COMPARE_DOCUMENT_EXTRACTOR=ppstructure_ocr_hybrid
COMPARE_REQUIRE_STRUCTURED_OCR=true
PYMUPDF_MIN_TEXT_CHARS=1
ALIGN_STRUCTURED_EXTRACTION=true
```

合同比对接口默认强制使用 `ppstructure_ocr_hybrid`。每份文件都会同时调用 `{PPSTRUCTURE_URL}/layout-parsing` 和 `{PPOCRV5_URL}/ocr`，最终对比文本以 PP-OCRv5 为准，版面和表格区域以 PP-Structure 为准。任一服务不可用或 PP-Structure 解析失败时，比对任务会标记为失败，不会降级到 PyMuPDF 或仅 PP-OCRv5。

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

OCR 原始结果会保存到 `storage/tasks/{task_id}/ocr`，用于排查识别质量。扫描件默认链路使用 PP-Structure 的结构区域标记 PP-OCRv5 文字行，不再基于 OCR 文本关键词推断表格区域。差异结果来自程序化条款匹配和结构化 diff，不依赖 AI 改写底层差异识别结果。

点击导出报告或请求 `/api/compare/{task_id}/report` 时，系统不会调用大模型，会生成包含基础信息和审计统计改动点表格的差异分析报告。`AI_LLM_*` 配置仅用于合同字段提取。

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
- `original_highlight_pdf_url`：兼容字段，当前固定为空字符串；在线高亮由前端渲染。
- `compare_highlight_pdf_url`：兼容字段，当前固定为空字符串；在线高亮由前端渲染。

查询任务：

```bash
curl "http://127.0.0.1:8000/api/compare/{task_id}"
curl "http://127.0.0.1:8000/api/compare/{task_id}/diffs"
```

下载产物：

```bash
curl -O -J "http://127.0.0.1:8000/api/compare/{task_id}/report"
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
cd backend
python -m compileall app tests
python -m pytest
cd ..
cd frontend && npm test && npm run build
```

## 当前限制

- 合同比对必须同时配置可用的 `PPOCRV5_URL` 和 `PPSTRUCTURE_URL`；任一服务不可用都会使比对任务失败。
- 合同字段提取的 Word 支持依赖 LibreOffice；合同对比仍暂不支持 Word、Excel 和复杂表格深度 diff。
- MVP 使用同步任务；任务元数据支持本地 JSON 或 PostgreSQL，文件产物仍使用本地文件存储。
- MVP 使用同步任务，任务元数据和文件产物均使用本地文件存储。
- 报告导出不会在导出时重新调用大模型；风险统计基于任务已有的差异分析结果。

## 后续扩展

- 增强表格、金额、日期、主体信息等合同要素结构化抽取。
- 引入异步任务队列和任务进度。
- 增加规则风险引擎或按需接入大模型审查总结。
