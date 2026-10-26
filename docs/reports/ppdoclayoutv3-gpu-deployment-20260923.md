# PP-DocLayoutV3 GPU 独立部署验收

2026-09-23：已部署至 `10.10.12.64` 的 RTX 4090，目录 `/home/sprixin/ppocr_docker/ppdoclayoutv3`，容器 `ppdoclayoutv3-service`。

- 健康检查：`http://10.10.12.64:8090/health`
- 推理接口：`POST http://10.10.12.64:8090/layout-detection`
- 接口文档：`http://10.10.12.64:8090/docs`
- 运维与请求示例：[README](../../deployment/ppdoclayoutv3/README.md)

参考目录 `ppstructurev3/docker-compose.yml` 是旧配置；实际运行中的 `ppstructurev3-centos7` 使用 `ppocrv6:centos7-4090-v1`，因此新服务沿用该已适配 CentOS 7 的镜像，并固定 image ID。使用独立端口、单进程 GPU 常驻 Layout 模型，不加载 OCR/表格模型。模型文件 SHA-256 与本地 Challenger 权重逐项一致。

运行库为 PaddleOCR 3.7.0、PaddleX 3.7.2、PaddlePaddle GPU 3.1.1，与冻结 Challenger 的 CPU 环境不同；未宣称业务语义等价。默认阈值 0.5、NMS=false，接口可按类别指定阈值，返回官方类别、坐标、多边形及 order。

## 验证结果

真实合同 B 原文第 1 页、同一 PNG、连续 5 次请求均成功，默认阈值下每次返回 33 个区域。排除首个真实文档请求后的 4 次：模型调用与结果整理中位数 0.112 秒，本机经网络访问中位数 0.171 秒。首个真实文档请求为 0.169 秒模型调用、0.238 秒客户端耗时；服务启动时已做合成图预热。这是单页顺序冒烟测试，不是吞吐、P95 或 A–E 端到端结果。

新进程推理后占用约 1068 MiB GPU 显存。容器重启后，健康检查、真实页面推理、类别阈值字典请求均通过。非法 Base64/无效或空图片返回 400，非法 fileType/阈值返回 422。原 OCR 和 PP-StructureV3 的 8088、8089 健康检查均返回 200；8000、8001 的 nginx 入口 `/health` 返回 404，因此不将该路由用于旧代理健康判定。

新服务未接入冻结 A–E 或生产合同对比。部署于 13:03:26 UTC 开始，与 C 组运行重叠；GPU 常驻占用及少量验证请求改变了共享服务器负载，已在 A–E 输出目录单独记录环境变化，不应把重叠数据视为严格隔离环境的性能收益证据。

`READY_FOR_PRODUCTION=false`、544 个 `DEFERRED_BUSINESS_REVIEW` 和 Golden expectation 未修改。业务上线仍依赖后续 review 或明确风险接受。

证据目录：`output/analysis/ppdoclayoutv3-deployment-20260923/`，含健康响应、原始样例结果、5 次测量、重启验证、部署镜像/文件 hash 和共享 GPU 环境记录。原矩阵旁另存 `environment-note-ppdoclayoutv3.json`，不更改原测量结果。
