# TextIn 对齐首批实现范围

版本：`v3-pdf-v1` / `textin-events-v1`，2026-09-09。

本批实现 PDF 单对合同的纵向闭环。**这是显式限定范围的适配入口，不是 TextIn 全协议替代品。** 生产比对不调用 TextIn。现有 `/api/compare`、原审计报告和历史审核保留；事件视图从 `?view=events` 进入。

| 能力 | 当前行为 | 与 TextIn 的差距 |
|---|---|---|
| 创建 | POST `/api/contracts/v3/comparison/external/create`，JSON，standard_doc/compare_doc 各一份 base64 PDF | 多文件、Word、图片转换暂不支持，请求返回 400 |
| 选项（2026-09-14 更新） | merge_diff 为 0/1；use_pdf_parser 支持 true/1/"true"，remove_watermark 支持 false/0/"false"；统一完整比对印章、签署区域及页眉页脚 | 已移除 remove_stamp、remove_headerfooter，传入任何值均返回 400；remove_comments/remove_symbol 非零、ignored_symbols 非空、水印忽略及未知选项返回 400 |
| 认证 | 本服务 Bearer 身份及原有任务权限 | 不接受 TextIn 的 x-ti 密钥；同形本地应用密钥认证未实现 |
| 预览 | token_mode=0，预览链接进入现有登录保护的事件页 | token_mode=1 返回 400；token/apply 返回 501，尚无令牌兼容 |
| 预览地址 | 默认请求 origin，可设 TEXTIN_PREVIEW_BASE_URL 为前端地址，含 `/contract` 等前缀 | 前后端分开部署需配置该地址 |
| 查询 | GET diff_info?task_id=…&with_diff_result=true；code/msg/data，result 数组；状态 1/2/-1 | 错误码采用本服务 HTTP 语义，未承诺 TextIn 私有错误码逐位相同 |
| 事件 | INSERT/DELETE/CHANGE；text/stamp/table；element_id 稳定；表格包装为嵌套 text diff | 目前未输出 MATCH；未知的 cell/row/col 信息不伪造，细粒度表格兼容待补齐 |
| 统计 | total 等于实际 diff_result 项数 | 保存 TextIn 样本 total=24 含一个 MATCH，本地不人为补造 MATCH；有 23 项变化也不意味着 23 项真实变化 |
| 坐标 | page_index 从 0 开始；左上角、旋转后显示页 PDF point，polygon 为四角；pages 尺寸使用同一单位 | TextIn 样本为栅格像素；本批 page type=pdf，未复制其 png/page_id 图像资源协议 |
| 字符框 | char_polygons=[] 表示未提供，使用证据 polygon 定位 | 尚无逐字符几何转换，不用均分区域伪造字符框 |
| 相似度 | similarity=null；similarity 查询明确 501 | 没有公开同一算法，不能以常数或其他指标冒充 |
| 列表与删除 | 保留本地原有记录接口 | TextIn 管理接口尚未实现 |
| 页面 | 标准/比对双文档、右侧列表、类型与元素筛选、事件导航、明细展开、缩放、对应页联动、整组审核、事件 PDF 导出 | 原任务 TextIn 预览返回 task not found；已观察空态布局，但未验证长事件真实展开、高亮颜色和完整交互同形 |
| 历史迁移 | 缺少持久快照时保守输出独立事件；显式 rebuild CLI 输出新 JSON | 不静默重跑/覆盖历史任务；旧记录列表及原审计视图继续采用原统计 |
| 图形覆盖 | 保留此前已验证误报修复与独立印章 | 二维码样图形差异仍未单独实现，不将其伪装成表格来凑数 |

## 本地事件接口

- GET `/api/compare/{task_id}/events`：版本、revision、事件、成员、原始数、事件数、页映射及审核冲突。
- PUT `/api/compare/{task_id}/events/{event_id}/review`：`{"revision": 当前版本, "review_status": "CONFIRMED"}`；同一数据库更新中审核所有成员，过期 revision 返回 409。
- GET `/api/compare/{task_id}/events/export`：可追溯 JSON。
- GET `/api/compare/{task_id}/events/report`：同一事件口径的 PDF，不复用原审计报告缓存。

审核冲突保留为 NEEDS_REVIEW；折叠、展开、筛选和审核不改变事件身份。事件快照指纹不含审核状态，源差异变化则快照失效并保守退回独立事件。

## 验证与后续出口

首个样本基于相同 PDF 和缓存 OCR 执行完整创建、队列、比对、查询、PDF 读取、导出及审核闭环；这不等于验证新一次外部 OCR 服务质量。回放与上一轮原始 35 条完全相等，事件为 5 条，连续新增的 31 条合为一组，两个章独立。未知图形差距和已裁定 TextIn 误报都应保留在对照表中。

后续完整兼容必须补齐上表未实现项，获得有效 TextIn 预览任务并验证实际交互。当前不能标记整个五阶段方案全部完成，也不能据此替换要求 TextIn 全协议的客户端。
