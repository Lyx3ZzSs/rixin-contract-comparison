# PP-DocLayoutV3 batch / 并发重部署验证（2026-09-24）

已重新部署到 **http://10.10.12.64:8090**，容器 `ppdoclayoutv3-service`，服务版本 `2.0.0`。真实 GPU batch 与有界 HTTP 并发已验证通过；**batch 2/4 对旧单图输出的等价验证未通过**，因此原单页接口默认保留 batch 1，仅显式多图请求或 `dynamicBatch=true` 使用批量推理。

- 真正支持 batch：Paddle runner 实际输入出现 `[2,3,800,800]`、`[4,3,800,800]`；最终基准共处理 1200 页，执行 batch 1 × 720、batch 2 × 80、batch 4 × 80，计 880 次 forward。
- 固定 batch 4、2 个并发请求：40 个 D 扫描页 wall P50 **3.972s**，相对旧单图串行 7.391s 降低 **46.3%**；相对旧服务最佳并发 4.991s 降低 **20.4%**。
- 正常基准 HTTP error = 0、retry = 0；无模型错误、无 OOM、无意外容器重启。最终默认接口的全量 80 页及并发 1/2/4/8 输出与旧服务完全一致。
- 固定 batch 的重复输出各自稳定，但与单图不同；动态合批会随分组变化产生不同原始哈希。不能据此宣布冻结 D Challenger 的业务输出等价或 Pipeline 达标。

## 当前开启方式

| 能力 | 调用方式 | 实际行为 |
|---|---|---|
| 兼容单图并发 | `POST /layout-detection`，保持原请求 | 最多 8 个活动 HTTP 请求；每页 batch 1，模型线程逐批执行 |
| 固定多图 batch | `POST /layout-detection/batch`，`files` 传 1–4 图 | 一个请求保留固定分组；按输入顺序返回 `result.pages` |
| 动态合批 | 单图请求增加 `dynamicBatch=true`，客户端有界并发 | 相同 threshold/NMS 的不同请求合批，最多 4 图 |

```python
import base64
from pathlib import Path
import httpx

payload = {
    "files": [base64.b64encode(Path(p).read_bytes()).decode()
              for p in ["page-1.png", "page-2.png", "page-3.png", "page-4.png"]],
    "pageIds": ["1", "2", "3", "4"],
    "fileType": 1,
    "threshold": {"6": 0.4, "17": 0.4},
    "layoutNms": False,
}
response = httpx.post("http://10.10.12.64:8090/layout-detection/batch",
                      json=payload, timeout=60, trust_env=False)
response.raise_for_status()
for page in response.json()["result"]["pages"]:
    print(page["pageIndex"], page["pageId"], page["batchSize"], page["boxes"])
```

单图动态合批需要客户端同时发出多个请求。客户端逐页 `await` 不可能汇集尚未发出的页面。服务只有一个 owning model worker；支持的是 HTTP 并发与批内多图计算，批次之间串行，不是多个 predictor 同时占用 GPU。

配置位于 `/home/sprixin/ppocr_docker/ppdoclayoutv3/docker-compose.yml`：

```yaml
MAX_BATCH_SIZE: "4"
BATCH_WAIT_MS: "10"
MAX_PENDING_PAGES: "16"
MAX_PENDING_IMAGE_MIB: "256"
MAX_ACTIVE_REQUESTS: "8"
REQUEST_TIMEOUT_SECONDS: "60"
```

兼容单图和固定分组无需等待动态合批窗口。排队与正在推理的图像共同计入页面/图像内存上限；批量请求原子入队。超限返回 503 + `Retry-After: 1`，超时返回 504，服务不自动重试。模型错误会停止接单并使 health 不健康，需排查后重启。未就绪不回退 CPU。模型与调用在同一线程，Uvicorn 保持 `--workers 1`。

## 性能实测

仅用 D，40 页扫描合同；另有 D 原文 + 扫描件共 80 页单图等价验证。所有候选均使用相同 PNG（2× PDF 渲染，输入 SHA-256 对照一致）、同一 GPU、镜像、权重及阈值 `{6:0.4,17:0.4}`，`layoutNms=false`。每组预热 1 次、正式 3 次。wall 从客户端开始发请求到全部返回，含 LAN 传输、JSON 和队列时间，不含预先渲染；不是 Extraction/Pipeline 全链路耗时。

