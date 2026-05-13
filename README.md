# AI 合同差异审查系统

这是一个前后端分离的合同差异审查 MVP。后端 FastAPI 负责上传两份文本型 PDF 合同、按条款识别差异、生成 AI 风险审查结果、高亮 PDF、差异截图和 PDF 报告；前端 React 工作台负责上传、任务结果预览和产物下载。它面向合同审查流程，不是普通逐字 Diff 工具。

## 功能

- 文本型 PDF 解析和条款切分。
- 条款编号、标题、正文相似度匹配。
- 新增、删除、修改差异识别。
- Mock LLM 风险审查，支持切换到 OpenAI SDK。
- 原合同和对比合同高亮 PDF。
- 差异截图和 `AI 合同差异分析报告` PDF。
- React 预览页：左右 PDF iframe、差异列表、截图和 AI 分析。

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

## LLM 配置

默认使用 MockLLMClient，适合本地开发和无网络环境：

```bash
USE_MOCK_LLM=true
```

配置真实 OpenAI 调用：

```bash
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4.1-mini
USE_MOCK_LLM=false
```

如果 OpenAI 调用失败，系统会把错误写入单条差异分析，不中断主流程。

## API 示例

```bash
curl -X POST "http://127.0.0.1:8000/api/compare" \
  -F "original_file=@original.pdf" \
  -F "compare_file=@compare.pdf" \
  -F "enable_ai_analysis=true"
```

响应包含：

- `task_id`
- `diff_count`
- `high_risk_count`
- `report_url`
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

- 仅支持可复制文本的 PDF，不支持扫描件、图片型 PDF 或 OCR。
- 暂不支持 Word、Excel、复杂表格结构化对比和图片识别。
- MVP 使用同步任务、本地 JSON 和本地文件存储。
- 预览页使用浏览器原生 PDF 预览，不做 PDF.js 级别页内锚点定位。

## 后续扩展

- 增加 OCR 和版面结构识别。
- 引入异步任务队列和任务进度。
- 用数据库替代本地 JSON。
- 接入 PDF.js，实现差异点击后精确跳页和定位。
- 增强表格、金额、日期、主体信息等合同要素结构化抽取。
