# 合同PDF预览性能优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 默认在当前工作流逐项实施，不自动委派，不自动提交或部署。

**Goal:** 基于现有PDF.js实现更快的首屏、更稳定的差异跳转与双栏滚动，同时控制重复绘制和缓存占用。

**Architecture:** 继续使用现有PDF接口和差异证据层；增加轻量页面尺寸元数据、双栏共享的渲染调度器及有限容量的Canvas缓存。采用当前可见页优先、跳转目标页优先、邻近页空闲预渲染；将PDF下载策略作为独立可回退的优化项。

**Tech Stack:** React 19、TypeScript、现有pdfjs-dist 5.5.207、Canvas、FastAPI/Starlette FileResponse、现有PyMuPDF、Vitest及pytest。

---

**执行结果（2026-09-14）：** Task 1–7 的本轮实现、策略选择、回归和本地验收已完成。复审发现的导航竞态已修复，前端79项、后端15项通过。冷热加载最终对照每版本/网络各30轮。限速冷首屏降低30%的目标未达，因此不宣称全部性能目标通过；下载策略诊断后保留默认，未扩大已失败候选的全量实验。执行中的测量口径调整、报告样本缺失及本地部署边界见 [验证报告](../../reports/pdf-preview-performance-verification.md)。

## 1. 范围与方案选择

本方案只处理合同页面加载与显示性能。前面的页脚差异归组、TextIn计数对齐、PDF第54页对应关系问题分别保留为独立工作；本方案的测试必须证明事件、审核及导出数据未受影响。

| 路线 | 收益 | 成本与取舍 | 决策 |
|---|---|---|---|
| 完善现有PDF.js的调度、缓存和定位 | 能利用已有Range、坐标和高亮能力，改动范围可控 | 大型扫描PDF的解析和共享资源下载仍可能成为瓶颈 | 本轮实施 |
| 全面改为服务端逐页图片 | 能独立请求页图，把客户端PDF解析转移到服务端 | 新增页图生成、清晰度档位、存储、缓存失效与接口维护 | 实测仍不达标时再立项 |
| 同时维护PDF与图片两套默认预览 | 可按文档特点选择 | 当前会扩大测试矩阵和故障定位范围 | 本轮不采用 |

保留现有双栏页面布局、缩放范围40%—160%、差异选择和审核操作。本轮不新增用户可见的技术配置。

## 2. 已核实的起点

- `PdfDocumentViewer.tsx` 已按可视范围挂载Canvas，其他页为占位框；上下各800px为预渲染缓冲。
- 两栏分别创建PDF.js文档实例，渲染任务缺少跨栏统一优先级和并发控制。
- 离开窗口会卸载页面，再返回时重新进入 `page.render()`；没有应用层的已绘制页面缓存。PDF.js自身的数据缓存与这种Canvas缓存是不同层次。
- 未测量页高使用A4估计值或已测页高，混合纸张尺寸可能引起滚动偏移和跳转二次移动。
- `getDocument` 未覆盖默认流式读取及自动预取配置；可视区渲染不等于只下载可见页的数据。
- 本地比对PDF接口已实测支持 `Range: bytes=0-0`，返回206、Content-Range和ETag。该结论仅覆盖当前本地直连链路，部署代理链路须另测。
- 高亮层独立于PDF Canvas；双栏联动已使用 `eventAlignment.counterpart()`，未知对应关系保持另一侧位置。

代码落点：

