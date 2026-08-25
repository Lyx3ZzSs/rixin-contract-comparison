# PP-OCRv6 + PP-StructureV3 服务 - API 使用说明

> 服务由 `paddlex --serve` 提供（FastAPI/Uvicorn）。两个服务同卡共存于一张 RTX 4090：
> - **OCR 服务**（PP-OCRv6，纯文字识别）端口 8088，端点 `POST /ocr`
> - **结构服务**（PP-StructureV3，版面/表格/公式分析）端口 8089，端点 `POST /layout-parsing`
>
> 配套文档：[部署说明书.md](部署说明书.md)

---

## 1. 服务地址

| 服务 | Base URL | 端点 | 用途 |
|---|---|---|---|
| OCR (PP-OCRv6) | `http://10.10.12.64:8088` | `POST /ocr` | 纯文字识别（快、轻） |
| 结构 (PP-StructureV3) | `http://10.10.12.64:8089` | `POST /layout-parsing` | 版面/表格/公式/文字综合分析 |

两个服务都有 `GET /health`、`GET /docs`、`GET /openapi.json`。

> 端口由启动脚本参数决定：OCR `bash start.sh <tag> <gpu> <port>`，结构 `bash start_structure.sh <tag> <gpu> <port> <mem_fraction>`。

---

## 2. 端点列表

两个服务各自独立，端点路径相同（都含 `/health`、`/docs`），只是推理端点不同：

| 服务端口 | 推理端点 | 方法 | 用途 |
|---|---|---|---|
| 8088 (OCR) | `/ocr` | POST | 纯文字识别 |
| 8089 (结构) | `/layout-parsing` | POST | 版面/表格/公式/文字综合分析 |
| 两者 | `/health` | GET | 健康检查，200 即存活 |
| 两者 | `/docs` | GET | FastAPI 交互式 API 文档 |
| 两者 | `/openapi.json` | GET | OpenAPI schema（含全部字段定义） |

> 下文 §3–§8 以 OCR 的 `/ocr` 为例；`/layout-parsing` 的调用方式完全相同（JSON + base64 + `fileType`），见 §9。

---

## 3. 请求格式

`POST /ocr` 接收 **`application/json`** 请求体（**不是** multipart/form-data）。

### 3.1 请求体参数

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file` | string | ✅ | — | 图片/PDF 的 **纯 base64 字符串（无 `data:` 前缀）**，或一个 http(s) URL |
| `fileType` | int | ❌* | — | `1` = 图片，`0` = PDF。**强烈建议显式传**；不传时仅当 `file` 是 URL 才能自动推断 |
| `useDocOrientationClassify` | bool | ❌ | false | 文档方向分类（0/90/180/270°） |
| `useDocUnwarping` | bool | ❌ | false | 文档去畸变（透视校正） |
| `useTextlineOrientation` | bool | ❌ | true | 文本行方向识别（竖排/倒置） |
| `textDetLimitSideLen` | int | ❌ | 64 | 检测前图像长边限制像素 |
| `textDetLimitType` | string | ❌ | "min" | 边长限制类型：`min` 或 `max` |
| `textDetThresh` | float | ❌ | 0.3 | 检测像素置信度阈值 |
| `textDetBoxThresh` | float | ❌ | 0.6 | 文本框平均置信度阈值 |
| `textDetUnclipRatio` | float | ❌ | 1.5 | 文本框外扩比例 |
| `textRecScoreThresh` | float | ❌ | 0.0 | 识别结果过滤阈值（低于此分数的文本行被丢弃） |
| `returnWordBox` | bool | ❌ | false | 是否返回单字级别框 |
| `visualize` | bool | ❌ | false | 是否返回可视化结果图（`ocrImage`） |
| `logId` | string | ❌ | 自动生成 | 请求追踪 ID，原样回显 |

> `fileType` 取值与直觉相反：**`1`=图片，`0`=PDF**。传反会报 `Invalid input file`。

### 3.2 `file` 字段三种写法

```bash
# ① 纯 base64（最常用，本地文件）
B64=$(base64 -w0 ./test.png)        # -w0 关闭换行，必须是单行连续串

