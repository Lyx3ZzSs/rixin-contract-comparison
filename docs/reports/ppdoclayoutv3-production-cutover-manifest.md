# PP-DocLayoutV3 生产替换回滚清单

记录时间：2026-09-24（Asia/Shanghai）。此清单在删除旧实现之前创建。

## 可核实的发布点

- Git tag：`pre-ppdoclayoutv3-production-cutover`
- Git commit：`48a23804c1273ef59deea775707047efc60ae339`
- **注意：tag 仅包含该提交。创建时工作区已有 154 项未提交变更，包括生产代码、实验和文档。回滚到 tag 不会恢复这些未提交内容；发布镜像必须从确定的提交构建，不能把当前工作区当作可回滚 release。**
- 本地 `.env` 有 57 个已设置变量；敏感值不写入仓库。相关变量名：`COMPARE_DOCUMENT_EXTRACTOR`、`COMPARE_REQUIRE_STRUCTURED_OCR`、`PPOCRV5_URL`、`PPOCRV5_ACCESS_TOKEN`、`PPOCRV5_TIMEOUT_SECONDS`、`PPSTRUCTURE_URL`、`PPSTRUCTURE_ACCESS_TOKEN`、`PPSTRUCTURE_TIMEOUT_SECONDS`、`PPSTRUCTURE_USE_TABLE_RECOGNITION`、`PPSTRUCTURE_USE_SEAL_RECOGNITION`、`PPSTRUCTURE_USE_REGION_DETECTION`、`HYBRID_COMPONENT_PARALLEL_ENABLED`、`LAYOUT_ANALYSIS_MODE`。
- 本地 `storage/tasks.sqlite3`：`PRAGMA user_version = 0`；7 条任务记录的 `schema_version = 3`。这不是生产数据库版本。
- 仓库中的 PP-DocLayoutV3 Compose 固定镜像 ID：`sha256:207108115cbdb8ecb0e5f085d4e8d17bc5a7cf0e72370f2410148d7fa547ebe5`。尚未核对运行实例。

## 部署前必须补齐的运行信息

生产主机 `sprixin@10.10.12.64` 的 SSH 密钥认证失败，无法读取当前生产 backend 镜像 ID、PP-StructureV3 镜像 ID / 容器配置、环境变量实际值、生产数据库 schema version 或服务日志。因此**当前没有经过验证的完整运行时回滚点，不能声称已完成生产部署**。

取得主机访问后，应将以下只读结果附入受控发布记录：`docker inspect rixin-backend ppstructurev3-centos7` 的 image ID / image tag 与配置备份、部署 release ID、生产数据库 schema version，以及环境变量名称和加密备份位置。令牌等敏感值不得写入本仓库。

## 回滚方式

代码回滚到 tag 对应提交；部署回滚使用已记录的旧 backend Docker image / release。PP-StructureV3 服务镜像与配置应保留到观察窗口结束。当前缺少已核实的运行镜像与 release ID，因此具体生产回滚命令尚不能安全写定。
