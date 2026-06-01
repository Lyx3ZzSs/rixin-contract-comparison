# MinerU 源码与整体架构分析

本文基于本地源码目录 `/Users/liyuanxin/PyCharmMiscProject/MinerU` 做静态分析，重点解释 MinerU 的模块分层、入口编排、解析后端、数据流、输出体系与扩展点。本文不依赖线上版本信息，结论以当前本地源码为准。

## 1. 总体架构

MinerU 不是一个单纯的本地解析脚本，而是一个围绕统一解析任务模型构建的文档解析平台。它同时提供 CLI、FastAPI、Router、多种解析后端和多种输出产物。

主调用链如下：

```text
CLI / FastAPI / Router
        |
        v
mineru.cli.common: do_parse / aio_do_parse
        |
        +-- Office native parser: DOCX / PPTX / XLSX
        +-- pipeline backend: 小模型流水线
        +-- vlm backend: MinerU VLM 客户端
        +-- hybrid backend: VLM + pipeline/OCR 小模型融合
        |
        v
middle_json
        |
        v
Markdown / content_list / content_list_v2 / images / zip / visualization
```

包入口定义在 `pyproject.toml`，主要命令包括：

- `mineru`：CLI 编排客户端。
- `mineru-api`：FastAPI 服务。
- `mineru-router`：多 worker / 多 upstream 统一入口。
- `mineru-vllm-server`、`mineru-lmdeploy-server`、`mineru-openai-server`：VLM 推理服务相关入口。
- `mineru-models-download`：模型下载工具。
- `mineru-gradio`：Gradio Web UI。

## 2. 模块分层

### `mineru/cli`

`cli` 是编排层，负责参数解析、HTTP 表单协议、任务队列、本地 API 拉起、Router 转发、结果下载和输出重建。关键文件：

- `client.py`：`mineru` CLI。当前 CLI 本质上是 API 编排客户端；未传 `--api-url` 时可拉起本地临时 API 服务。
- `fast_api.py`：`mineru-api` 服务，暴露同步和异步解析接口。
- `router.py`：`mineru-router`，负责健康检查、worker pool、请求转发和任务状态代理。
- `api_request.py`：公共表单协议 `ParseRequestOptions`。
- `common.py`：解析核心分发函数 `do_parse` / `aio_do_parse`。

### `mineru/backend`

`backend` 是解析后端适配层。它把不同模型或文档转换器的结果归一化为 MinerU 的中间结构。

- `backend/pipeline`：传统小模型流水线。
- `backend/vlm`：VLM 模型后端适配。
- `backend/hybrid`：VLM 与 pipeline/OCR 小模型融合路径。
- `backend/office`：DOCX、PPTX、XLSX 原生解析。
- `backend/utils`：Markdown、HTML image、段落、运行时等后端共用工具。

### `mineru/model`

`model` 是模型和格式转换实现层，包括：

- layout：`PPDocLayoutV2LayoutModel`。
- OCR：`PytorchPaddleOCR`。
- 公式：`UnimernetModel` 或 `FormulaRecognizer`。
- 表格：有线表、无线表、表格分类、表格方向分类。
- Office：DOCX/PPTX/XLSX converter。
- VLM server：vLLM、LMDeploy 服务封装。

### `mineru/data`

`data` 抽象读写后端，包括本地文件 writer、dummy writer、S3、多 bucket S3、HTTP/S3 IO 等。当前主路径主要使用 `FileBasedDataWriter` 写本地输出目录。

### `mineru/utils`

`utils` 是跨层工具集合，涵盖 PDFium 保护、PDF 分类、页码处理、bbox、OCR 工具、模型下载、配置读取、表格合并、标题层级后处理、VLM 引擎选择等。

## 3. API、CLI 与 Router 编排

FastAPI 的公开接口定义在 `mineru/cli/fast_api.py`：

- `POST /file_parse`：同步解析接口。内部仍创建异步任务，然后等待任务完成并返回结果。
- `POST /tasks`：异步提交接口，返回 task id。
- `GET /tasks/{task_id}`：查询任务状态。
- `GET /tasks/{task_id}/result`：获取任务结果。
- `GET /health`：返回版本、协议版本、队列数、并发限制、窗口大小、任务保留时间等。

所有公开解析参数由 `mineru/cli/api_request.py` 的 `ParseRequestOptions` 管理，包含：