| 文件（均以仓库根目录为起点） | 职责与改动 |
|---|---|
| `frontend/src/components/PdfDocumentViewer.tsx` | 接入元数据、共享运行时、缓存Canvas及稳定导航；保留高亮语义 |
| `frontend/src/components/pdfPageScroll.ts` | 基于准确页高、真实页面间距计算窗口和目标位置 |
| `frontend/src/pages/ResultPage.tsx` | 每次结果视图创建一个双栏共享运行时，接入元数据与导航完成状态 |
| `frontend/src/lib/pdfPreviewTypes.ts`（新建） | 明确页面尺寸、缓存键、任务及性能记录类型 |
| `frontend/src/lib/pdfRenderScheduler.ts`（新建） | 渲染优先级、并发、取消与公平调度 |
| `frontend/src/lib/pdfCanvasCache.ts`（新建） | Canvas借用、归还、容量计量和淘汰 |
| `frontend/src/lib/pdfPreviewRuntime.ts`（新建） | 组合调度器、缓存、会话代次、文档生命周期和开发态指标 |
| `frontend/src/lib/api.ts`、`frontend/src/types.ts` | 获取、校验、声明预览元数据响应 |
| `backend/app/api.py`、`backend/app/api_schemas.py` | 增量增加元数据接口，复用既有任务访问控制 |
| `backend/app/services/pdf_preview_metadata.py`（新建） | 从源PDF读取显示页尺寸、生成文件版本、缓存元数据 |
| `backend/tests/test_pdf_preview_metadata.py`（新建） | 元数据、旋转、缓存失效及权限边界测试 |
| `frontend/src/lib/pdfRenderScheduler.test.ts`、`pdfCanvasCache.test.ts`（新建） | 调度和资源生命周期反例测试 |
| `frontend/src/components/PdfDocumentViewer.test.tsx`、`frontend/src/pages/ResultPage.test.tsx` | 跳页、缩放、联动、错误与计数稳定性回归 |
| `docs/reports/pdf-preview-performance-verification.md`（新建） | 基线、候选、网络数据、图像定位和启用结果 |

## 3. 接口与行为约定

### 3.1 页面元数据

新增 `GET /api/compare/{task_id}/preview-metadata`，复用当前任务访问权限；使用现有任务PDF，不重新跑OCR和比对。不向客户端暴露服务器文件路径。

```ts
export type PdfSide = "original" | "compare";
export interface PreviewPage {
  page_no: number;          // 从1开始的物理页码
  width_points: number;    // 与PDF.js默认显示方向一致的宽度
  height_points: number;
  rotation: 0 | 90 | 180 | 270;
}
export interface PreviewDocument {
  source_revision: string; // 文件版本，不使用审核revision或访问令牌
  page_count: number;
  pages: PreviewPage[];
}
export interface PreviewMetadata {
  schema_version: 1;
  coordinate_space: "display_pdf_points";
  documents: Record<PdfSide, PreviewDocument>;
}
```

`source_revision` 根据任务、侧别、文件大小、mtime_ns及元数据版本生成不透明标识；这是文件版本标识，不冒充内容SHA-256。任务输入仍按既有不可变文件约定管理，替换输入必须使版本失效。

页尺寸读取 `fitz.Page.rect` 的显示尺寸，并记录旋转。使用非零CropBox、90/270度旋转样本验证与 `page.getViewport({scale:1})` 一致；已经处于显示坐标系的差异框不能重复旋转。

元数据按任务缓存在 `storage/tasks/{task_id}/preview/metadata-v1.json`，使用现有原子文件写入工具；删除任务时随任务目录清理。读取时验证文件版本，旧缓存自动重建，缓存不写入业务结果或改变任务revision。

前端并行启动PDF加载和元数据请求，元数据不能成为显示首屏的额外串行阻塞。接口暂不可用时继续当前预览；导航目标页尺寸缺失时优先读取该页PDF几何信息，避免直接按A4估计值落位。

### 3.2 渲染与缓存

```ts
export type RenderPriority = 0 | 1 | 2; // 0:跳转目标 1:当前可见 2:邻页
export interface RenderJob {
  key: string;
  generation: number;
  side: PdfSide;
  priority: RenderPriority;
  signal: AbortSignal;
  run: (signal: AbortSignal) => Promise<void>;
}
export interface PreviewPolicy {
  renderConcurrency: number;
  backgroundConcurrency: number;
  idleCanvasBudgetBytes: number;
  neighborPages: number;
}
export const previewPolicy: PreviewPolicy = {
  renderConcurrency: 2,
  backgroundConcurrency: 1,
  idleCanvasBudgetBytes: 96 * 1024 * 1024,
  neighborPages: 2,
};
```

这些是首轮实验值，不照搬TextIn的预加载参数5。双栏合计最多2个渲染任务，后台最多1个；可见任务等待时取消或让出后台渲染。邻页任务在当前可见页完成后，通过 `requestIdleCallback` 启动；缺少该API时用可取消的定时器。

