# 文档解析服务 API 使用说明书

> 基于 PaddleOCR-VL 的文档解析微服务，支持 PDF / Word / 图片输入，输出 JSON 和 Markdown 格式。
>
> **服务地址**: `http://10.8.0.248:8000`
> **API 基础路径**: `/api/v1`

---

## 目录

- [1. 认证方式](#1-认证方式)
- [2. 接口概览](#2-接口概览)
- [3. 接口详情](#3-接口详情)
  - [3.1 文档解析（同步）](#31-文档解析同步)
  - [3.2 文档解析（流式）](#32-文档解析流式)
  - [3.3 健康检查](#33-健康检查)
- [4. 错误码](#4-错误码)
- [5. 并发与限流说明](#5-并发与限流说明)
- [6. 支持的文件类型](#6-支持的文件类型)
- [7. curl 调试命令速查](#7-curl-调试命令速查)

---

## 1. 认证方式

服务支持 **API Key** 认证，通过 HTTP Header 传递：

```
X-API-Key: your-api-key-here
```

- 若服务未配置 API Key（`DOC_PARSER_API_KEYS` 为空），则所有请求均放行，无需传此 Header。
- 若已配置，缺少或错误的 Key 将返回 `401 Unauthorized`。

---

## 2. 接口概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/parse` | 同步解析文档，等待全部页面完成后返回 |
| POST | `/api/v1/parse/stream` | 流式解析文档，逐页通过 SSE 返回结果 |
| GET  | `/api/v1/health` | 查看服务健康状态和队列信息 |

---

## 3. 接口详情

### 3.1 文档解析（同步）

**POST** `/api/v1/parse`

上传文档文件，等待所有页面 OCR 完成后统一返回结果。适合页数较少（1-10 页）的文档。

#### 请求

- **Content-Type**: `multipart/form-data`

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `file` | file | 是 | — | 待解析的文档文件 |
| `output_format` | string | 否 | `"json"` | 输出格式：`"json"` 或 `"markdown"` |
| `dpi` | int | 否 | `200` | PDF/Word 转图片的分辨率，范围 72-600 |
| `max_pages` | int | 否 | `50` | 最大处理页数，范围 1-200 |
| `page_range` | string | 否 | 全部页面 | 指定页范围，如 `"1-5,8,10-15"` |

#### 响应（output_format=json）

```json
{
  "success": true,
  "request_id": "a1b2c3d4-...",
  "file_name": "contract.pdf",
  "total_pages": 5,
  "pages_processed": 5,
  "output_format": "json",
  "processing_time_ms": 15230,
  "result": [
    {
      "page_number": 1,
      "content": "# 合同标题\n\n甲方：...",
      "error": null
    },
    {
      "page_number": 2,
      "content": "## 第二条 服务范围\n\n...",
      "error": null
    }
  ]
}
```

- `result` 为数组，每个元素对应一页的解析结果。
- `content` 为模型输出的 Markdown 格式文本（表格用 HTML，公式用 LaTeX）。
- 若某页 OCR 失败，`error` 字段会包含错误信息，`content` 为空字符串。

#### 响应（output_format=markdown）

```json
{
  "success": true,
  "request_id": "e5f6g7h8-...",
  "file_name": "contract.pdf",
  "total_pages": 5,
  "pages_processed": 5,
  "output_format": "markdown",
  "processing_time_ms": 15230,
  "result": "## Page 1\n\n# 合同标题\n\n甲方：...\n\n---\n\n## Page 2\n\n## 第二条 服务范围\n\n..."
}
```

- `result` 为字符串，所有页面内容以 `---` 分隔，每页以 `## Page N` 开头。

#### curl 示例

```bash
# 基础用法：解析 PDF，JSON 格式
curl -X POST http://localhost:8000/api/v1/parse \
  -F "file=@./contract.pdf"

# 解析 Word 文档，输出 Markdown
curl -X POST http://localhost:8000/api/v1/parse \
  -F "file=@./report.docx" \
  -F "output_format=markdown"

# 解析图片
curl -X POST http://localhost:8000/api/v1/parse \
  -F "file=@./invoice.png"

# 指定页范围（只解析第 1、2、5 页）
curl -X POST http://localhost:8000/api/v1/parse \
  -F "file=@./document.pdf" \
  -F "page_range=1-2,5"

# 自定义 DPI 和最大页数
curl -X POST http://localhost:8000/api/v1/parse \
  -F "file=@./document.pdf" \
  -F "dpi=300" \
  -F "max_pages=10"

# 带 API Key 认证
curl -X POST http://localhost:8000/api/v1/parse \
  -H "X-API-Key: your-api-key-here" \
  -F "file=@./contract.pdf"

# 将结果保存到文件
curl -X POST http://localhost:8000/api/v1/parse \
  -F "file=@./contract.pdf" \
  -F "output_format=markdown" \
  -o result.json

# 用 jq 提取 Markdown 文本内容
curl -s -X POST http://localhost:8000/api/v1/parse \
  -F "file=@./contract.pdf" \
  -F "output_format=markdown" | jq -r '.result'
```

---

### 3.2 文档解析（流式）

**POST** `/api/v1/parse/stream`

上传文档文件，每完成一页即通过 Server-Sent Events (SSE) 推送结果。适合页数较多的大文档，避免 HTTP 长时间等待超时。

#### 请求参数

与 `/parse` 完全一致。

#### 响应格式

- **Content-Type**: `text/event-stream`

每页完成后推送一个事件，格式为：

```
data: {"request_id":"...","page_number":1,"total_pages":5,"content":"...","error":null}

data: {"request_id":"...","page_number":2,"total_pages":5,"content":"...","error":null}

...

data: [DONE]
```

所有页面处理完毕后推送 `data: [DONE]`。

#### 事件字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | string | 请求唯一标识 |
| `page_number` | int | 当前页码 |
| `total_pages` | int | 文档总页数 |
| `content` | string | 该页解析结果（Markdown 格式） |
| `error` | string/null | 错误信息，成功时为 null |

#### curl 示例

```bash
# 流式解析 PDF，逐页输出
curl -N -X POST http://localhost:8000/api/v1/parse/stream \
  -F "file=@./large-document.pdf"

# 流式解析并实时显示每页内容
curl -N -X POST http://localhost:8000/api/v1/parse/stream \
  -F "file=@./report.pdf" \
  -F "page_range=1-3"

# 流式解析 Word 文档
curl -N -X POST http://localhost:8000/api/v1/parse/stream \
  -F "file=@./contract.docx"
```

> **注意**: `-N` 参数禁用 curl 的输出缓冲，确保 SSE 事件实时显示。

#### Python 客户端示例（流式）

```python
import httpx

url = "http://localhost:8000/api/v1/parse/stream"

with open("document.pdf", "rb") as f:
    with httpx.stream("POST", url, files={"file": f}, data={"output_format": "json"}, timeout=300) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    print("\n--- 完成 ---")
                    break
                import json
                event = json.loads(payload)
                if event.get("error"):
                    print(f"第{event['page_number']}页 失败: {event['error']}")
                else:
                    print(f"第{event['page_number']}/{event['total_pages']}页 完成, 内容长度: {len(event['content'])}")
```

---

### 3.3 健康检查

**GET** `/api/v1/health`

无需认证。返回服务状态和并发队列信息。

#### 响应

```json
{
  "status": "healthy",
  "model_endpoint": "http://10.10.12.64:3010/v1/chat/completions",
  "queue_depth": 3,
  "semaphore_available": 1
}
```

| 字段 | 说明 |
|------|------|
| `status` | 服务状态，`"healthy"` 表示正常 |
| `model_endpoint` | 后端模型服务地址 |
| `queue_depth` | 当前排队等待处理的请求数 |
| `semaphore_available` | 当前可用的模型并发槽位数（默认 3） |

#### curl 示例

```bash
# 健康检查
curl http://localhost:8000/api/v1/health

# 格式化输出
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool

# 监控队列状态（每 5 秒查询一次）
watch -n 5 'curl -s http://localhost:8000/api/v1/health | python3 -m json.tool'
```

---

## 4. 错误码

所有错误响应格式：

```json
{
  "success": false,
  "request_id": "uuid",
  "error": {
    "code": "ERROR_CODE",
    "message": "错误描述"
  }
}
```

| HTTP 状态码 | 错误码 | 说明 | 处理建议 |
|-------------|--------|------|----------|
| 400 | `UNSUPPORTED_FILE_TYPE` | 不支持的文件格式 | 检查文件扩展名，参见 [支持的文件类型](#6-支持的文件类型) |
| 401 | `UNAUTHORIZED` | API Key 无效或缺失 | 检查 `X-API-Key` Header |
| 413 | `FILE_TOO_LARGE` | 文件超过大小限制（默认 50MB） | 压缩文件或联系管理员调整 `DOC_PARSER_MAX_FILE_SIZE_MB` |
| 422 | `CONVERSION_FAILED` | 文档转换失败（如 LibreOffice 转换 Word 失败） | 检查文件是否损坏，尝试另存为新文件 |
| 502 | `OCR_ERROR` | OCR 模型返回错误 | 稍后重试，若持续出现联系管理员检查模型服务 |
| 503 | `SERVICE_OVERLOADED` | 请求队列已满（默认 50） | 稍后重试，或通过 health 接口观察队列深度 |
| 504 | `OCR_TIMEOUT` | OCR 请求超时（重试 3 次后仍失败） | 页面可能过于复杂，尝试降低 DPI 或拆分文档 |

---

## 5. 并发与限流说明

| 参数 | 默认值 | 环境变量 | 说明 |
|------|--------|----------|------|
| 模型最大并发 | 3 | `DOC_PARSER_MODEL_MAX_CONCURRENT` | 同时发送给模型的请求数，需匹配模型服务承载能力 |
| 队列最大深度 | 50 | `DOC_PARSER_MAX_QUEUE_DEPTH` | 排队等待的请求数上限，超出返回 503 |
| 单次最大页数 | 50 | `DOC_PARSER_MAX_PAGES` | 单次请求最多处理的页数 |
| 文件大小限制 | 50MB | `DOC_PARSER_MAX_FILE_SIZE_MB` | 上传文件大小上限 |

**并发行为说明**：

- 信号量控制同时只有 `model_max_concurrent`（默认 3）个请求在调用模型。
- 每个请求内部逐页串行处理，不会占用多个信号量槽。
- 超出并发能力的请求会排队等待，先到先得。
- 排队超过 `max_queue_depth` 时新请求直接返回 503，避免级联超时。

**性能参考**（实测数据）：

| 场景 | 单请求延迟 | 20 并发吞吐 |
|------|-----------|-------------|
| PDF（单页） | ~5-8s | ~0.9 req/s |
| 图片（643KB PNG） | ~25-30s | ~0.11 req/s |
| Word（单页） | ~8-12s | ~0.5 req/s |

> 延迟主要取决于模型推理时间和图片大小。PDF 页面渲染后图片较小，所以比直接上传大图更快。

---

## 6. 支持的文件类型

| 类型 | 扩展名 | 说明 |
|------|--------|------|
| PDF | `.pdf` | 逐页渲染为图片后 OCR |
| Word | `.docx`, `.doc` | 通过 LibreOffice 转 PDF 后渲染 OCR |
| 图片 | `.png`, `.jpg`, `.jpeg`, `.bmp`, `.tiff`, `.tif`, `.webp` | 直接 OCR，超过 2048px 自动缩放 |

---

## 7. curl 调试命令速查

```bash
# ===== 健康检查 =====
curl http://localhost:8000/api/v1/health

# ===== 解析 PDF =====
curl -X POST http://localhost:8000/api/v1/parse -F "file=@doc.pdf"

# ===== 解析 Word =====
curl -X POST http://localhost:8000/api/v1/parse -F "file=@doc.docx"

# ===== 解析图片 =====
curl -X POST http://localhost:8000/api/v1/parse -F "file=@img.png"

# ===== 指定输出格式 =====
curl -X POST http://localhost:8000/api/v1/parse -F "file=@doc.pdf" -F "output_format=markdown"

# ===== 指定页范围 =====
curl -X POST http://localhost:8000/api/v1/parse -F "file=@doc.pdf" -F "page_range=1-5,8"

# ===== 限制最大页数 =====
curl -X POST http://localhost:8000/api/v1/parse -F "file=@doc.pdf" -F "max_pages=10"

# ===== 自定义 DPI =====
curl -X POST http://localhost:8000/api/v1/parse -F "file=@doc.pdf" -F "dpi=300"

# ===== 带 API Key =====
curl -X POST http://localhost:8000/api/v1/parse \
  -H "X-API-Key: your-key" -F "file=@doc.pdf"

# ===== 流式解析（大文档推荐） =====
curl -N -X POST http://localhost:8000/api/v1/parse/stream -F "file=@doc.pdf"

# ===== 提取 Markdown 文本 =====
curl -s -X POST http://localhost:8000/api/v1/parse \
  -F "file=@doc.pdf" -F "output_format=markdown" | jq -r '.result'

# ===== 提取所有页面内容（JSON 格式时） =====
curl -s -X POST http://localhost:8000/api/v1/parse \
  -F "file=@doc.pdf" | jq '.result[].content'

# ===== 查看处理耗时 =====
curl -s -X POST http://localhost:8000/api/v1/parse \
  -F "file=@doc.pdf" | jq '{pages: .pages_processed, time_ms: .processing_time_ms}'

# ===== 监控队列状态 =====
watch -n 5 'curl -s http://localhost:8000/api/v1/health | python3 -m json.tool'
```