- 输入文件列表。
- `backend`：`pipeline`、`vlm-*`、`hybrid-*` 等。
- `parse_method`：`auto`、`txt`、`ocr`。
- `lang_list`、`formula_enable`、`table_enable`、`image_analysis`。
- `server_url`：用于 `*-http-client` 后端。
- 返回产物开关：Markdown、middle JSON、model output、content list、images、zip、原文件等。
- 页码范围：`start_page_id`、`end_page_id`。

任务调度由 `AsyncTaskManager` 管理。它维护：

- 进程内任务表 `tasks`。
- 每个任务的事件 `task_events`。
- `asyncio.Queue` 等待队列。
- dispatcher task。
- cleanup task。
- active processor task 集合。

任务执行时通过 `_request_semaphore` 控制最大并发，再调用 `run_parse_job`，最终落到 `do_parse` 或 `aio_do_parse`。

`mineru-router` 位于更上层。它维护 worker pool，可以拉起本地 worker，也可以接入远程 upstream。Router 通过 upstream 的 `/health` 做健康检查，选择健康节点后转发 `/tasks` 或 `/file_parse` 请求。它适合多服务、多 GPU、多 worker 场景，但任务状态仍依赖后端 API 或 Router 自身的内存态记录。

## 4. 统一解析分发

真正的统一解析入口是 `mineru/cli/common.py`：

- `do_parse`：同步解析入口。
- `aio_do_parse`：异步解析入口。

执行顺序如下：

1. 先执行 `_process_office_doc`，DOCX/PPTX/XLSX 会被原生解析并从 PDF 列表中移除。
2. 剩余 PDF/image 输入统一准备为 PDF bytes；图片输入会先转成 PDF bytes。
3. PDF bytes 通过 PDFium 重写和裁页，处理 `start_page_id` / `end_page_id`。
4. 根据 `backend` 分发到 `_process_pipeline`、`_process_vlm` 或 `_process_hybrid`。
5. 每条后端都产出 `middle_json` 和 `model_output`。
6. `_process_output` 根据 `process_mode` 选择对应 `union_make`，生成 Markdown、content list、middle JSON、model JSON、图片和原文件。

输出目录结构大致如下：

```text
output_dir/
  document_stem/
    pipeline-or-ocr-or-vlm-or-hybrid_xxx-or-office/
      document_stem.md
      document_stem_middle.json
      document_stem_model.json
      document_stem_content_list.json
      document_stem_content_list_v2.json
      images/
```

## 5. Pipeline 后端

pipeline 是传统小模型流水线，优点是稳定、低幻觉、CPU/GPU 都可运行。主入口是 `mineru/backend/pipeline/pipeline_analyze.py` 的 `doc_analyze_streaming`。

核心机制是多文件滑动窗口：

1. 为每个文档创建 context，包括 PDF bytes、PDFium document、页数、语言、OCR 开关、middle JSON、model list、image writer 等。
2. 按 `processing_window_size` 从多个文档中取页面组成 batch。
3. 将 PDF 页渲染为 PIL image。
4. 调用 `batch_image_analyze` 做批量推理。
5. 将 batch 结果 append 到对应文档的 middle JSON。
6. 某个文档完成后立即触发 `on_doc_ready`。
7. 上层 `_process_pipeline` 在线程池中执行输出写入，实现长文档流式落盘。

`MineruPipelineModel` 位于 `backend/pipeline/model_init.py`，组合了多个原子模型：

- layout 模型：`PPDocLayoutV2LayoutModel`。
- OCR 模型：`PytorchPaddleOCR`。
- 公式模型：`UnimernetModel` 或 `FormulaRecognizer`。
- 表格模型：有线表、无线表、表格分类、表格方向分类。

pipeline 的模型输出转换在 `backend/pipeline/model_json_to_middle_json.py`。它通过 `MagicModel` 把 layout/OCR/公式/表格检测结果组织成：

- `preproc_blocks`
- `discarded_blocks`
- `page_size`
- `page_idx`

并负责裁剪 image/table/chart/interline equation 等 span。后处理包括：

- 段落切分。
- 公式编号合并。
- post OCR。
- 跨页表格合并。
- 标题层级处理。
- 内部元数据清理。

## 6. VLM 后端

VLM 主入口是 `mineru/backend/vlm/vlm_analyze.py`。它围绕 `mineru_vl_utils.MinerUClient` 封装不同推理后端：