队列同优先级按等待时间调度，避免一侧长期得不到渲染。先异步取得PDFPageProxy，再进入Canvas绘制队列；等待页面数据不长期占据绘制槽位，待取页集合受当前窗口和邻页范围限制。缓存命中优先直接显示；网络慢的跳转目标不能让另一侧已就绪的可见页长期空白。

Canvas缓存键至少包含任务/侧别、源文件版本、页码、显示旋转、实际zoom及DPR。禁止把审核revision或轮换令牌放入键。缓存存放实际Canvas，页面卸载后归还，命中后重新绑定到页面容器；同一Canvas同一时刻只能被一个视图借用，避免复制一份位图又留一份缓存。

- 96MiB限制只针对闲置Canvas缓存，按 `width × height × 4` 计量；这是位图估算，不是浏览器总内存承诺。
- 淘汰最久未访问且未借出的Canvas，清空其width/height释放绘图缓冲。渲染中和显示中的Canvas不参与闲置淘汰。
- 缓存放不下的大页显示后直接释放，不挤占全部缓存；不为达到内存指标暗中降低正文或签字的清晰度。
- 统计闲置、显示中、渲染中三部分预计位图占用，浏览器和PDF.js Worker占用另测。
- 切换任务、输入文件版本变化、退出结果视图或退出会话时释放全部相关资源。令牌正常刷新保持当前任务加载连续。

### 3.3 跳转、联动与缩放

给每次跳转递增 `navigationId`，每次文档/缩放改变递增 `generation`。异步任务提交结果前必须匹配当前代次；过期任务只释放资源，不改画布、不改当前页、不触发联动。

保留既有 `scrollToDiff` 返回值兼容当前调用，内部执行：确定证据页 → 优先获得目标页尺寸 → 提升渲染优先级 → 按准确偏移滚动 → Canvas和高亮都准备好后报告完成。只在测量产生必要偏差时做一次受控位置校正，禁止多轮互相追赶。

缩放前记录当前可见页及页内锚点，缩放后恢复该锚点，避免重置到第一页。80ms定时器不再作为“跳转已完成”的唯一依据；导航完成/取消状态控制联动锁，并保留超时释放机制防止永久锁住。

左右两栏继续消费现有显式页对应结果；无对应页保持另一侧位置。预览层不推断同页号对应，也不修改页对应结果。

### 3.4 下载策略

对比现有默认策略与候选按需策略，不直接全局关闭流式读取：

```ts
const defaultTransport = {};
const onDemandTransport = {
  disableRange: false,
  disableStream: true,
  disableAutoFetch: true,
  rangeChunkSize: 256 * 1024,
};
```

候选256KiB再与PDF.js默认64KiB比较请求次数、首屏耗时和远距离跳页延迟。PDF.js要求同时关闭流式读取，禁止自动预取才会按预期生效。

Range按字节工作，不能把一个字节分段等同于一页。带共享字体、交叉引用或扫描图像资源的PDF，显示某页仍可能需要多个远端范围。

对忽略Range并返回200的服务端，允许PDF.js正常回退，不用无休止重试Range。鉴权失败走现有重新鉴权流程，不降级为无鉴权请求。取消请求不展示错误；文件损坏、页面渲染失败显示对应页重试入口，不能只吞异常形成永久白页。

## 4. 任务拆分与执行顺序

### Task 1：固定性能基线和计数快照（P0）

**Files:** 新建 `output/analysis/pdf-preview-performance/baseline.json`、`samples.json`；新建报告草稿；记录现有应用代码版本和工作区状态。

- [x] 记录当前真实54/55页合同的源文件哈希、大小、页数和事件快照。当前历史任务只作为样本ID，不把当时78个展示事件写死为永久断言。
- [x] 选取一份实际较小PDF、一份混合尺寸/旋转PDF，以及从现有PDF按固定规则生成的200页性能样本。生成样本标记为合成数据，只测预览，不冒充真实合同质量样本、不触发OCR。
- [x] 使用生产构建和固定Chrome版本测量；记录机器、DPR、缩放、浏览器缓存状态、网络及CPU限制。分别采用本地不节流、20Mbps/40ms网络两个档位。
- [x] 首屏定义为“左右两栏第一张可见页绘制完成”，跳转定义为“操作开始至目标页画布与高亮就绪”；禁止用JSON返回或占位框出现代替完成。
- [x] 操作序列固定为首次打开、连续翻10页、1→26→末页→1、反复查看第26页、80%→160%→80%缩放、切换任务和返回。性能数据不记录合同正文、姓名或令牌。
- [x] 每项首轮先跑5次诊断，最终对照各30次记录中位数/P95；累计数据不能把冷缓存和热缓存混为一组。

