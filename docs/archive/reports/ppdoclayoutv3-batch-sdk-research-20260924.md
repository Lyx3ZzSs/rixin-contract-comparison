# PP-DocLayoutV3 batch / 并行能力：SDK 与官方源码核查

核查日期：2026-09-24。范围：PaddleOCR 3.7.0、PaddleX 3.7.2，区分官方能力、当前 HTTP 封装与运行时验证。本文只作资料和源码核查，不改 GPU 服务。

**结论：PP-DocLayoutV3 的当前 Paddle SDK 支持真正多图片 batch；相同服务器模型和运行时的 CPU 隔离探针已通过 batch 1/2/4，当前部署的 HTTP 服务没有启用它。** `batch_size=1` 是当前封装选择，不是模型硬限制。单请求 batch、多个 HTTP 请求、多个模型实例并行是三种不同机制，不能互相代替。GPU 上 batch 2/4 的可用性、输出一致性和收益仍需独立实测。

| 层次 | 已确认事实 | 开启方式 / 限制 |
|---|---|---|
| PaddleX 3.7.2 SDK | 支持 list 输入和 `batch_size`；多张图片由 `ToBatch` 合并，再调用一次 runner | `predict(images, batch_size=2)`；不能只传一张图片却期待 batch 2 |
| 官方 PP-DocLayoutV3 静态图 | `image=[-1,3,800,800]`，`im_shape=[-1,2]`，`scale_factor=[-1,2]` | batch 维动态，空间尺寸固定；仍需验证具体 Paddle runtime 的算子执行 |
| 当前 HTTP Layout 服务 | 单张图片字段、单个常驻模型、全局 `threading.Lock()`、`batch_size=1` | 并发 HTTP 会在模型锁前排队，不能自动合批或并行运行 predictor |
| 多模型实例并行 | 可以以独立进程分别持有模型实例设计服务 | 每个进程独立初始化 predictor；显存和吞吐需实测，不直接删除共享模型的锁 |
| PP-StructureV3 产线 | 官方提供多 GPU 与多进程实例方案 | 产线支持 `device="gpu:0,1"`；不能直接把该能力套到单模型 `LayoutDetection` |
| Transformers | 官方明确 PP-DocLayoutV3 支持 batched inference | 另一条 backend / 权重格式路径，不是现有 Paddle 静态图的开关 |

## 1. Paddle 路径为何属于真正 batch