# ② http(s) URL（服务需能访问该地址）
"https://example.com/test.png"

# ③ ❌ 错误写法：不要加 data: 前缀
"data:image/png;base64,xxxxx"        # 会被当 base64 解码失败 -> Invalid input file
```

---

## 4. 响应格式

### 4.1 顶层结构

```json
{
  "logId": "10d39d90-4241-441d-a188-3826cfcbb128",
  "result": {
    "ocrResults": [ /* 见 4.2，每页一个元素 */ ],
    "dataInfo": { "width": 900, "height": 480, "type": "image" }   // PDF 时为 PDFInfo
  },
  "errorCode": 0,
  "errorMsg": "Success"
}
```

| 字段 | 说明 |
|---|---|
| `logId` | 请求追踪 ID |
| `errorCode` | `0` 成功；非 0 见第 6 节 |
| `errorMsg` | `"Success"` 或错误描述 |
| `result.dataInfo` | 输入图元信息（宽高/类型） |
| `result.ocrResults` | 数组，**每页一个元素**（图片 1 个，PDF N 个） |

### 4.2 `ocrResults[i]` 结构

```json
{
  "prunedResult": { /* 见 4.3，核心识别结果 */ },
  "ocrImage":   "<base64 可视化图，visualize=true 时才有>",
  "inputImage": "<base64 原始输入图>"
}
```

### 4.3 `prunedResult` 核心字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `rec_texts` | string[] | **识别出的文本行**（按阅读顺序） |
| `rec_scores` | float[] | 每行识别置信度（0–1），与 `rec_texts` 一一对应 |
| `dt_polys` | int[][][] | 检测文本框，4 个角点 `[[x,y],[x,y],[x,y],[x,y]]` |
| `rec_polys` | int[][][] | 识别文本框，同上格式 |
| `rec_boxes` | int[][] | 轴对齐外接框 `[x1,y1,x2,y2]` |
| `textline_orientation_angles` | int[] | 每行方向角（0=正常） |
| `model_settings` | object | 实际生效配置，如 `{"use_doc_preprocessor":false,"use_textline_orientation":true}` |
| `text_type` | string | 文本类型，`"general"` |
| `text_rec_score_thresh` | float | 识别过滤阈值 |
| `return_word_box` | bool | 是否含单字框 |
| `text_det_params` | object | 检测参数 |

---

## 5. 调用示例

### 5.1 curl - 单张图片

```bash
B64=$(base64 -w0 ./test.png)
curl -s -X POST http://10.10.12.64:8088/ocr \
  -H "Content-Type: application/json" \
  -d "{\"file\":\"$B64\",\"fileType\":1}" | python3 -m json.tool
```

### 5.2 curl - PDF

```bash
B64=$(base64 -w0 ./test.pdf)
curl -s -X POST http://10.10.12.64:8088/ocr \
  -H "Content-Type: application/json" \
  -d "{\"file\":\"$B64\",\"fileType\":0}" | python3 -m json.tool
```

### 5.3 curl - URL 直接推理（无需 base64）

```bash
curl -s -X POST http://10.10.12.64:8088/ocr \
  -H "Content-Type: application/json" \
  -d '{"file":"https://paddleocr.bj.bcebos.com/PP-OCRv6/ch/demo.jpg","fileType":1}'
```

### 5.4 curl - 只提取文本

```bash
B64=$(base64 -w0 ./test.png)
curl -s -X POST http://10.10.12.64:8088/ocr \
  -H "Content-Type: application/json" \
  -d "{\"file\":\"$B64\",\"fileType\":1}" \
| python3 -c "
import sys, json
d = json.load(sys.stdin)
if d.get('errorCode') != 0:
    print('错误:', d.get('errorMsg')); sys.exit(1)
for i, t in enumerate(d['result']['ocrResults'][0]['prunedResult']['rec_texts'], 1):
    print(f'{i}. {t}')
"
```

### 5.5 Python

```python
import base64, json, requests