示例指标格式：

```json
{"scenario":"cold_open","sample":"contract-54-55","network":"20Mbps-40ms","run":1,"both_visible_ms":0,"downloaded_bytes":0,"render_count":0,"idle_canvas_bytes":0,"active_canvas_bytes":0,"cancelled_jobs":0}
```

示例中的0是字段格式说明，采集器必须写实测值；未采到的值记录null和原因，不填0冒充测量。

### Task 2：建立准确页几何信息（P0）

**Files:** `backend/app/services/pdf_preview_metadata.py`、`api.py`、`api_schemas.py`、`backend/tests/test_pdf_preview_metadata.py`；前端 `api.ts`、`types.ts`、`pdfPageScroll.ts`。

- [x] 先写接口回归：原件55页/比对件54页、混合A4/A3、旋转90/270度、非零CropBox、文件版本变化、无权任务、非法task_id和损坏文件。用程序生成最小PDF夹具，避免把整份业务合同提交进测试。
- [x] 运行 `python -m pytest -q backend/tests/test_pdf_preview_metadata.py`，确认新接口未实现时失败。
- [x] 实现3.1约定，复用现有 `_load_accessible_or_404`、存储路径检查及原子写入工具。返回长度必须等于page_count，page_no唯一且连续，尺寸必须为正数。
- [x] 前端将元数据转为页高/偏移表，页面间距从当前布局实际值计算；用PDF.js实际测量值校验并只更新有差异的页。
- [x] 增加混合页高跳转测试，明确第二页起点必须包含第一页面高与实际间距，缩放后相同比例变化；元数据请求失败仍能显示PDF。
- [x] 运行上述后端测试及前端预览相关测试，检查源文件和事件快照均未变。

### Task 3：实现双栏优先调度及取消（P0）

**Files:** `pdfPreviewTypes.ts`、`pdfRenderScheduler.ts`、`pdfPreviewRuntime.ts`及相应测试；接入 `ResultPage.tsx` 和 `PdfDocumentViewer.tsx`。

- [x] 定义调度器公开入口：`enqueue(job: RenderJob): Promise<void>`、`cancelGeneration(generation: number): void`、`dispose(): Promise<void>`；dispose拒绝未开始任务并等待运行中任务清理，不遗留悬挂Promise。
- [x] 编写人工控制完成时机的异步测试，验证目标页先于邻页、两栏不超过总并发2、后台不超过1、取消后槽位释放、失败任务不堵塞队列。一个确定性测试结构如下：

```ts
const order: string[] = [];
let release!: () => void;
const blocked = new Promise<void>(resolve => { release = resolve; });
const scheduler = new PdfRenderScheduler({ maxConcurrent: 2, maxBackground: 1 });
const signal = new AbortController().signal;
const add = (key: string, priority: 0 | 1 | 2, side: PdfSide) => scheduler.enqueue({
  key, priority, side, generation: 1, signal,
  run: async () => { order.push(key); await blocked; },
});
const jobs = [add("neighbor", 2, "original"), add("visible", 1, "compare"), add("target", 0, "original")];
await Promise.resolve();
expect(order).toEqual(["target", "visible"]);
release();
await Promise.all(jobs);
await scheduler.dispose();
```

- [x] 首次排队后在微任务中排序并启动，保证同一轮入队任务有机会按优先级竞争；正在执行的可见页不因新邻页而被打断。
- [x] 把 `page.render()` 接入队列；调度器为每个任务持有AbortController，组合调用方取消信号，并把内部signal传入run。cancelGeneration取消对应控制器，signal触发RenderTask.cancel；不能用销毁整个PDF文档取消单页。取消以AbortError结束任务Promise，调用方必须消费该结果，避免未处理拒绝。
- [x] 快速连续选择不同差异时只执行最新导航，旧任务完成不得覆盖新画面；切换任务、缩放和卸载执行代次取消。
- [x] 将邻页预渲染放入低优先级空闲任务，当前窗口更新后移除已不相关的排队任务。首次保留前后各2页作为实验参数，不同时请求整份合同的所有Canvas。