官方模块文档允许 list 输入，`batch_size` 为正整数，默认 1。[PaddleX 3.7 版面分析文档](https://paddlepaddle.github.io/PaddleX/3.7/module_usage/tutorials/ocr_modules/layout_analysis.html)

源码链路是 `ImageBatchSampler` 按指定 batch 大小组装图片 → `LayoutAnalysisRunnerPredictor.process()` → `ToBatch` → 一次 `self.runner(batch_inputs)` → 按图片拆分结果；不是 SDK 接受列表后必然逐页调用模型。PaddleOCR wrapper 将 `predict()` 参数透传给 PaddleX。本次检查这些路径没有发现 PP-DocLayoutV3 的 `batch_size == 1` 断言。[batch sampler](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/paddlex/inference/common/batch_sampler/image_batch_sampler.py)、[Layout predictor](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/paddlex/inference/models/layout_analysis/predictor.py)、[PaddleOCR wrapper](https://github.com/PaddlePaddle/PaddleOCR/blob/v3.7.0/paddleocr/_models/base.py)

`STATIC_SHAPE_MODEL_LIST` 包含 PP-DocLayoutV3，但对应断言限制的是用户修改 `img_size`。官方 `inference.json` 的输入 batch 维为 `-1`；`inference.yml` 固定 resize 到 800×800，并且 HPI 动态形状配置的 image batch 上限为 8。因而不能把 `use_dynamic_shape: false` 解读成 batch 只能为 1；也不能把 HPI 配置中的 8 当成所有 backend 的通用模型上限。[shape 列表](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/paddlex/inference/models/layout_analysis/utils.py)、[官方模型图](https://huggingface.co/PaddlePaddle/PP-DocLayoutV3/blob/main/inference.json)、[官方推理配置](https://huggingface.co/PaddlePaddle/PP-DocLayoutV3/blob/main/inference.yml)

与当前部署相同的 SDK 入口，最小开启写法如下。`page_paths` 必须是存在的图片路径，或改用已解码的 NumPy 图片列表。保持本地同一模型目录、阈值及 NMS 设置：

```python
from paddleocr import LayoutDetection

model = LayoutDetection(
    model_name="PP-DocLayoutV3",
    model_dir="/root/.paddlex/official_models/PP-DocLayoutV3",
    device="gpu:0",
    enable_hpi=False,
)
page_paths = ["/data/page_01.png", "/data/page_02.png"]
results = model.predict(
    page_paths,
    batch_size=2,
    threshold={6: 0.4, 17: 0.4},
    layout_nms=False,
)
assert len(results) == len(page_paths)
```

也可使用 `paddlex.create_model(model_name="PP-DocLayoutV3", device="gpu:0")` 后 `list(model.predict(page_paths, batch_size=2))`。以上是依据官方接口和源码给出的调用方式；本文不把它写成已通过 GPU 验证的部署配置。

同服务器权重、PaddleOCR 3.7.0 / PaddleX 3.7.2 / Paddle 3.1.1 的隔离 CPU 探针已实测 4 张 D 扫描页：

| batch_size | 实际 runner image shape / 调用次数 | 结果页数 | 每页 region 数 | 状态 |
|---|---|---:|---|---|
| 1 | `[1,3,800,800]` × 4 | 4 | 8 / 14 / 12 / 7 | PASS |
| 2 | `[2,3,800,800]` × 2 | 4 | 8 / 14 / 12 / 7 | PASS |
| 4 | `[4,3,800,800]` × 1 | 4 | 8 / 14 / 12 / 7 | PASS |

三组完整 boxes 输出 hash 均为 `a5e7c2f44c7835345f099793f0d010943b15ab2cdec2767bc349904c30350cea`。这证明该 runtime 能执行真正 batch，且这 4 个 CPU 样本输出一致；不代表 GPU 已验证，也不代表全量合同业务语义等价。探针单线程、低优先级运行且曾暂停，不使用其中耗时作性能结论。[探针结果](../../output/analysis/doclayout-batch-capability-20260924/cpu-probe.json)、[探针源码](../../experiments/doclayout_batch_capability/probe.py)

## 2. 当前服务为何不会自动 batch / GPU 并行

当前服务源码明确只接收 `file: str`，取得全局模型锁后执行 `predict(image, batch_size=1)`，最后仅取第一个结果。HTTP 请求在前处理和网络层可以重叠，但锁内推理不会重叠。仅把 `batch_size=1` 改成 4，仍然只有一张输入，不会产生 4 张图片的 batch。[当前服务器源码快照](../../output/analysis/layout-server-batch-parallel-20260924/doclayout-server.py)

后续若保持每页一个 HTTP 请求，可在服务内部增加**有界微批队列**，无需先设计公开多图 API：入口完成解码后，将 `request_id/page_index/image` 入队；一个模型 worker 最多收集 2 或 4 张，达到上限或短等待期限就执行一次 `predict(images, batch_size=len(images))`；逐项把结果返回对应请求。队列设容量与超时，满载时明确拒绝；请求的 threshold、NMS 等推理选项必须相同才能进入同一批。这是针对当前服务的实现建议，不是 PaddleX 已自动提供的动态合批能力。

批内严格保留输入索引，HTTP 完成后客户端再按 `page_index` 排序。验证 runner 收到的第一维确实大于 1，并同时检查输出语义 hash、区域与阅读顺序、HTTP 错误、队列延迟、显存及 GPU 稳定性。仅看到多个 HTTP 请求在飞行中，不足以证明真正合批。

实际部署的 PP-StructureV3 已核实为请求内 batch=4、跨请求队列串行，与独立 DocLayout 的 batch=1 有区别；四页服务实测见[服务端 batch/并行报告](layout-server-batch-parallel-20260924.md)。

## 3. 并行与 PP-StructureV3 的差别

官方 PP-StructureV3 的基本服务默认一次处理一个请求；高稳定服务需要配置多实例才能增加请求并行度。产线 API 的多 GPU 示例是 `create_pipeline(pipeline="PP-StructureV3", device="gpu:0,1")`；其官方多进程示例在每个 worker 内独立初始化产线，允许每 GPU 多个实例。[官方服务 FAQ](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-StructureV3/PP-StructureV3.html)、[PaddleX 3.7.2 并行推理](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/docs/pipeline_usage/instructions/parallel_inference.md)

该示例的 `--batch_size` 是 worker 一次提交的输入文件数量，不能据此认定 PP-StructureV3 的每个内部模型都以相同 batch 运行。单模块 CLI 在 PaddleOCR wrapper 中设置 `allow_multiple_devices=False`；因此不能把 PP-StructureV3 的 `gpu:0,1` 多设备入口当成 `LayoutDetection` 的已支持开关。[单模型 CLI 源码](https://github.com/PaddlePaddle/PaddleOCR/blob/v3.7.0/paddleocr/_models/base.py)

如果采用多个 Layout 模型实例，可使用固定数量独立进程，每个进程在选定 GPU 上创建自己的 `LayoutDetection`，由有界任务队列分派页面；这符合官方进程内初始化模式，但这里没有验证该服务器上的进程数收益。不建议以多个线程同时调用同一个 predictor 来“开启并行”：当前 SDK 会修改共享 batch sampler 状态，且官方资料未在本次核查中给出该实例的线程安全承诺。[BasePredictor 共享状态](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.2/paddlex/inference/models/predictors/base_predictor.py)

## 4. Transformers 路径

Transformers 官方模型页单独列出 PP-DocLayoutV3 的批量推理。显式设置 `batch_size` 的 pipeline 调用可避免把“列表输入”误当成必然合批：

```python
from transformers import pipeline

detector = pipeline(
    "object-detection",
    model="PaddlePaddle/PP-DocLayoutV3_safetensors",
    device=0,
)
results = detector(["/data/page_01.png", "/data/page_02.png"], batch_size=2)
```

该方案使用 Transformers / PyTorch 和 safetensors 权重。它证明模型家族存在官方批量实现，但不能证明与现有 Paddle 输出完全等价，也不是本轮冻结 Challenger 可以直接替换的调度开关。[Transformers PP-DocLayoutV3](https://huggingface.co/docs/transformers/model_doc/pp_doclayout_v3)

本次最有价值的下一项证据是：**在隔离 GPU 条件下，用现有 Paddle 权重与运行时执行 batch 2/4，检查逐页输出一致性与 GPU wall time**。CPU 功能验证已经完成，无需为开启 SDK batch 先切换 Transformers、模型或公开服务协议。
