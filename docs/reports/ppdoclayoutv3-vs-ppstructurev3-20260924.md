# PP-DocLayoutV3 与 PP-StructureV3 服务性能对照

2026-09-24 实测：新 GPU Layout-only 服务的单页 HTTP 耗时约为现有 PP-StructureV3 默认服务的 1/5.2–1/6.3，降低约 81%–84%。这是不同功能范围的服务调用比较，不是两个 Layout 模型独立推理速度比较，也不是合同对比端到端提速。

| 页面 | Layout-only | PP-StructureV3 默认 | PP-StructureV3 关闭可选模块 | 默认服务耗时 / Layout-only |
|---|---:|---:|---:|---:|
| 正文页 | 0.185 s | 1.166 s | 0.535 s | 6.30× |
| 表格页 | 0.159 s | 0.971 s | 0.316 s | 6.10× |
| 扫描页 | 0.145 s | 0.749 s | 0.345 s | 5.18× |

三个样本均来自既有 Full Golden 的 144 DPI PNG：HUANENG_DINGBIAN 原文第 13 页、RETURN_CONFIRMATION 原文第 1 页、HUANENG_DINGBIAN 对比文第 1 页。每个页面/配置预热 1 次，之后交替调用 5 轮，共 45 次计分请求，全部成功并返回非空结果。表格数值为 5 次中位数；单并发，含网络传输、完整响应下载和 JSON 解码，不含 Base64 请求构造。样本路径、SHA-256、请求选项、原始结果、逐次耗时均已保存。

PP-StructureV3 默认仅指定关闭可视化和 Markdown 图片回传，其返回 model_settings 确认启用预处理、表格、印章、公式、图表及 region detection。关闭可选模块组通过请求关闭上述模块及文字行方向分类，但响应仍含 overall_ocr_res 和文字识别结果。当前 OpenAPI 没有关闭 General OCR 的参数，因此此组不叫 Layout-only。各组保留各自默认模型阈值/后处理，新服务 threshold=0.5、layoutNms=false；输出区域数量也存在差异。

新服务仅输出版面类别、坐标、多边形及阅读顺序。原生 PDF 可另取 native text；扫描页还需要 OCR；需要结构化表格时也必须另计表格识别。因此不能把这个倍数直接外推到完整合同对比，也没有证明业务结果等价。共享服务器没有隔离其他任务，本次是小样本延迟测量，不报告吞吐或 P95。

冻结 A–E 原矩阵现已完成 30/30，但没有接入此 GPU 服务；本次结果独立保存，不改写原矩阵。READY_FOR_PRODUCTION=false、544 个 DEFERRED_BUSINESS_REVIEW 及 Golden expectation 均未更改。

复现脚本：`archive/experiments/2026-09-24/layout_service_comparison/run.py`。证据目录：`output/analysis/ppdoclayoutv3-vs-ppstructurev3-20260924/`。脚本拒绝覆盖既有测量；重跑应使用独立输出目录。