### Task 4：增加有限容量Canvas缓存（P1）

**Files:** `pdfCanvasCache.ts`、`pdfCanvasCache.test.ts`、`pdfPreviewRuntime.ts`、`PdfDocumentViewer.tsx`。

- [x] 定义 `acquire(key): HTMLCanvasElement | null`、`release(key, canvas): void`、`invalidateDocument(sourceRevision): void`、`dispose(): void`，并暴露只读缓存字节数用于测试和开发态指标。借出状态与闲置LRU分开记录。
- [x] 写缓存淘汰、借出对象不得被清空、同键重复归还不得重复计费、源文件/zoom/DPR变化不能误命中、单页超预算直接释放的测试。真实Canvas尺寸用受控测试对象，避免依赖jsdom实现完整PDF绘制。
- [x] 页面Canvas改为由运行时借用并挂载；归还前解除当前DOM绑定，渲染结束后才能进入可复用状态。失败或取消的半成品不得作为缓存命中显示。
- [x] 实现96MiB闲置LRU上限；淘汰时设置canvas.width=0、canvas.height=0并移除引用。组件仍在显示的Canvas不能提前淘汰。
- [x] 以26→27→26真实操作验证：同一尺寸和缩放下缓存命中后不再次调用page.render；改变选中差异只更新高亮层，不重绘页面。
- [x] 在源文件、任务和会话切换时清理；令牌轮换不改变源文件键。检测重建PDF文档或React重复挂载时无残留画布。

### Task 5：稳定跳转、缩放与双栏联动（P1）

**Files:** `PdfDocumentViewer.tsx`、`pdfPageScroll.ts`、`ResultPage.tsx`及现有测试。

- [x] 为目标页未加载、混合页尺寸、末页、快速连续跳转和缩放中跳转编写失败回归；断言最终物理页和证据坐标，不能只断言调用了scrollTo。
- [x] 实现3.3定义的导航状态、优先加载及一次校正；保留现有对外方法兼容性，并增加完成通知供ResultPage解除联动锁。
- [x] 以页内锚点恢复缩放前位置，不再因重置renderRange使用户闪回第一页。
- [x] 双栏始终调用现有counterpart；覆盖MATCHED、MERGED、SPLIT、无对应页和UNCERTAIN，不新增页序猜测。
- [x] 反例验证：缓慢完成的旧任务不反向拖动另一栏；快速滚动不会造成两栏循环触发；文字/章/页脚框在40%、80%、160%均落在相同原图区域。
- [x] 加载失败显示对应页错误和重试，正确区分RenderingCancelledException与实际文件/绘制错误。

### Task 6：对照验证下载策略（P1）

**Files:** `pdfPreviewRuntime.ts`、其测试；部署代理配置仅在探针证明确实需要时修改。

- [x] 完成默认、64KiB及256KiB策略诊断和选择。真实合同与200页样本采用默认/range64各5轮有效网络记录；256KiB因首屏明显退化提前淘汰。10秒实测从导航开始计时，并非首屏后10秒，不将其当作原网络目标通过；默认保留流式下载。
- [x] 测试206及Content-Range、Range被忽略返回200、416、401/403、取消与重试；实际代理入口也要测，不能只检查FastAPI直连。
- [x] 候选策略只在显著减少无用传输且首屏/跳转不明显退化时启用；PDF需要整文件回退时必须可正常显示，不能用下载字节数目标判定文档错误。
- [x] 继续使用现有Authorization传递和令牌刷新机制；ETag/条件请求按实际服务响应工作，合同文件不配置共享公共缓存。
- [x] 在生产构建配置中保留默认/按需策略切换，不新增页面设置项；记录最终选值和选择依据。

### Task 7：真实浏览器验收和启用（P1）

**Files:** `docs/reports/pdf-preview-performance-verification.md`；验证数据存入 `output/analysis/pdf-preview-performance/`。