with open("test.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

resp = requests.post(
    "http://10.10.12.64:8088/ocr",
    json={"file": b64, "fileType": 1},   # 图片=1, PDF=0
    timeout=120,
)
data = resp.json()
assert data["errorCode"] == 0, data["errorMsg"]

page = data["result"]["ocrResults"][0]
pr = page["prunedResult"]
for text, score, box in zip(pr["rec_texts"], pr["rec_scores"], pr["rec_boxes"]):
    print(f"[{score:.3f}] {box}  {text}")
```

### 5.6 完整响应示例（真实输出）

请求：一张含 5 行中英文的 900×480 PNG。响应（已裁剪 base64 图字段）：

```json
{
  "logId": "10d39d90-4241-441d-a188-3826cfcbb128",
  "result": {
    "ocrResults": [
      {
        "prunedResult": {
          "model_settings": {"use_doc_preprocessor": false, "use_textline_orientation": true},
          "dt_polys": [[[38,31],[510,31],[510,78],[38,78]], "...(共5个框)"],
          "text_det_params": {"..."},
          "text_type": "general",
          "textline_orientation_angles": [0, 0, 0, 0, 0],
          "text_rec_score_thresh": 0.0,
          "return_word_box": false,
          "rec_texts": [
            "PP-OCRv6 文字识别测试",
            "Hello, PP-OCRv6! 2026-07-21",
            "光学字符识别 OCR 引擎",
            "The quick brown fox 1234567890",
            "单卡 RTX 4090 GPU 推理服务"
          ],
          "rec_scores": [0.9966, 0.9868, 0.9402, 0.9999, 0.9932],
          "rec_polys": [[[38,31],[510,31],[510,78],[38,78]], "..."],
          "rec_boxes": [[38,31,510,78], "..."]
        },
        "ocrImage": "<base64...>",
        "inputImage": "<base64...>"
      }
    ],
    "dataInfo": {"width": 900, "height": 480, "type": "image"}
  },
  "errorCode": 0,
  "errorMsg": "Success"
}
```

---

## 6. 错误码

服务统一用 `errorCode` + `errorMsg` 字段，HTTP 状态码同步反映。

| HTTP | errorCode | errorMsg | 原因 / 处理 |
|---|---|---|---|
| 200 | 0 | Success | 成功 |
| 422 | 422 | `File type cannot be determined` | 缺 `fileType`，且 `file` 非 URL。补 `fileType`（图 1 / PDF 0） |
| 422 | 422 | `Invalid input file` | `file` 不是合法 base64 / URL，或 `fileType` 与实际内容不符（图片传了 0、PDF 传了 1） |
| 422 | 422 | `Input should be a valid dictionary...` | 请求体不是 JSON（误用了 `curl -F` multipart）。改用 `-H "Content-Type: application/json" -d '{...}'` |
| 422 | 422 | `Unsupported file type` | URL 推断不出类型，显式传 `fileType` |
| 422 | 422 | `Input image or document page exceeds pixel limit` | 图/PDF 页过大，调小 `textDetLimitSideLen` 或裁剪输入 |
| 413 | — | Request Entity Too Large | 请求体过大（base64 比原图大 ~33%），压缩或降分辨率后再传 |

---

## 7. 常见问题

**Q：为什么 `curl -F "file=@x.png"` 报 422？**
A：本版接口只收 JSON。`-F` 是 multipart，服务端拒绝。改用 base64 + JSON（见 5.1）。

**Q：base64 要不要加 `data:image/png;base64,` 前缀？**
A：**不要**。`file` 必须是纯 base64。加前缀会被当 base64 解码失败。

**Q：`fileType` 到底 0 还是 1？**
A：`1`=图片，`0`=PDF。和直觉相反，记牢即可。

**Q：URL 推理失败？**
A：服务容器需能访问该 URL（出网）。内网 URL 要确保容器网络可达；跨主机访问用宿主机 IP（如 `10.10.12.64`）而非 loopback。

**Q：如何拿可视化结果图？**
A：请求加 `"visualize": true`，响应 `ocrResults[i].ocrImage` 即带框 base64 图。

**Q：PDF 多页怎么处理？**
A：`result.ocrResults` 是数组，每页一个元素，各自有独立的 `prunedResult`。

**Q：识别置信度低/漏字怎么办？**
A：调 `textRecScoreThresh`（默认 0，已不过滤）；调 `textDetThresh`/`textDetBoxThresh` 降低检测门槛；调 `textDetUnclipRatio` 改变框大小；图片清晰度本身最关键。

**Q：并发性能？**
A：单实例单 GPU 串行推理，warm ~0.16s/张。高并发需多副本 + 前置 nginx 负载均衡（参考 H100 多卡版方案）。

---

## 8. 快速自测

```bash
# 1. 服务存活
curl -fsS http://10.10.12.64:8088/health && echo OK

# 2. 生成一张测试图（需 PIL + 中文字体）
python3 - <<'PY'
from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB",(900,200),"white"); d = ImageDraw.Draw(img)
f = ImageFont.truetype("/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",40)
d.text((30,30),"PP-OCRv6 测试 12345",fill="black",font=f)
img.save("/tmp/t.png")
PY

# 3. 推理
B64=$(base64 -w0 /tmp/t.png)
curl -s -X POST http://10.10.12.64:8088/ocr \
  -H "Content-Type: application/json" \
  -d "{\"file\":\"$B64\",\"fileType\":1}" \
| python3 -c "import sys,json;d=json.load(sys.stdin);print(d['result']['ocrResults'][0]['prunedResult']['rec_texts'])"
```

---

## 9. PP-StructureV3 结构分析接口 `POST /layout-parsing`

> 地址 `http://10.10.12.64:8089/layout-parsing`。对文档做**版面分析 + 表格识别 + 公式识别 + 文字识别**，
> 输出结构化 block 列表、表格 HTML、公式 LaTeX、整页 markdown。比 `/ocr` 重（约 12 个模型，单张 11s 量级）。

### 9.1 请求

与 `/ocr` 完全相同的 JSON + base64 格式（`file` 纯 base64 或 URL，`fileType: 1=图片 / 0=PDF`），额外支持结构参数（均可选，不传用默认）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `useDocOrientationClassify` | bool | false | 文档方向分类 |
| `useDocUnwarping` | bool | false | 文档去畸变 |
| `useTableRecognition` | bool | true | 表格识别 |
| `useFormulaRecognition` | bool | true | 公式识别 |
| `useSealRecognition` | bool | false | 印章识别 |
| `useChartRecognition` | bool | false | 图表识别 |
| `useRegionDetection` | bool | true | 区域检测 |
| `layoutThreshold` | float | - | 版面检测阈值 |
| `layoutNms` | bool | - | 版面 NMS |
| `textDetThresh` / `textDetBoxThresh` / `textDetUnclipRatio` | - | - | 文字检测参数（同 OCR） |
| `textRecScoreThresh` | float | - | 文字识别过滤阈值 |

### 9.2 响应结构

```jsonc
{
  "logId": "...",
  "errorCode": 0,
  "errorMsg": "Success",
  "result": {
    "layoutParsingResults": [          // 每页一个元素
      {
        "prunedResult": {
          "page_count": 1, "width": 1000, "height": 720,
          "model_settings": { "use_doc_preprocessor": false, "use_textline_orientation": true, ... },
          "parsing_res_list": [        // ★ 版面 block 列表
            { "block_label": "paragraph_title", "block_content": "标题文本",
              "block_bbox": [x1,y1,x2,y2], "block_id": 0, "block_order": 1 },
            { "block_label": "text", "block_content": "正文..." , ... },
            { "block_label": "table",  "block_content": "<html>...表格HTML...</html>", ... },
            ...
          ],
          "layout_det_res": { "dt_polys": [...], ... },     // 版面检测框
          "overall_ocr_res": { ... },                       // 整页 OCR 结果
          "table_res_list": [                               // ★ 表格识别
            { "cell_box_list": [...], "pred_html": "<table>...</table>", "table_ocr_pred": {...} }
          ],
          "formula_res_list": [                             // ★ 公式识别
            { "rec_formula": "a^{\\wedge}2+b^{\\wedge}2=c^{\\wedge}2", "dt_polys": [...], "formula_region_id": 1 }
          ],
          "doc_preprocessor_res": { ... }
        },
        "markdown": { "text": "...", "isStart": true, "isEnd": true, "images": {...} },  // 整页 markdown
        "outputImages": { ... },     // 可视化图 (base64)
        "inputImage": "<base64>"     // 原始输入图
      }
    ],
    "dataInfo": { "width": 1000, "height": 720, "type": "image" }
  }
}
```

**关键字段**：

| 字段 | 含义 |
|---|---|
| `result.layoutParsingResults[i].prunedResult.parsing_res_list` | 版面 block 列表，每个含 `block_label`（paragraph_title/text/table/formula/figure...）、`block_content`、`block_bbox` |
| `...table_res_list[i].pred_html` | 表格 HTML，可直接渲染 |
| `...formula_res_list[i].rec_formula` | 公式 LaTeX |
| `...overall_ocr_res` | 整页文字 OCR 结果 |
| `...markdown.text` | 整页 markdown 文本 |

### 9.3 调用示例

```bash
# curl - 图片
B64=$(base64 -w0 ./doc.png)
curl -s -X POST http://10.10.12.64:8089/layout-parsing \
  -H "Content-Type: application/json" \
  -d "{\"file\":\"$B64\",\"fileType\":1}" | python3 -m json.tool

# 只看版面 block 的标签和内容
B64=$(base64 -w0 ./doc.png)
curl -s -X POST http://10.10.12.64:8089/layout-parsing \
  -H "Content-Type: application/json" \
  -d "{\"file\":\"$B64\",\"fileType\":1}" \
| python3 -c "
import sys,json
d=json.load(sys.stdin)
assert d['errorCode']==0, d['errorMsg']
for b in d['result']['layoutParsingResults'][0]['prunedResult']['parsing_res_list']:
    print(f\"[{b['block_label']}] {str(b['block_content'])[:80]}\")
"
```

```python
# Python
import base64, requests, json
b64 = base64.b64encode(open("doc.png","rb").read()).decode()
r = requests.post("http://10.10.12.64:8089/layout-parsing",
                  json={"file": b64, "fileType": 1}, timeout=180)
data = r.json()
assert data["errorCode"] == 0, data["errorMsg"]
pr = data["result"]["layoutParsingResults"][0]["prunedResult"]
print("blocks:", [(b["block_label"], b["block_content"][:30]) for b in pr["parsing_res_list"]])
print("tables:", [t["pred_html"] for t in pr["table_res_list"]])
print("formulas:", [f["rec_formula"] for f in pr["formula_res_list"]])
```

### 9.4 实测示例（含标题+文字+3×3表格+公式的图）

```
blocks:
  [paragraph_title] PP-StructureV3文档结构测试
  [paragraph_title] Document Structure Recognition Demo
  [text]            这是一段普通文字，用于测试版面分析。
  [figure_title]    PP-StructureV3 can detect layout, tables and formulas.
  [table]           <html><body><table>...</table></body></html>
  [text]            Formula:$a^{\wedge}2+b^{\wedge}2=c^{\wedge}2$
tables:
  <table><tr><td>姓名</td><td>年龄</td><td>城市</td></tr>
         <tr><td>Alice</td><td>30</td><td>北京</td></tr>
         <tr><td>Bob</td><td>25</td><td>上海</td></tr></table>
formulas:
  a^{\wedge}2+b^{\wedge}2=c^{\wedge}2
```

### 9.5 OCR 与结构服务怎么选

| 场景 | 用哪个 |
|---|---|
| 只提取图片里的文字（最快） | `POST /ocr`（8088） |
| 文档版面分析、要表格 HTML / 公式 LaTeX / 整页 markdown | `POST /layout-parsing`（8089） |
| PDF 按页结构化 | `POST /layout-parsing`，`fileType: 0`，`layoutParsingResults` 每页一个 |