| 版本 | 每请求图数 | HTTP 并发 | 40 页 wall P50(s) | 请求 latency P50 / max(ms) | 3 次原始 hash 稳定 | 与旧 batch 1 一致 |
|---|---:|---:|---:|---:|---|---|
| 旧服务：单图 | 1 | 1 | 7.391 | 177.2 / 255.6 | 是 | 是 |
| 旧服务：单图 | 1 | 2 | 4.991 | 242.3 / 351.6 | 是 | 是 |
| 旧服务：单图 | 1 | 4 | 5.169 | 497.7 / 627.1 | 是 | 是 |
| 旧服务：单图 | 1 | 8 | 5.105 | 987.0 / 1125.9 | 是 | 是 |
| 候选：动态合批 | 1 | 1 | 8.116 | 210.1 / 242.8 | 是 | 是 |
| 候选：动态合批 | 1 | 2 | 4.877 | 238.3 / 330.2 | 否 | 否 |
| 候选：动态合批 | 1 | 4 | 3.907 | 384.4 / 622.4 | 否 | 否 |
| 候选：动态合批 | 1 | 8 | 3.978 | 772.5 / 1003.0 | 否 | 否 |
| 已部署：兼容单图 / 固定 batch | 1 | 1 | 7.258 | 187.2 / 246.4 | 是 | 是 |
| 已部署：兼容单图 / 固定 batch | 2 | 1 | 6.138 | 292.0 / 378.4 | 是 | 否 |
| 已部署：兼容单图 / 固定 batch | 4 | 1 | 5.876 | 585.1 / 658.6 | 是 | 否 |
| 已部署：兼容单图 / 固定 batch | 1 | 2 | 4.777 | 232.9 / 327.7 | 是 | 是 |
| 已部署：兼容单图 / 固定 batch | 4 | 2 | 3.972 | 753.3 / 987.8 | 是 | 否 |
| 已部署：兼容单图 / 固定 batch | 1 | 4 | 4.648 | 447.0 / 572.6 | 是 | 是 |
| 已部署：兼容单图 / 固定 batch | 1 | 8 | 4.498 | 855.2 / 1044.5 | 是 | 是 |

latency P50 是三次逐请求 P50 的中位数，max 是三次中最大值。候选动态合批是部署前的独立试验；其自动合批默认行为未上线，最终服务通过显式字段提供该能力。固定 batch 4 + 2 请求的 wall 优于只看单次请求耗时所能判断的结果；并发继续升高不一定带来收益，未进行无限并发或全部排列组合。

## 输出稳定性与未通过项

默认接口完整 80 页 boxes 的 SHA-256：

```text
66f8af79d074a720ad85ab134f23392b15748f7ed10cab0ad30105047591aed6
```

40 个扫描页：

| 模式 | 原始 boxes 总 hash | 固定分组重复稳定 |
|---|---|---|
| batch 1 | `183768c4d68284276ac61d5ea0885c2d25b01b805164dc0f2441cb96275d8c71` | 是 |
| batch 2 | `f1db2168efcac4ffa65a0f6c0c6334612e34faa09ff5c25b39932c6c6dad6e7d` | 是 |
| batch 4 | `4d4b9c8225f7c2e6fbf0c3b174a872d88a0e0e52a099d11e352b6ed9019f63c4` | 是 |

两种固定 batch 均有 40/40 页原始 hash 不同于单图；所有页面 region 数、class、label、order 一致。batch 2 有 6 页框坐标变化；batch 4 有 5 页变化（扫描件第 9、10、13、34、39 页，1 起算）。其中第 13 页最大坐标差 **164 输入图像像素**，最大 score 差约 **0.02696**；其余 batch 4 坐标变化页最大差为 1 像素。polygon 也有对应变化。

这些差异的底层原因尚未定位，不能仅按浮点尾数误差忽略。服务原样返回模型结果，没有通过舍入、坐标归一化、排序或修改阈值制造 hash 一致。动态分组随请求到达时间变化，因此即使输入页相同，也不能保证跨次原始 hash 相同。

结论：服务器 batch 能力可用；在冻结输出要求下，batch 2/4 仍不能替代现有单图推理。未运行或宣称新的 clause/diff/event 等业务链路等价结果；原 D 性能报告和 Golden expectation 不改写，544 个业务待审继续冻结。

