# 合同对比系统四批次稳定化改造设计

## 文档状态

- 方案：B（按业务纵切面稳定化）
- 日期：2026-07-16
- 范围：合同对比主流程、审核与报告、文件型任务基础设施
- 状态：四部分设计已沟通确认，两轮书面 Review 意见已纳入，待最终审阅

## Review 闭环索引

| # | 处理结论 | 设计定位 |
| --- | --- | --- |
| 1 | Task 取消采用兼容投影，Job 保留独立取消终态 | [任务状态模型](#task-state-model) |
| 2 | Repository-backed Token 通过 ExecutionContext 逐层传递 | [取消协议](#cancellation-protocol) |
| 3 | Job 终态 mark 增加状态、owner 和 lease 前置条件 | [取消协议](#cancellation-protocol) |
| 4 | 证据覆盖重叠率阈值固定为 0.80 | [Diff 去重规则](#diff-deduplication) |
| 5 | canonical diff_id 采用字典序最小 ID | [Diff 去重规则](#diff-deduplication) |
| 6 | 前端展示并更新独立 AuditItem 审核状态 | [AuditItem 审核模型](#audit-item-review-model) |
| 7 | 报告按 report revision 分片互斥并原子发布 | [Revision 化报告](#revisioned-reports) |
| 8 | 单 API 进程、多 Worker 线程统一使用共享 RLock | [队列互斥与 Worker 模型](#queue-locking) |
| 9 | 补偿失败写 recovery marker 并支持幂等恢复 | [流式上传和补偿](#upload-compensation) |
| 10 | 增加 TaskStaleLeaseError 且禁止旧 Worker 写回 | [错误模型](#error-observability) |
| 11 | 原子写入抽取为 Infrastructure 公共原语 | [文件存储原子性](#atomic-storage) |
| 12 | 增加取消竞态、阈值、报告并发和崩溃恢复边界测试 | [测试策略](#test-strategy) |
| 13 | 使用稳定显式 anchor 建立章节交叉引用 | 本索引及各目标章节 |
| 14 | 定义结构化事件、公共字段、错误字段和脱敏示例 | [结构化日志](#structured-logging) |

## 1. 背景与问题定义

当前项目已从“合同对比和字段提取 MVP”演进为合同对比产品。字段提取产品功能已经下线，
但后端仍残留字段提取专属模型、DTO、Presenter、Repository 方法、任务类型和文档描述。
这些残留扩大了维护边界，也容易让后续改造错误地恢复已经退出产品范围的接口。

合同对比主流程已经具备上传、异步处理、结果展示、人工审核和 PDF 报告能力，但整体 Review
发现以下系统性问题：

- 运行中取消仅改变 Job 状态，执行链仍可继续并最终写入成功状态。
- SSE 在任务终态持久化前发布终态事件，前端可能读取并缓存旧的处理中状态。
- SSE 异常后的轮询降级不完整，网络错误可能让结果页永久停留在处理中。
- 差异去重键缺少页码、条款路径和证据位置，可能误合并不同位置的相同文本。
- 没有类型化证据的 Diff 不会生成 AuditItem，导致审计表和报告遗漏真实差异。
- Diff 审核状态和 AuditItem 审核状态并存，统计口径会随数据形态变化。
- 上传、任务保存和 Job 入队之间没有补偿机制，失败时会产生孤儿文件或孤儿任务。
- 本地 JSON 队列只适用于进程内线程，但各 Repository 锁彼此独立，尚未统一保护 Task/Job/manifest
  终态更新；当前也没有明确拒绝多 API 进程配置。
- 报告文件固定路径并发覆盖，缓存没有和任务审核 revision 绑定。
- 质量工作台运行时读取测试 fixture，生产镜像因不包含测试目录而不可用。

## 2. 目标与非目标

### 2.1 目标

1. 移除字段提取产品功能及其遗留代码，收敛产品和架构边界。
2. 保证任务取消、重试、成功和失败终态具有确定且可验证的状态语义。
3. 让 Diff、AuditItem、审核统计和报告使用一致的数据口径。
4. 让上传、任务提交、报告发布和文件队列在失败及并发情况下保持可恢复。
5. 在不引入外部基础设施的前提下，形成可靠的单节点部署形态。
6. 保持现有 `/api/compare/*` 主流程兼容；新增响应字段采用向后兼容方式。

### 2.2 非目标

- 不恢复或兼容 `/api/extract/*`。本次需求已明确允许移除字段提取产品接口。
- 不删除历史 storage 中的字段提取数据，只在读取和索引时安全忽略。
- 不删除合同对比内部的 PDF、Word、图片文本解析和 OCR 能力。
- 不大范围调整 OCR、条款匹配或风险分析算法。
- 不引入 Redis、PostgreSQL、Celery、消息中间件或对象存储。
- 不支持多节点部署，也不承诺多个 API 进程之间的实时 SSE 广播。
- 不在本轮实现历史数据的离线全量重写。

## 3. 术语与边界

“字段提取”指已经退出产品范围的独立用例：用户提交合同后，系统按预定义字段返回结构化字段值，
以及其对应的 `/api/extract/*` API、任务类型和展示逻辑。

“文档提取/解析”指合同对比内部的基础能力：读取 PDF、Word 或图片，执行文本提取、OCR、版面
解析并生成用于比较的文档结构。因此 `backend/app/services/extractors/` 仍属于合同对比主流程，必须保留。

“Diff”是比较引擎产生的语义差异；“AuditItem”是最终用户审核、统计和报告使用的最小业务单元。
一个 Diff 可以映射为一个或多个 AuditItem，但每个 Diff 至少映射为一个 AuditItem。

## 4. 目标架构

保留 FastAPI + React 的整体技术栈和现有分层：

```text
React Result/Create/Records Pages
                |
        /api/compare/* + SSE
                |
FastAPI HTTP Adapters (parse/map/wire only)
                |
Application Use Cases
  create / execute / cancel / retry / review / report
                |
Domain Models and Policies
  task transitions / diff identity / audit projection
                |
Infrastructure Adapters
  task store / job queue / artifact store / report manifest
                |
Single-node filesystem storage
```

API 层只负责请求解析、HTTP 错误映射和响应装配；状态转换、补偿、取消检查和 revision 规则位于
Application/Domain 层；共享进程锁、原子写入和索引维护位于 Infrastructure 层。

## 5. 四批次改造

### 5.1 第一批：产品范围收敛

删除字段提取专属内容：

- 字段值、字段配置等专属领域模型和 schema。
- 字段提取请求/响应 DTO、Presenter 和 JSON 转换函数。
- Repository 中只服务于字段提取结果的读写方法。
- `TaskJobType` 中的 extraction 类型和相关分支。
- 前端字段提取结果类型、菜单开关和已失效入口。
- 架构文档、README、部署说明和 `AGENTS.md` 中已过期的产品描述。

保留内容：

- 合同对比调用的 extractor/OCR/版面解析服务。
- Repository 对历史未知任务类型的容错跳过逻辑。
- storage 中已有文件；本批不做数据清理命令或自动删除。

OpenAPI 和路由回归测试明确断言：合同对比路由存在，字段提取产品路由不存在。

### 5.2 第二批：任务、差异和审核正确性

<a id="task-state-model"></a>

#### 5.2.1 任务状态模型

公开 `TaskStatus` 继续兼容：

- `PROCESSING`
- `COMPLETED`
- `FAILED`

新增可选 `terminal_reason`：

- `NONE`
- `EXECUTION_FAILED`
- `SUBMISSION_FAILED`
- `CANCELLED`

取消后的任务使用 `status=FAILED`、`terminal_reason=CANCELLED`、`stage=已取消`。这样既不破坏只认识
三种 TaskStatus 的客户端，又能让新前端区分“执行失败”和“用户取消”。这里是有意的兼容投影，
不是把 Job 的 `CANCELLED` 重新解释为执行失败：Job 状态是队列执行事实，TaskStatus 是既有公开 API
的粗粒度结果封装，`terminal_reason` 才是 Task 的具体终态原因。旧任务按第 6 节兼容规则推导。

Task 与 Job 的对应关系固定如下：

| Job 状态 | TaskStatus | terminal_reason | 前端文案 | 是否允许 Retry |
| --- | --- | --- | --- | --- |
| `SUCCEEDED` | `COMPLETED` | `NONE` | 已完成 | 否 |
| `FAILED` | `FAILED` | `EXECUTION_FAILED` | 执行失败 | 是 |
| 无 Job 或 Job `FAILED` | `FAILED` | `SUBMISSION_FAILED` | 提交失败 | 是，输入仍完整时 |
| `CANCELLED` | `FAILED` | `CANCELLED` | 已取消 | 否，重新提交新任务 |

Retry 校验 Task 原因和输入 artifact：`EXECUTION_FAILED` 还要求现有 Job 为 `FAILED`；可恢复的
`SUBMISSION_FAILED` 在左右输入仍完整时允许创建新 Job，即使上次提交没有成功落盘 Job。Task 虽投影
为 `FAILED`，但取消任务不能通过 Retry 接口恢复。

Job 内部状态继续表达队列执行细节：

```text
QUEUED ------claim------> RUNNING ------success------> SUCCEEDED
   |                         |
 cancel                      cancel request
   v                         v
CANCELLED              CANCEL_REQUESTED
                              |
                      cancellation checkpoint
                              v
                          CANCELLED
```

终态只能写入一次。`CANCELLED`、`SUCCEEDED`、`FAILED` 之间不能互相覆盖；冲突转换作为显式
`TaskTransitionConflict` 处理并记录。

<a id="cancellation-protocol"></a>

#### 5.2.2 取消协议

- 队列中的 Job 在 `ExecutionStateCoordinator` 内原子转换为 `CANCELLED`。
- 运行中的 Job 转换为 `CANCEL_REQUESTED`。
- Runner 为每次 claim 创建 `TaskExecutionContext(job_id, task_id, worker_id, cancellation_token)`；
  Handler 签名从只接收 payload 改为同时接收该上下文。
- Application Handler 将 `cancellation_token` 依次传给 `CompareService.compare()` 和
  `PipelineContext`，不使用模块全局变量或仅存在于 HTTP 请求内的标志。
- `CancellationToken` 是 Repository-backed Token。`raise_if_cancelled()` 每次从 Job Repository
  读取最新持久化状态；`CANCEL_REQUESTED/CANCELLED` 抛出 `TaskCancelled`，owner 不匹配或 lease
  已失效时抛出 `TaskStaleLeaseError`。
- `ComparePipeline.run()` 在每个 stage 开始前、`stage.execute()` 返回后和终态提交前检查 Token；
  stage 内部的进度回调也执行检查，使有细粒度进度的长阶段可以更早退出。
- 检查点抛出 `TaskCancelled`，由统一终态处理器保存 Job 和 Task 的取消终态。
- 远程 OCR 请求开始后不保证立即中断 TCP 请求，但响应返回后不能继续下游阶段或写入成功。
- `finally` 或通用成功路径不得覆盖已经持久化的取消终态。

Job Repository 提供受状态前置条件约束的终态方法：

- `mark_succeeded` 只接受 owner 和 lease 均有效的 `RUNNING`。
- `mark_failed` 只接受 owner 和 lease 均有效的 `RUNNING`。
- `mark_cancelled` 接受 `CANCEL_REQUESTED`，并对已经 `CANCELLED` 的重复调用保持幂等。
- 两个 mark 方法看到 `CANCEL_REQUESTED/CANCELLED` 时不能覆盖，取消优先；看到 owner 不匹配或
  lease 过期时抛出 `TaskStaleLeaseError`，且不修改任何状态。
- 其他非法转换抛出 `TaskTransitionConflict`。

Runner 单独捕获 `TaskCancelled` 并调用 `mark_cancelled`；捕获 `TaskStaleLeaseError` 时只记录并停止
当前旧执行，不写 Task/Job；普通异常进入 `mark_failed`。若普通异常与取消请求竞态，Repository 的
前置条件保证取消获胜并转入 `CANCELLED`。

#### 5.2.3 创建与重试顺序

创建流程：

```text
stream to staging
  -> validate both inputs
  -> publish artifacts
  -> save PROCESSING task
  -> create visible job
```

Job 创建失败时执行补偿，清理 staging 和临时 Job；已经归属到成功保存 Task 的两份已验证输入予以
保留，使 `SUBMISSION_FAILED` 可以重试。Task 保存失败时输入尚无有效 owner，必须清理。重试必须先
验证输入文件、重置 Task 和 revision，再创建对 Worker 可见的新 Job；入队失败同样补偿。

#### 5.2.4 终态事件顺序

取消请求、进度写入、Job claim 和执行终态共用单个进程级
`ExecutionStateCoordinator(threading.RLock)`。当前受支持的 Worker 是同一 API 进程内的线程；使用单个
可重入锁可以避免 Queue 全局锁与 task_id 分片锁之间出现相反加锁顺序。锁只保护短暂的状态检查和
文件元数据写入，OCR、比较和报告生成不持有该锁。谁先进入临界区并通过状态前置条件，谁确定该任务
终态：取消先进入则成功路径不得覆盖；成功先提交则后续取消返回既有成功终态。

成功终态严格采用：

```text
acquire ExecutionStateCoordinator
  -> token/owner/lease check
  -> persist terminal task and revision
  -> persist terminal job
  -> publish terminal SSE event containing revision
  -> release coordinator
```

失败和取消使用相同协调器及顺序。`ComparePipeline` 只返回计算结果，不自行发布终态事件；Application
终态协调器是 Task、Job 和 SSE 终态的唯一写入入口。这样可以消除当前 Pipeline 先发布 COMPLETED、
随后才保存 Task，以及 Runner 无条件 `mark_succeeded` 的两处覆盖窗口。

前端收到终态事件后获取任务详情，只有详情已是终态且 `task.revision >= event.revision` 时才停止同步。

前端同步状态机：

```text
INITIAL_LOAD -> SSE_ACTIVE
                  |
       error / EOF / first-event timeout
                  v
          POLLING_WITH_BACKOFF
                  |
          confirmed terminal state
                  v
               STOPPED
```

组件卸载时关闭 EventSource、AbortController 和定时器。服务端每个订阅者使用有界的 latest-only
队列，慢客户端不能无限积压进度事件。

<a id="diff-deduplication"></a>

#### 5.2.5 Diff 去重规则

去重分两层：

1. `diff_id` 相同：视为同一语义差异，合并证据、质量标记、来源和审核投影。
2. `diff_id` 不同：只有同时满足以下条件才允许合并：
   - 来源、章节类型和差异类型相同；
   - 条款 ID 相同，或具有相同的稳定 section path；
   - original/compare 文本相同；
   - 至少存在一组同侧证据，且证据位于相同页、bounding box 覆盖重叠率不低于 `0.80`。

不同页码、不同条款、不同稳定路径的相同文本必须保留为独立差异。合并时保留双方全部去重后的证据，
不能只保留第一条证据。

覆盖重叠率定义为 `intersection_area / min(left_area, right_area)`，优先使用 `BBox.normalized`，没有
normalized 坐标时使用同页原始坐标。若两个 Diff 的 original 和 compare 两侧都存在可定位证据，
则两侧均必须达到 `0.80`；只有一侧有证据的 ADD/DELETE 使用存在的一侧。不同 diff_id 且双方均没有
可定位证据时禁止基于文本合并。阈值作为具名常量 `FINAL_DIFF_EVIDENCE_OVERLAP_THRESHOLD = 0.80`，
边界值 `0.80` 视为可合并。

不同 diff_id 合并时，canonical `diff_id` 固定取候选集合中字典序最小的非空 ID，不能依赖输入列表
顺序。所有其他 ID 写入 `dedupe_remap` 并指向 canonical ID；审核投影、OCR 质量引用和 AuditItem ID
统一使用该映射。相同 diff_id 的合并继续保留原 ID。

<a id="audit-item-review-model"></a>

#### 5.2.6 AuditItem 作为唯一审核单元

- 有类型证据的 Diff 按业务语义生成 `ADD`、`DELETE` 或 `MODIFY` AuditItem。
- 没有类型化证据时仍生成 fallback AuditItem。
- 没有可定位坐标时设置 `evidence_state=UNLOCATED`、质量状态 `NEEDS_REVIEW`，并增加
  `EVIDENCE_UNLOCATED` 标记。
- `audit_item_reviews` 是唯一审核事实源。
- AuditItem 审核接口只更新目标 AuditItem。
- 旧的 Diff 级审核接口作为兼容入口，对该 Diff 下全部 AuditItem 执行相同审核动作。
- Diff 上的审核字段只作为 AuditItem 审核的投影，不再独立写入。

Diff 投影规则：全部 AuditItem 同一审核状态时投影该状态；存在混合状态时投影 `NEEDS_REVIEW`；
全部未审核时投影 `UNREVIEWED`。

统计始终按 AuditItem 计算，不再根据“是否存在 AuditItem 审核记录”切换口径。API 响应增加
`review_unit="audit_item"`，让前端和后续调用者能够解释计数。

前端每张 AuditItem 卡片直接展示该 item 独立的审核状态徽标：`未审核`、`已确认`、`误报`、
`需复核` 或 `已忽略`。操作只更新当前 `audit_item_id`，同一 Diff 下的其他卡片不跟随改变；服务端
响应中的审核统计覆盖本地统计。前端不再以 Diff 投影状态作为卡片状态，Diff 级接口仅保留给旧客户端
做批量兼容。

#### 5.2.7 报告与 API

报告遍历所有 AuditItem，并展示：来源、章节、差异文本、证据位置或“未定位”、质量标记、置信度、
OCR 信息、修复建议和审核状态。

Diff DTO 以向后兼容的可选字段补充：

- `section_type`
- `section_path`
- `match_confidence`
- `structural_flags`

后端向前端提供归一化 AuditItem 列表，前端不再自行重新解释 Diff 证据并生成第二套审核项。

### 5.3 第三批：提交链路与报告可靠性

<a id="upload-compensation"></a>

#### 5.3.1 流式上传和补偿

上传按块写入 staging，在写入过程中累计大小并执行上限检查。超限时立即停止并清理临时文件，
不先将整个上传读入内存。左右合同全部校验通过后，才发布到任务 artifact 目录。

文件系统无法提供跨多个文件、Task 和 Job 的真正事务，因此使用可测试的 Saga 补偿：

| 失败点 | 补偿行为 |
| --- | --- |
| staging 写入或校验失败 | 删除本次 staging 文件 |
| artifact 发布失败 | 删除本次已经发布的 artifact |
| Task 保存失败 | 删除本次 artifact |
| Job 创建失败 | Task 标记提交失败；保留其已验证输入，清理 staging 和临时 Job |
| Retry 入队失败 | 恢复为可解释的失败状态，不保留处理中假象 |

补偿只处理本次请求拥有的文件，绝不递归删除未知目录或历史任务数据。

补偿动作必须幂等，文件已经不存在视为成功。补偿失败不能覆盖最初的提交错误：API 返回最初错误，
同时把补偿错误追加到结构化日志。如果 Task 已创建，则保存 `terminal_reason=SUBMISSION_FAILED` 和
“补偿未完整完成”的错误摘要。无论 Task 是否已经成功保存，都在 `storage/recovery/{task_id}.json`
写入 marker，记录仅属于本次请求的待清理路径、失败动作和重试次数。启动恢复流程和幂等维护脚本
重试这些动作，全部成功后删除 recovery marker。
如果 marker 本身无法写入则记录 CRITICAL 日志，但仍不得尝试扩大删除范围。

<a id="revisioned-reports"></a>

#### 5.3.2 Revision 化报告

- 通用 `task.revision` 用于 SSE 和并发可见性，每次 Task 持久化都会增长；另增
  `report_revision`，只在比较结果首次完成或 AuditItem 审核发生变化时增长。进度更新、报告路径回写
  和缓存命中不增长 `report_revision`，避免下载报告本身使缓存立即失效。
- 报告文件名包含输入和审核状态对应的 report revision，例如
  `reports/contract_compare_report-r12.pdf`。
- 先写同目录唯一临时文件，完成并 flush 后原子 replace 到正式路径。
- manifest 只在正式文件发布成功后更新。
- `ReportLockRegistry` 按 `(task_id, report_revision)` 提供进程内互斥锁。请求取得锁后必须再次检查
  正式文件及 manifest；已存在且校验有效时直接复用，否则只有持锁请求执行生成。Registry 对锁做
  引用计数，最后一个等待者释放后删除该 key，避免长期运行时无限积累历史 revision 锁。
- 生成失败保留既有正式文件，清理本次唯一临时文件并释放锁；等待者获得锁后可以重试。
- 相同 report revision 的并发请求只生成一次；审核变更提升 report revision，不同 revision 使用
  不同锁和文件，互不覆盖。

#### 5.3.3 质量工作台和前端请求

- 新增可配置 `QUALITY_CASES_DIR`，生产默认位置位于 storage 下。
- 运行时代码不再从 `backend/tests/fixtures` 加载质量案例。
- 测试通过依赖注入使用临时目录；测试 fixture 仍可作为测试数据存在。
- Docker 镜像无需复制 tests 目录即可运行应用和质量工作台。
- 前端普通 HTTP 请求统一支持超时、AbortSignal 和卸载取消。
- OIDC 前端测试显式清理或模拟环境变量，避免本地 `.env` 改变测试结果。

### 5.4 第四批：单机部署一致性

<a id="atomic-storage"></a>

#### 5.4.1 文件存储原子性

Task、Job、artifact 和 report manifest 通过统一 Repository/Store 入口更新。JSON 写入统一采用：

1. 在目标目录创建唯一临时文件；
2. 写入、flush 并 fsync；
3. 使用原子 replace 发布；
4. 必要时 fsync 目录。

以上逻辑提取为 Infrastructure 公共原语，例如 `atomic_write_text`、`atomic_write_json`、
`atomic_publish_file`，统一处理同目录唯一临时文件、flush/fsync、replace 和异常清理。Task Repository、
Job Repository、Artifact Store、质量案例和 manifest 不再各自维护 `.tmp` 写法；公共原语本身不更新
manifest，避免递归副作用。

启动时检查 storage 目录可写性、关键 manifest 可解析性，并清理可确认未被引用的陈旧临时文件。
Task 终态同时记录 `terminal_job_id` 和 `terminal_attempt`。如果进程在“Task 终态已保存、Job 终态
未保存”之间退出，启动 reconciliation 在取得 Coordinator 后用这两个字段校验对应 Job：匹配且 lease
已过期时补齐 Job 终态；不匹配时记录冲突并保持不变，禁止盲目把新的 attempt 标记为旧终态。

<a id="queue-locking"></a>

#### 5.4.2 队列互斥和原子领取

当前 `QueuedTaskRunner` 的 Worker 是同一 API 进程内的 `threading.Thread`，正式支持模型也限定为
单 API 进程，因此使用 `ExecutionStateCoordinator` 持有的共享 `threading.RLock`，不使用
`fcntl/flock` 冒充多进程支持。Queue、Task 终态协调和 manifest 元数据更新必须使用同一个
Coordinator，不能由各 Repository 各自创建互不相干的 `RLock`。

Job 列表读取、领取、取消、重试和状态写回在相应锁临界区内完成：

```text
read eligible job -> verify state -> mark RUNNING/owner -> atomic write -> unlock
```

Job 保存 owner、claimed_at、lease_expires_at 和 attempt。lease 到期后的新 Worker 可以在锁内 claim；
旧 Worker 下一次 Token 检查或终态 mark 时得到 `TaskStaleLeaseError`，不得覆盖新 owner 的状态。
如果未来把 Worker 拆成独立进程，必须替换 Queue Adapter 为真正的跨进程协调实现；这不属于当前
文件队列承诺。

#### 5.4.3 记录摘要索引

记录列表维护不含完整 Diff 的轻量摘要索引，用于排序、过滤和分页。任务主文件仍是事实源：

- 主 Task 保存成功后再增量更新索引。
- 索引更新失败不回滚已经成功的主任务写入，但记录错误并允许后续重建。
- 提供幂等重建脚本，从 Task 文件恢复摘要索引。
- API 分页只读取索引命中的 Task，避免为一页记录加载全部历史 Diff。

#### 5.4.4 部署约束

本轮正式支持“单节点、单 API 进程、一个或多个受共享 RLock 保护的 Worker 线程”。SSE ProgressBus 仍是
进程内组件，因此配置多个 API 进程时启动检查必须明确拒绝或报错，不能静默进入部分实时事件丢失的
状态。前端轮询降级是网络容错机制，不作为多 API 进程支持方案。

未来如需多节点部署，应在既有 Adapter 边界后替换为共享数据库、对象存储和跨进程事件总线；
不在本次四批次中实现。

## 6. 兼容与迁移策略

- `/api/compare/*` 路径和既有必需字段保持兼容，新字段均为附加字段。
- 不再提供 `/api/extract/*`；相关删除属于本次明确批准的产品 API 迁移。
- 历史 Task 缺少 `terminal_reason` 时按兼容规则读取：非 `FAILED` 为 `NONE`；`FAILED` 且 stage/错误
  明确为既有取消文案时为 `CANCELLED`；其他 `FAILED` 为 `EXECUTION_FAILED`。在下次正常保存时落盘。
- 历史 Task 缺少 `report_revision` 时，以“已有完成结果”为 1、其他状态为 0 初始化；首次审核变更后
  按新规则递增。
- 历史 extraction Task/Job 在扫描时按未知或退役类型跳过，不删除原文件。
- 历史 Diff 只有 Diff 级审核状态时，读取层生成 AuditItem 初始审核投影；发生下一次审核写入后，
  持久化为 canonical `audit_item_reviews`。
- 旧固定路径报告可以继续作为历史 artifact 读取；再次生成时使用 revision 化路径。
- 索引是可重建派生数据，不作为 Task 或审核事实源。

<a id="error-observability"></a>

## 7. 错误处理与可观测性

应用层使用明确异常区分可预期控制流：

- `TaskCancelled`：正常取消控制流，不记录为执行异常。
- `TaskTransitionConflict`：并发状态冲突，API 映射为 409 或由 Worker 记录后停止覆盖。
- `TaskStaleLeaseError`：当前 Worker 已失去 lease 或 owner 身份；Worker 仅记录 warning 并立即停止，
  不再修改 Task、Job 或发布终态事件。它是 `TaskExecutionError` 的专门类型，API 边界映射为 409，
  但正常情况下只在 Worker 内部消费。
- `TaskSubmissionError`：Task/Job 提交链失败，触发补偿并返回可解释错误。
- artifact/report 发布错误：保留原有已发布版本，临时文件进入安全清理流程。

每次 Job 日志至少包含 task_id、job_id、attempt、owner、状态转换和 revision；补偿失败、索引更新
失败和 manifest 解析失败必须有结构化错误记录。日志不记录合同全文、OCR 全文或敏感字段值。

<a id="structured-logging"></a>

### 7.1 结构化日志

日志采用单行 JSON。所有状态事件使用以下公共字段；不适用的字段写 `null`，不得用不同字段名表达
同一概念：

| 字段 | 含义 |
| --- | --- |
| `timestamp` | UTC ISO-8601 时间 |
| `level` | `INFO`、`WARNING`、`ERROR` 或 `CRITICAL` |
| `event` | 稳定事件名，不使用自然语言句子 |
| `request_id` | HTTP 请求关联 ID；后台恢复没有请求时为 null |
| `task_id`、`job_id` | 任务与执行记录标识 |
| `attempt`、`worker_id` | 执行轮次与 lease owner |
| `from_status`、`to_status` | 本次状态转换；非转换事件为 null |
| `terminal_reason` | Task 具体终态原因 |
| `task_revision`、`report_revision` | 可见性 revision 与报告业务 revision |
| `error_type`、`error_code` | 异常类名与稳定机器码；无错误时为 null |
| `recovery_marker` | 补偿恢复 marker 的 storage 相对路径 |
| `duration_ms` | 本次操作耗时 |

稳定事件名至少包括：`job_claimed`、`cancellation_requested`、`task_terminal_committed`、
`stale_lease_detected`、`compensation_failed`、`report_published`、`report_generation_failed`、
`manifest_recovery_failed` 和 `record_index_rebuilt`。

取消终态示例：

```json
{"timestamp":"2026-07-16T08:15:30.125Z","level":"INFO","event":"task_terminal_committed","request_id":null,"task_id":"task-7f2a","job_id":"compare:task-7f2a","attempt":1,"worker_id":"worker-a-0","from_status":"CANCEL_REQUESTED","to_status":"CANCELLED","terminal_reason":"CANCELLED","task_revision":18,"report_revision":0,"error_type":null,"error_code":null,"recovery_marker":null,"duration_ms":7}
```

补偿失败示例：

```json
{"timestamp":"2026-07-16T08:16:02.410Z","level":"ERROR","event":"compensation_failed","request_id":"req-91bd","task_id":"task-902c","job_id":null,"attempt":0,"worker_id":null,"from_status":"PROCESSING","to_status":"FAILED","terminal_reason":"SUBMISSION_FAILED","task_revision":3,"report_revision":0,"error_type":"PermissionError","error_code":"ARTIFACT_CLEANUP_FAILED","recovery_marker":"recovery/task-902c.json","duration_ms":12}
```

路径只记录 storage 相对路径。错误消息必须经过脱敏和长度限制；合同文件名、合同文本、OCR 文本、
用户姓名和 Token 不进入结构化字段。Python traceback 由日志后端单独保存并受生产日志权限控制，不能
拼入公开 API 错误响应。

<a id="test-strategy"></a>

## 8. 测试策略

所有行为改造遵循测试驱动：先加入能稳定复现当前缺陷的失败测试，再做最小实现并运行回归。

### 8.1 第一批

- OpenAPI 和路由范围测试。
- 历史 extraction 文件不会破坏 compare Task/Job 列表的兼容测试。
- 全仓库静态搜索，确认无字段提取产品类型和展示入口。

### 8.2 第二批

- 排队中、stage 前、stage 执行中、stage 返回后以及终态提交前取消测试，断言取消后永远不会写入成功。
- 使用 Barrier 精确覆盖“取消与成功 mark”“取消与失败 mark”的竞态，断言先取得 Coordinator 的
  合法转换胜出，终态不会被后写覆盖。
- owner 不匹配和 lease 过期测试，断言抛出 `TaskStaleLeaseError` 且 Task/Job 保持不变。
- 取消任务 Retry 被拒绝、执行失败 Retry 可用的 Task/Job 映射测试。
- 终态持久化和 SSE 发布顺序测试。
- 前端 SSE error、EOF、首事件超时、轮询网络错误和组件卸载测试。
- 跨页、跨条款相同文本的 Diff 去重回归测试；覆盖率 `0.79` 不合并、`0.80` 合并、无定位证据
  不合并，以及输入顺序变化仍选择相同 canonical diff_id。
- 无证据和无坐标 Diff 的 AuditItem/报告完整性测试。
- AuditItem 单项审核、旧 Diff 批量审核和统计投影测试；前端断言同一 Diff 下各卡片独立显示审核
  状态徽标，更新一个 item 不改变兄弟 item。

### 8.3 第三批

- 上传超限、第二文件失败、artifact 发布失败和入队失败的故障注入测试。
- Retry 在 Worker 领取竞争下的顺序测试。
- 同 report revision 并发请求只调用一次 Generator、审核后新 revision、不同 revision 互不阻塞、
  生成失败保留旧报告和临时文件清理测试。
- 补偿动作失败时保留主错误、写入 recovery marker，以及启动恢复重试成功后删除 marker 的测试。
- 无 tests 目录的生产质量目录测试。
- 隔离 OIDC 环境变量的前端测试。

### 8.4 第四批

- 同进程多 Worker 线程并发 claim，断言一个 Job 只有一个 owner；lease 转移后旧 Worker 不能写终态。
- 公共原子写入在写入、fsync、replace 各故障点的清理测试，以及损坏 manifest 恢复测试。
- 模拟 Task 终态保存后、Job 终态保存前退出，断言启动 reconciliation 只补齐匹配 job_id/attempt
  的 Job，不覆盖新 attempt。
- 摘要索引更新失败、重建和分页不加载完整 Diff 的测试。
- 不受支持的多 API 进程配置启动失败测试。

每批完成后执行相关测试和完整门禁：

```bash
cd backend && python -m compileall app tests
cd backend && python -m ruff check .
cd backend && python -m pytest
cd frontend && npm test
cd frontend && npm run build
```

## 9. 交付与 Review 流程

四批按顺序连续实施，每批形成可独立验证的稳定状态：

1. 产品范围收敛。
2. 任务、差异和审核正确性。
3. 提交链路与报告可靠性。
4. 单机部署一致性。

每批采用 Red-Green-Refactor，完成后运行该批相关测试与完整回归，并启用独立 SubAgent Review
对应流程代码。阻断级 Review 问题修复并复验后才进入下一批。现有 OCR、匹配和报告 golden tests
作为行为保护网，不因重构随意修改期望值。

## 10. 完成标准

- 字段提取产品逻辑、接口描述和前端入口全部移除，合同对比解析/OCR 能力正常。
- 取消、失败、重试和成功状态不存在互相覆盖，前端最终状态可收敛。
- Diff 不会因缺少位置维度而误合并，每个 Diff 均进入 AuditItem 和报告。
- AuditItem 是审核、统计和报告的唯一业务口径，历史审核可以兼容迁移。
- 上传和入队失败不会留下不可解释的孤儿任务或永久处理中状态。
- 补偿不完整时存在可重试的 recovery marker，不会静默遗留未知文件。
- 报告与 report revision 对齐，同 revision 只生成一次且不会损坏或覆盖其他 revision。
- 文件队列在单进程多 Worker 线程下不会重复领取任务，旧 lease owner 不能覆盖新 owner，记录分页
  不再加载全部历史差异。
- 后端编译、lint、完整测试以及前端测试和生产构建全部通过。