- `transformers`
- `vllm-engine`
- `vllm-async-engine`
- `lmdeploy-engine`
- `mlx-engine`
- `http-client`

`ModelSingleton` 按 `(backend, model_path, server_url)` 缓存 predictor，避免重复加载大模型。非 http 后端如果没有传 `model_path`，会自动下载或定位 VLM 模型。服务关闭时通过 `shutdown_cached_models` 释放模型、engine、processor 等运行时资源。

VLM 推理同样按处理窗口运行：

1. 打开 PDFium document。
2. 按 `processing_window_size` 切页。
3. 将当前窗口页面渲染成 PIL image。
4. 调用 `predictor.batch_two_step_extract(...)`。
5. 异步路径调用 `predictor.aio_batch_two_step_extract(...)`。
6. 把每页 block list append 到 middle JSON。
7. 如果不是 client-side output generation，则执行 `finalize_middle_json`。

VLM 的模型输出转换在 `backend/vlm/model_output_to_middle_json.py`。核心是 `MagicModel(page_blocks, width, height)`，它从 VLM block list 中抽取：

- image blocks
- table blocks
- chart blocks
- title blocks
- text blocks
- interline equation blocks
- code/ref/phonetic/list 等 block

然后裁剪图片资源、替换 inline table image、构造 page info。VLM finalize 主要做段落构建、文本块合并、跨页表格合并、标题层级处理和内部 metadata 清理。

## 7. Hybrid 后端

hybrid 是 VLM 与 pipeline/OCR 小模型的融合路径，主入口是 `mineru/backend/hybrid/hybrid_analyze.py`。它的目标是在复杂版面上利用 VLM 的结构识别能力，同时借助传统 OCR 和公式模型降低文本、公式幻觉风险。

hybrid 先根据 `parse_method` 判断 `_ocr_enable`，再通过 `_should_enable_vlm_ocr` 决定是否走 VLM OCR 特化路径。影响因素包括：

- 是否是 OCR/扫描类文档。
- 语言是否为 `ch` 或 `en`。
- 是否启用行内公式。
- 是否通过环境变量强制 VLM OCR 或强制 pipeline。

如果启用 VLM OCR 路径：

1. VLM 完整提取 block。
2. 额外运行 layout 小模型辅助标题拆分。
3. OCR det 只作为视觉行级 sidecar，不填充文本。
4. 合并 sidecar 后进入 hybrid middle JSON 转换。

如果不启用 VLM OCR 路径：

1. VLM 调用时传入 `not_extract_list`，让部分文本类内容不由 VLM 直接抽取。
2. pipeline 侧补跑 OCR det、公式识别和 layout 标题拆分。
3. 将 inline formula 和 OCR text sidecar 合并回 VLM block list。
4. 再转换为 middle JSON。

hybrid 的 batch 大小由 `MINERU_HYBRID_BATCH_RATIO` 或显存自动估算控制。显存越大，batch ratio 越高。

## 8. Office 原生解析

Office 路径位于 `mineru/backend/office`，覆盖 DOCX、PPTX、XLSX。它不走 PDF 渲染和 OCR，而是调用 `mineru/model/docx|pptx|xlsx` 下的 converter，将 Office 文档直接转换为结构化 blocks。

以 DOCX 为例，`office_docx_analyze` 会：

1. 把 bytes 包装为 `BytesIO`。
2. 调用 `mineru.model.docx.main.convert_binary`。
3. 得到 page/block 结构。
4. 调用 `backend/office/model_output_to_middle_json.py` 的 `result_to_middle_json`。

Office 输出的 middle JSON 标记为 `_backend: "office"`。页面信息中直接包含 `para_blocks`，并处理：

- 内嵌图片写出。
- 表格中的 inline image 替换。
- chart 图片保存。
- 目录锚点校验。
- 标题章节编号推导。

## 9. 输出体系

MinerU 的统一输出思路是：先归一化为 `middle_json`，再从 `middle_json` 派生面向用户或下游系统的产物。

主要输出包括：

- `*_middle.json`：完整中间结构，是二次开发最重要的契约。
- `*_model.json`：原始或近原始模型输出，主要用于 debug。
- `*.md`：Markdown。
- `*_content_list.json`：旧版平铺内容列表。
- `*_content_list_v2.json`：3.0 后新增的跨后端通用内容列表。
- `images/`：裁剪出的图片、表格、公式、图表等资源。
- 可视化 PDF：layout/span bbox 可视化，主要用于质检。

