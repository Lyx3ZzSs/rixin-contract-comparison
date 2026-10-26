# 国能日新 · 合同智能审查平台

这是一个前后端分离的国能日新 · 合同智能审查平台 MVP。后端 FastAPI 负责上传两份 PDF 合同、通过可插拔文档识别器提取结构化文本和坐标、按条款识别差异、生成 PDF 报告；前端 React 工作台负责上传、原始 PDF 在线预览、差异高亮渲染和产物下载。它面向合同审查流程，不是普通逐字 Diff 工具。

## 功能

- 文档识别默认走 PyMuPDF，扫描件切换到远端 PP-OCRv5。
- 文本型 PDF、扫描件 PDF 的结构化文本提取。
- 条款切分和坐标证据绑定。
- 条款编号、标题、正文相似度匹配。
- 新增、删除、修改差异识别。
- 上传后统一比对印章、签署区域及页眉页脚，不再提供排除开关。`/api/compare` 已移除相关参数；TextIn 兼容入口不再接受 `remove_stamp`、`remove_headerfooter`。历史结果保持保存时的检测内容，重新执行时采用完整比对。
- 统一事件结果页：左右原始 PDF 预览、差异高亮、同步滚动、差异项定位、审核和事件报告导出。
- 提前预览：两份输入文件发布后，处理中也可阅读 PDF、缩放和同步滚动；最终检查完成后显示差异并开放报告导出。性能候选的启用条件与实测结果见 [2026-09-16 优化实施记录](docs/reports/comparison-optimization-implementation-20260916.md)。
- 匹配加速默认开启（`MATCH_PERFORMANCE_STRATEGY=enabled`）：严格精确对应的条款跳过重排，发生对应冲突时回退完整匹配；设为 `off` 可关闭。配置在后端进程启动时读取。
- 图像计算优化：表格与矩阵码检查默认并行分析两页，签章笔画使用等价数组计算；`COMPARE_IMAGE_ANALYSIS_MAX_INFLIGHT=1` 可恢复串行分析。[实测与验证记录](docs/reports/comparison-image-computation-optimization-20260916.md)。
- 旧版审计页面已下线，旧链接（包括 `?view=legacy`）统一打开事件结果页；历史结果缺少分组快照时按独立变化展示，保留原始明细、历史审核记录和旧报告。
- `合同差异分析报告` PDF：修改项左右对照并突出变化片段，新增/删除项完整展示，长条款自动分页并重复标题；记录页和结果页统一导出事件报告。

## 安装与启动

准备后端环境变量：

```bash
cp .env.example .env
```

`.env.example` 提供服务配置示例；接入扫描件 OCR 与版面识别时，按实际环境填写 `PPOCRV5_URL` 和 `PPDOCLAYOUT_ENDPOINT`。

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
python scripts/run_api.py
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

## 统一身份认证（Keycloak）

认证由前后端一致的模式开关控制，默认保持 OIDC：

```env
AUTH_MODE=oidc
VITE_AUTH_MODE=oidc
```

本地开发或明确隔离的可信内网可将两项都设为 `disabled`。后端不会移除用户语义，而是使用
`AUTH_DISABLED_USER_SUB`、`AUTH_DISABLED_USER_NAME` 和 `AUTH_DISABLED_USER_ROLES`
配置的固定身份；前端使用对应的 `VITE_AUTH_DISABLED_USER_*` 值展示用户与菜单。关闭认证意味着
后端 API 不再校验访问凭证，不得用于公网或不可信网络。修改 `VITE_*` 后需要重新构建前端。

前端本地开发必须使用 `http://127.0.0.1:5173/`，当前认证注册未授权 `localhost:5173`。未登录访问任意页面会自动跳转 Keycloak，并经 `/callback` 回到同一应用内的安全路径。

前端使用公有客户端 `rixin-contract-comparison-web` 的授权码 + PKCE 流程，所有 OIDC 协议状态只保存在浏览器 `sessionStorage`；不要在前端添加 Client Secret。后端通过 discovery 文档获取签名密钥，验证 RS256 签名、issuer、过期时间和 API audience，并只从 `resource_access.rixin-contract-comparison-api.roles` 读取角色。