## 可靠性与资源

- 本地 13 项测试通过：线程归属、真实分组、输入顺序、选项隔离、原子准入、队列/图像字节上限、取消/过期、模型失败传播、兼容接口、默认单图、固定分组、显式动态开关、参数/请求体校验。
- 正常最终基准 1200 页，无 HTTP error/retry、模型错误；峰值 pending 8 页、47,295,276 字节，结束时队列清空。
- 额外过载测试故意发送 12 个并发 batch 请求，触发预期 503，成功请求保留顺序与固定 batch hash；随后普通请求恢复。该受控拒绝不混入正常负载 error 统计。
- 最终容器执行受控重启后再次验证通过：12 个过载请求中 4 个成功、8 个按预期返回 503；随后恢复正常。动态请求实际形成 batch 3/4。服务 health 正常，OOMKilled=false、RestartCount=0（无意外重启）。OCR 与 PP-StructureV3 的启动时间未变，8088/8089 健康检查均返回 200。
- GPU 总显存实测峰值 **23,840 / 24,564 MiB**，最低余量约 **724 MiB**。旧服务基线约 22,520 MiB。现有显存空间不适合通过增加 Uvicorn workers 复制模型。
- CPU/RSS 采样保存在原始资源文件；正式基准采样曾达到约 331% CPU（Docker 按单核 100%），进程 RSS 与高水位另有记录。采样值不代表硬资源上限。
- 重启后过载与恢复测试结束时，进程 RSS 为 1,579,072 KiB，高水位 1,855,676 KiB，约 1.51 / 1.77 GiB。
- 固定镜像 ID `207108115cbdb8ecb0e5f085d4e8d17bc5a7cf0e72370f2410148d7fa547ebe5`；PaddleOCR 3.7.0 / PaddleX 3.7.2 / Paddle GPU 3.1.1。权重 SHA 与原服务一致。
- 当前机器 OCR 与 PP-StructureV3 保持原进程。没有本轮完整 OCR + Layout + Table 同时负载的全链路重测，不将此服务测量外推为新的 D Pipeline 成绩。

## 与 PP-StructureV3 的区别

| 项目 | 当前 PP-StructureV3 | 本次 PP-DocLayoutV3 |
|---|---|---|
| 用途 | 全结构化 pipeline | 单独 Layout |
| 实际 Layout 模型 | PP-DocLayout_plus-L（既有配置） | PP-DocLayoutV3 |
| 既有配置 batch | pipeline / Layout 配置为 4 | 最大 4；固定多图或显式动态合批 |
| HTTP 并发 | 既有 wrapper 串行 pipeline 调用 | 有界异步 HTTP，单模型线程批量推理 |
| 跨 HTTP 动态汇集 | 未证实启用；并发 HTTP 不等于自动 batch | 显式 `dynamicBatch=true`，10ms 窗口 |
| 可观测证据 | 既有源码/配置记录 | 每批 ID、size、队列计数、真实 runner shape |

PP-StructureV3 的内部批量配置不意味着客户端发多个请求就会并行运行完整 pipeline；其 OCR 识别 crop batch 也不能当作全页 Layout batch。既有服务对照证据见 `layout-server-batch-parallel-20260924.md`，SDK/CPU probe 见 `ppdoclayoutv3-batch-sdk-research-20260924.md`。

## 运维、回滚与证据

```bash
cd /home/sprixin/ppocr_docker/ppdoclayoutv3
docker compose ps
curl -fsS http://127.0.0.1:8090/health
curl -fsS http://127.0.0.1:8090/metrics
# 恢复升级前的单图服务
bash rollback-pre-batch.sh
```

原文件与容器信息保存在 `backups/pre-batch-20260924/`；候选与发布文件保存在 `releases/batch-20260924/`。候选 8091 容器已清理；正式部署不依赖该容器。

源码：`deployment/ppdoclayoutv3/server.py`、`layout_batcher.py`、`docker-compose.yml`、`README.md`；验证：`test_batching.py`、`verify_live.py`、`verify_edges.py`。原始证据目录：`output/analysis/ppdoclayoutv3-batch-deployment-20260924/`，包含 `baseline.json`、`candidate.json`、`deployed.json`、`summary.json`、逐页差异 `batch-output-differences.json`、runner 指标、服务日志、资源采样及部署前文件。