- [x] 在Chrome通过当前可用浏览器工具执行Task 1同一操作序列；使用浏览器前按项目要求读取ego-browser技能。计时使用性能标记及网络记录，不能以测试框架模拟时间代替真实加载耗时。
- [x] 测试合成200页、真实54/55页、混合尺寸/旋转PDF；额外覆盖连续查看50页、往返10轮、缩放10轮和切换5个任务。
- [x] 对照读取同一任务的events、reviews及报告状态：预览前后事件ID/成员/计数/审核revision不变。本样本没有已生成的报告PDF，验证报告revision及其全部业务数据不变，没有触发生成报告。
- [x] 完成下面的相关检查；仅在失败、改动或未解决疑点出现时扩大测试。

```bash
cd /Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison
python -m pytest -q backend/tests/test_pdf_preview_metadata.py backend/tests/test_api.py backend/tests/test_api_authz.py
cd /Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/frontend
npm test -- src/lib/pdfRenderScheduler.test.ts src/lib/pdfCanvasCache.test.ts src/components/PdfDocumentViewer.test.tsx src/pages/ResultPage.test.tsx src/lib/eventAlignment.test.ts
npm run build
```

预期：相关测试全部通过、构建退出码0；失败必须记录原因和修复后的复验，不能改变业务真值来让预览测试通过。

- [x] 新功能先在本地生产构建核验；加载修复后的实际服务后再验证，记录进程版本及构建哈希，避免只运行测试却仍展示旧代码。
- [x] 根据实测启用本地优化运行时，默认下载策略保持不变；限速冷首屏降低30%的目标未达，报告保留该项。远程生产未部署；回退前端不需要重跑合同或迁移审核数据。
- [x] 清理临时生成且报告不再引用的大文件，保留基线、样本哈希、最终性能记录及失败定位证据。

## 5. 验收目标

以下是待验证的目标，不是已经测得的效果；性能报告必须列出实测值和失败项。

| 项目 | 验收口径 |
|---|---|
| 定位正确性 | 全部目标页、旋转页与混合尺寸样本正确；0次旧任务覆盖、0次双栏循环跳转；高亮相对基线误差不超过2个CSS像素 |
| 事件不变 | 同一固定快照的事件ID、成员、数量、审核和导出数据完全不变；页面缓存/未加载页不得影响计数 |
| 冷首屏 | 当基线P95≥2秒时，目标降低至少30%；基线不足2秒时，退化不得超过10%。不达标须说明瓶颈后再决定是否启用 |
| 热跳转 | 同缩放、缓存命中的目标页，操作至Canvas及高亮就绪P95≤200ms，且不重新调用page.render |
| 冷跳转 | 在指定20Mbps/40ms条件下，P95不比基线退化超过10%；预加载只以完整目标页就绪衡量 |
| 滚动/缩放 | 固定操作中无永久白页、无闪回第一页、无错页；生产构建记录长任务与掉帧，不以主观“更顺滑”代替证据 |
| 缓存 | 闲置Canvas始终≤96MiB；渲染并发≤2、后台≤1；显示和渲染中的占用另记，不把96MiB当整个浏览器上限 |
| 生命周期 | 预热后10轮往返、10轮缩放及5次任务切换，任务数、监听器、缓存和Worker无持续累积；清理后活动队列和持有Canvas归零 |
| 网络 | 启用按需策略需在200页样本首屏后10秒窗口显著减少传输，目标≥50%，且满足首屏/冷跳转指标；不满足则保留默认下载策略并报告原因 |

## 6. 交付顺序与后续边界

**M1：定位基础可靠。** Task 1—2完成，基线和页面尺寸契约可复现。

**M2：交互明显改善。** Task 3—5完成，共享调度、缓存命中和稳定跳转通过正反例及真实图像验证。

**M3：可启用。** Task 6—7完成，按实测选择下载策略，生产构建与实际服务结果一致，并有可回退版本。

仅当M2/M3数据显示PDF解析或不可避免的整文件传输仍是主要瓶颈，才进入逐页图片方案评估。届时单独规划页图生成、分辨率档位、缓存失效、放大清晰度及与现有证据坐标映射；本轮不预先引入第二套预览引擎。

参考：[PDF.js加载参数](https://mozilla.github.io/pdf.js/api/draft/module-pdfjsLib.html)。TextIn当前预览的公开前端代码可作为按页图像、相邻预加载和缓存的实现参考，但不能把其参数直接当作本项目性能结论。