- `agent_admin`：管理后台及全部新建任务。
- `agent_manager`、`agent_user`：仅能查看和操作本人创建的任务。

任务所有权以 Keycloak 的 `sub` 固化；无 `owner_sub` 的历史任务不会迁移，并会从列表隐藏且直接访问返回 404（管理员亦然）。测试账号请向认证管理员申请。此前通过非安全渠道暴露的后端凭据应立即轮换；本 Resource Server 的 JWT 校验不需要该凭据。

Docker 部署前，必须由认证管理员为容器的实际地址注册精确的回调地址、登出回调地址和 Web Origin。不要将 `127.0.0.1:5173` 的开发回调地址直接复用于 `/contract` 容器部署。

如果 `8000` 端口已被占用，可换一个端口：

```bash
cd backend
PORT=8001 python scripts/run_api.py
```

`python scripts/run_api.py` 是唯一受支持的 API 启动方式；它会强制单个 API 进程。PyCharm Run 配置也应指向该脚本。

如果后端不是运行在 `http://127.0.0.1:8000`，在前端 `.env` 中配置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8001
```

## Docker 部署

本项目支持 Docker Compose 部署前后端容器，PP-DocLayoutV3 和 OCR 使用外部服务。详细步骤见 [Docker 部署指南](docs/docker-deployment.md)。

快速启动：

```bash
cp .env.example .env
docker compose up -d --build
```

## 文档识别配置

后端运行配置集中在仓库根目录 `.env`，模板见 `.env.example`；也可在 `backend/.env` 放后端本地覆盖。代码读取和校验入口在 `backend/app/config.py`，默认提示词在 `backend/app/config_defaults.py`。

## 架构边界

后端代码统一在 `backend/` 下，并按 HTTP 适配、应用编排、基础设施适配和文档处理服务分层。`backend/app/api*.py` 只负责请求/响应和 HTTP 错误映射；`backend/app/application/` 负责任务创建与后台提交；`backend/app/infrastructure/` 封装可替换任务仓储、产物路径防护和后台执行器；`backend/app/services/` 保留合同解析、对比、证据定位、风险分析和报告生成能力。更多说明见 `docs/architecture.md`。



合同比对固定使用 Native-first、PP-DocLayoutV3 与选择性 PP-OCRv6。Native 页保留字符坐标且不调用 OCR；Mixed 页只对未覆盖区域调用 OCR；Scanned 页执行一次全页 OCR。

```bash
PPDOCLAYOUT_ENDPOINT=https://your-layout-host/layout-detection
PPOCRV5_URL=https://your-ocr-host/
```

合同比对请求逐页调用 `PPDOCLAYOUT_ENDPOINT`，固定 `dynamicBatch=false`。扫描页的 Layout 和全页 OCR 并行；Mixed 页先分析 Layout 与覆盖率，再识别缺失区域。识别失败会使比对任务失败。

接入默认扫描件识别链路时配置：

```bash
PPDOCLAYOUT_ENDPOINT=https://your-layout-host/layout-detection
PPDOCLAYOUT_TIMEOUT=120
PPDOCLAYOUT_MAX_INFLIGHT=4
PPDOCLAYOUT_TITLE_THRESHOLD=0.4
PPDOCLAYOUT_DEFAULT_THRESHOLD=0.5
PPOCRV5_URL=https://your-ppocrv5-host/
PPOCRV5_TIMEOUT_SECONDS=600
PPOCRV5_RETURN_WORD_BOX=true
PPOCRV5_USE_DOC_ORIENTATION_CLASSIFY=false
PPOCRV5_USE_DOC_UNWARPING=false
PPOCRV5_USE_TEXTLINE_ORIENTATION=false
PPOCRV5_TEXT_REC_SCORE_THRESH=0.0
SAVE_OCR_RAW_RESULT=true
```

表格结构只处理 Layout 标记的真实 table region。单元格网格或文字归属无法验证时，保留文字和页面证据，不发布推测的表格 HTML。差异结果来自程序化条款匹配和结构化 diff。

点击导出报告或请求 `/api/compare/{task_id}/report` 时，系统不会调用大模型，会生成包含基础信息和审计统计改动点表格的差异分析报告。

### 比对计算复用

页面序列对齐会先检查拆并页的必要条件，通过后才计算组合页相似度。条款匹配默认通过
`MATCH_FEATURE_CACHE_ENABLED=true` 复用条款路径、页关系、向量长度及有向相似度，保留全部候选和模型请求。
路径与页关系各最多缓存 8,192 项，向量长度最多 4,096 项，向量对分数最多 100,000 项；超过上限按 LRU
淘汰并重新计算。缓存只属于一次匹配，正常结束或异常退出都会释放。设为 `false` 可禁用匹配计算复用。

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

### 15-contract Golden Blocking Gate

当前正式业务基线版本为 `contract-golden-v3`，包含 15 份合同、30 个固定 PDF SHA256 和 255 条语义断言。新增五份的原件核对和验收结果见 `backend/tests/fixtures/contract_golden/UPDATE-v3.md`。真实合同 PDF 属于私有测试数据，不提交到普通源码仓库；开发机或 CI 需挂载版本固定的 bundle：

```bash
export GOLDEN_CONTRACT_DATA_ROOT=/secure/path/contract-golden-v3
make golden-contract
```

该统一命令依次校验 30/30 输入文件的 SHA256 和页数、Fresh Replay 15/15 合同、执行 semantic blocking test，并检查 Golden 高风险文件是否发生变化。CI 不绑定具体平台，只需挂载 bundle、设置环境变量并执行上述命令。任一 blocking case 被跳过或以下条件任一不满足，命令均以非零状态退出：

- 15/15 contracts executed，0 skipped。
- positive、negative、review assertions 100% PASS。
- unexpected confirmed diff 为 0。
- bbox regression 为 0。

在多 commit 的 PR/代码审查中，CI 应额外将目标分支对应的 Git revision 通过平台无关变量 `GOLDEN_BASE_REF` 传入，以检查完整变更范围；未设置时，干净 checkout 默认检查 `HEAD^...HEAD`。

后续修改 comparison pipeline 的标准流程是：

```text
修改代码 → unit tests → Golden 15-contract replay → PASS → 允许合并
```

如果 Golden 失败，禁止直接修改 Golden 让测试变绿：

- A. 代码回归：修复代码，保持 Golden 不变。
- B. 算法能力真实提升且业务结果合理变化：人工核对合同原件，批准新的业务真相后再修改 Golden。

Golden 变更必须先运行语义化变更检查；它会列出 added cases、removed cases、changed expected decision、changed semantic expectation 和 changed bbox expectation：

```bash
python backend/scripts/check_golden_baseline_change.py
```

人工审查通过后才可设置：

```bash
GOLDEN_BASELINE_CHANGE_APPROVED=1 make golden-contract
```

该变量仅表示允许提交已经人工审核的 Golden 修改，不会生成、更新或覆盖 Golden。`contract_golden/manifest.json`、`oracle/*`、`semantic_matcher.py`、blocking test 和 baseline change checker 均属于高风险保护区，修改时必须在 PR/代码审查中显式提示。

## 当前限制

- 合同比对必须配置可用的 `PPOCRV5_URL` 和 `PPDOCLAYOUT_ENDPOINT`；需要 OCR 的页面在 OCR 服务不可用时会失败。
- 合同对比暂不支持 Word、Excel 和复杂表格深度 diff。
- 任务由单个 API 进程内的工作线程异步执行；结构化状态存储在本地 SQLite，文件产物存储在本地文件系统。
- 当前部署只支持单个 API 进程，不支持多实例或跨机器任务调度；服务重启时未完成任务会标记失败，可手动重试。
- 报告导出不会在导出时重新调用大模型；风险统计基于任务已有的差异分析结果。

## 后续扩展

- 增强表格、金额、日期、主体信息等合同要素结构化抽取。
- 引入异步任务队列和任务进度。
- 增加规则风险引擎或按需接入大模型审查总结。