API 返回结果时，`build_result_dict` 会从输出目录读取对应文件。如果 `response_format_zip=true`，则由 `create_result_zip` 打包为 zip；否则返回 JSON，图片以 base64 data URL 形式内嵌。

## 10. 并发、缓存与资源控制

MinerU 的并发控制分为几层：

- API 层：`_request_semaphore` 限制同时处理的请求数。
- CLI 层：对 pipeline 后端按页数和 `processing_window_size` 规划 batch task。
- 后端层：pipeline、VLM、hybrid 都用 `processing_window_size` 控制单次渲染和推理窗口。
- Router 层：基于 worker 健康状态和 upstream 状态进行请求分发。

模型缓存也分层：

- pipeline 组合模型按 `(lang, formula_enable, table_enable)` 缓存。
- pipeline 原子模型按模型名、语言、设备、OCR 参数等缓存。
- VLM predictor 按 `(backend, model_path, server_url)` 缓存。
- hybrid 复用 VLM predictor，同时使用 pipeline/hybrid 小模型。

资源释放方面：

- PDFium document 显式 close。
- PIL image 在窗口处理后显式 close。
- VLM shutdown 尝试调用多种 engine 的 `shutdown`、`close`、`stop`、`terminate`、`destroy` 等方法。
- FastAPI lifespan 结束时调用 VLM 模型和 PDF render executor 的 shutdown。

## 11. 扩展点

### 新增输入格式

优先仿照 Office 路径实现：

1. 在 `mineru/model/<format>` 下实现 native converter。
2. 在 backend 层将 converter 结果转为 middle JSON。
3. 复用已有 `union_make` 或新增对应输出生成器。
4. 在 `cli/common.py` 的 suffix 和 `_process_*` 分发中接入。

### 新增解析后端

新后端应该接入 `cli/common.py` 的 backend 分发，并满足两个基本契约：

- 输出 `middle_json`。
- 输出可选的 `model_output`。

如果新后端结构接近 VLM 或 pipeline，应尽量复用现有 `model_output_to_middle_json` 和 `middle_json_mkcontent`。

### 新增远程推理

优先走已有 `*-http-client` 或 Router upstream 机制，不建议绕过 `ParseRequestOptions` 和 task manager。否则会丢失并发限制、健康检查、安全策略、结果打包和清理机制。

### 新增输出格式

建议从 `middle_json` 或 `content_list_v2` 派生，不要直接从模型输出生成。这样可以跨 pipeline、VLM、hybrid、Office 后端复用。

## 12. 风险与注意事项

- `mineru/cli/common.py` 是事实上的核心调度中心，承担输入分发、后端选择、输出落盘等职责，复杂度较高。
- `middle_json` 是全系统隐式契约，各后端结构相似但不完全相同，做二次开发前应先读 `docs/zh/reference/output_files.md`。
- VLM 推理核心已经外移到 `mineru_vl_utils`，本仓库主要能看到封装和调用，不能完整审计模型内部 two-step extract。
- API 任务状态是进程内存态，不是持久化队列；服务重启会丢失任务状态和临时输出。
- Router 是应用层负载均衡，不等同于强一致任务调度系统。生产环境需要结合外部进程管理、日志、持久化存储和监控。
- `client_side_output_generation` 会改变服务端输出职责：服务端返回 staged middle JSON 和 images，由客户端重建最终 Markdown/content list。

## 13. 推荐阅读路径

如果要继续深入源码，建议按以下顺序阅读：

1. `pyproject.toml`：看命令入口和依赖分组。
2. `mineru/cli/api_request.py`：看公开 API 协议。
3. `mineru/cli/fast_api.py`：看任务模型和 API 生命周期。
4. `mineru/cli/common.py`：看统一解析分发。
5. `mineru/backend/pipeline/pipeline_analyze.py`：看 pipeline 滑动窗口。
6. `mineru/backend/vlm/vlm_analyze.py`：看 VLM predictor 缓存和窗口推理。
7. `mineru/backend/hybrid/hybrid_analyze.py`：看 hybrid 的 VLM/OCR/公式融合策略。
8. `mineru/backend/*/*_model_output_to_middle_json.py`：看各后端如何归一化到 middle JSON。
9. `mineru/backend/*/*_middle_json_mkcontent.py`：看 Markdown 和 content list 生成。
10. `docs/zh/reference/output_files.md`：看输出结构契约。
