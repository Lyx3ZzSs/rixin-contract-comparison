# PP-StructureV3 与 PP-DocLayoutV3：当前部署的 batch / 并行核验

**结论：PP-StructureV3 已配置请求内 batch=4，但跨请求产线调用串行；PP-DocLayoutV3 独立服务是单图、batch=1，跨请求模型推理也串行。两个服务均能接收重叠的 HTTP 请求，不能把 HTTP 在途并发解释为 GPU 模型并行。**

2026-09-24 登录 `10.10.12.64` 核验实际运行容器、挂载配置、容器内 PaddleX 源码及进程环境，并用 D 扫描合同前 4 页实测。未将参考目录里的旧 Compose 当作实际部署。

## 部署结果

现有 PP-DocLayoutV3 已于 2026-09-23 部署完成，本次确认完整且健康，复用现有服务，未重复创建容器或重启服务。

- 目录：`/home/sprixin/ppocr_docker/ppdoclayoutv3`。
- 容器：`ppdoclayoutv3-service`；模型 `PP-DocLayoutV3`；GPU `gpu:0`，RTX 4090。
- API：[8090/docs](http://10.10.12.64:8090/docs)，`POST /layout-detection`。
- 健康检查：[8090/health](http://10.10.12.64:8090/health)。
- 运行镜像与现有 Structure 使用同一来源镜像；DocLayout Compose 固定 image ID。
- 远端 `server.py` 与本地部署代码 SHA-256 相同：`ae6c6f946f4e55d7215a1d37284bf69ca8187bbbeafce6d10d9788c41b5e41ed`。

## 实际服务差异

| 项目 | PP-StructureV3 :8089 | PP-DocLayoutV3 :8090 |
| --- | --- | --- |
| 实际运行容器 | ppstructurev3-centos7 | ppdoclayoutv3-service |
| 服务方式 | paddlex --serve | 自定义 FastAPI + Uvicorn |
| Layout 模型 | PP-DocLayout_plus-L | PP-DocLayoutV3 |
| 输入 | 单个 PDF / 单张图 | 单张图，必须 fileType=1 |
| 多文件/多图数组 API | 无 | 无 |
| 请求内页面 batch | pipeline=4，LayoutDetection=4 | predict(image, batch_size=1) |
| 跨 HTTP 请求动态合批 | 未实现，按队列逐个调用产线 | 未实现 |
| 模型/产线调用并行 | 单工作线程 Queue 串行 | 全局 threading.Lock 串行 |
| 排队方式 | 服务 wrapper 将整次 pipeline 调用排队 | 模型锁等待，超过 30s 返回 503 |
| 额外工作 | OCR、预处理、表格、印章、公式等 | Layout only |

**Structure 实际配置来源**为 `/home/sprixin/bak/ppocrv6-buildkit-centos7/conf/PP-StructureV3.yaml`，只读挂载到容器 `/app/PP-StructureV3.yaml`；不是参考目录 `/home/sprixin/ppocr_docker/ppstructurev3/docker-compose.yml` 所代表的旧部署。启动命令为 `paddlex --serve --pipeline /app/PP-StructureV3.yaml --device gpu --host 0.0.0.0 --port 8080`。

容器中 `PP-StructureV3` 注册到 `LayoutParsingPipelineV2`：`ImageBatchSampler(batch_size=config.get("batch_size",1))` 将输入页面组批，批内页面列表交给 Layout predictor；顶层和 LayoutDetection 实际配置都是 4。子模块各有自己的 batch 配置，不代表 OCR/表格/整个产线全部算子始终以四页张量同时运行。

`PipelineWrapper` 读取 `PADDLE_PDX_SERVING_SERIAL_PIPELINE_CALLS`，默认 True；实际服务进程未设置该变量，因此走单工作线程队列，将一次 `pipeline.predict(...)` 完整执行后再处理下一个调用。多页 PDF 的请求内 batching 和多请求并行是两个维度。

## 实测

只使用 D compare 的前 4 张扫描页。每组 warmup 1、正式 3 次；两个端点的实验顺序执行，未与 D 整份 pipeline 测速重叠。Structure 使用相同的单页 PDF 或四页合并 PDF；DocLayout 使用相同页面的 144 DPI PNG。各端点保留原模型/配置，Structure 只关闭可视化与 Markdown 图片返回。

| 请求方式 | 4页总 wall P50 s | 请求 latency P50 / max s | HTTP errors | 正式输出 hash unique |
| --- | ---: | ---: | ---: | ---: |
| DocLayout：4 PNG，concurrency=1 | 0.849 | 0.212 / 0.228 | 0 | 1 |
| Structure：4 单页 PDF，concurrency=1 | 6.068 | 1.583 / 2.164 | 0 | 1 |
| DocLayout：4 PNG，concurrency=2 | 0.568 | 0.238 / 0.339 | 0 | 1 |
| Structure：4 单页 PDF，concurrency=2 | 5.558 | 2.698 / 3.472 | 0 | 1 |
| DocLayout：4 PNG，concurrency=4 | 0.584 | 0.407 / 0.600 | 0 | 1 |
| Structure：4 单页 PDF，concurrency=4 | 5.690 | 3.579 / 5.712 | 0 | 1 |
| Structure：1 四页 PDF，请求内 batch=4 | 5.592 | 5.588 / 5.610 | 0 | 1 |

Structure 四页 PDF 相对四个串行单页 PDF 约快 7.8%；HTTP 并发 2 约快 8.4%，并发 4 约快 6.2%。单请求延迟随排队上升，与服务串行调用机制一致。DocLayout 并发 2 约快 33.1%，并发 4 约快 31.2%，这组仅四页，不能替代 D 全 80 页实验对并发的选择。

这些数值验证的是当前端点的调度表现。PDF 服务包含解析/渲染，PNG 服务不含客户端预渲染；两个 Layout 模型和功能范围也不同，不能把两列时长之比当作同一模型的 batch 加速比。请求内 batch=4 的判定来自实际配置和实际服务代码路径，本轮未插入 GPU kernel profiler 或修改模型来观测 forward 张量。

## 可靠性与证据

- 每组正式输出 hash 唯一值为 1；均返回全部 4 页，HTTP errors=0，无客户端重试。
- 1 秒间隔 GPU 采样 137 次，整卡显存均为 22,520 MiB / 24,564 MiB；这是共享 GPU 总量，不是单模型显存。
- 测试前后 Structure、DocLayout 和 OCR 容器的 StartedAt、RestartCount、OOMKilled 完全相同：RestartCount=0、OOMKilled=false。
- 未改服务端 API、batch 参数、模型、阈值或现有业务流程。
- 证据目录：`output/analysis/layout-server-batch-parallel-20260924/`，含 active config、服务源码、注册路径、串行开关/进程环境、请求测量、GPU 采样和容器状态。凭证未写入报告或实验脚本。
- 测试入口：[run.py](../../archive/experiments/2026-09-24/layout_server_batch_parallel/run.py)。

当前 PP-DocLayoutV3 的部署和验证已完成。若将来给独立 DocLayout 增加真正的多图 batch，需要单独改变请求协议与服务调度；本轮没有实施这一后续改造。
