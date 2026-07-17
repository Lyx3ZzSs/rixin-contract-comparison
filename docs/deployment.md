# 国能日新 · 合同智能审查平台 — 部署文档（Docker）

> **最后更新:** 2026-07-17

## 1. 系统要求

| 组件 | 最低版本 | 用途 |
|------|---------|------|
| Docker | 24+ | 容器运行时 |
| Docker Compose | 2.24+ | 多容器编排 |

**外部依赖服务（需确保从容器内可访问）：**

| 服务 | 配置项 | 说明 |
|------|-------|------|
| PP-OCRv5 | `PPOCRV5_URL` | 文字识别服务，用于扫描件 OCR |
| PP-Structure | `PPSTRUCTURE_URL` | 版面分析服务，合同比对必需 |

> 合同比对强制依赖 PP-OCRv5 和 PP-Structure 同时可用，任一不可用比对任务会失败。

## 2. 架构概览

```
浏览器 ──:80──▶ rixin-frontend (Nginx) ──/api/*──▶ rixin-backend (:8000)
                            │                           │
                        dist/ (SPA)              ┌──────┴──────┐
                                                 │ PP-OCRv5    │
                                                 │ PP-Structure│
                                                 └─────────────┘
                                                        │
                                                 storage_data 卷
                                                 (/data/storage)
```

- **rixin-backend**: FastAPI + uvicorn，Python 3.12，端口 8000
- **rixin-frontend**: Nginx 静态托管 React SPA + `/api/*` 反向代理，端口 80
- **storage_data**: Docker 命名卷，持久化任务元数据、上传文件、OCR 原始数据和报告

## 3. 前置准备

### 3.1 安装 Docker

```bash
# Ubuntu
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable docker --now
sudo usermod -aG docker $USER  # 重新登录后生效

# CentOS
sudo yum install -y docker docker-compose-plugin
sudo systemctl enable docker --now
```

### 3.2 获取代码

```bash
git clone <your-repo-url> /opt/rixin-contract-comparison
cd /opt/rixin-contract-comparison
```

### 3.3 配置环境变量

```bash
cp .env.example .env
vim .env
```

所有配置通过项目根目录 `.env` 管理，`docker-compose.yml` 通过 `env_file` 注入到后端容器。关键必填配置项见下节。

## 4. 配置说明

### 4.1 必填：OCR / 版面分析服务

```
PPOCRV5_URL=https://your-ppocrv5-host/
PPOCRV5_TIMEOUT_SECONDS=600
PPOCRV5_RETURN_WORD_BOX=true

PPSTRUCTURE_URL=https://your-ppstructure-host/
PPSTRUCTURE_TIMEOUT_SECONDS=600
PPSTRUCTURE_USE_TABLE_RECOGNITION=true
PPSTRUCTURE_USE_SEAL_RECOGNITION=true
```

### 4.3 上传限制

```
MAX_UPLOAD_SIZE_MB=30
```

### 4.4 差异匹配与报告

```
MATCH_THRESHOLD=85
REPORT_FONT_PATH=/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc
```

### 4.5 Document extraction/OCR（合同比对）

```
DOCUMENT_EXTRACTOR=auto
COMPARE_DOCUMENT_EXTRACTOR=ppstructure_ocr_hybrid
COMPARE_REQUIRE_STRUCTURED_OCR=true
PYMUPDF_MIN_TEXT_CHARS=1
ALIGN_STRUCTURED_EXTRACTION=true
SAVE_OCR_RAW_RESULT=true
```

### 4.6 任务执行器

```
TASK_RUNNER_MAX_WORKERS=2
TASK_RUNNER_MAX_ATTEMPTS=1
TASK_RUNNER_LEASE_SECONDS=3600
TASK_RUNNER_RETRY_DELAY_SECONDS=2
TASK_RUNNER_POLL_INTERVAL_SECONDS=0.25
```

### 4.7 Docker Compose 环境变量覆盖

`docker-compose.yml` 中已预设以下覆盖（通常无需修改）：

```yaml
environment:
  - STORAGE_DIR=/data/storage
  - QUALITY_CASES_DIR=/data/storage/quality/cases
  - QUALITY_RUNS_DIR=/data/storage/quality/runs
  - QUALITY_CASES_SEED_DIR=/app/resources/quality_cases
  - FRONTEND_CORS_ORIGINS=http://localhost,http://127.0.0.1
```

如果需要修改后端监听端口，需要同时更新 `docker-compose.yml` 中 `ports` 映射和 `frontend/nginx.conf` 中的 `proxy_pass` 地址。

## 5. 启动与停止

### 5.1 构建镜像

```bash
docker compose build
```

### 5.2 启动服务

```bash
docker compose up -d
```

首次启动会自动构建镜像（如未提前构建）。后端容器会等待 health check 通过后，前端容器才启动。

后端启动 API 前会运行 `python scripts/init_quality_cases.py`。该命令把镜像中经过脱敏和批准的
`QUALITY_CASES_SEED_DIR` 案例幂等初始化到持久卷内的 `QUALITY_CASES_DIR`，质量运行结果写入
`QUALITY_RUNS_DIR`。已有同 case ID 的管理员案例不会覆盖；后续启动和重新初始化会原样保留它。

### 5.3 查看状态

```bash
docker compose ps
docker compose logs -f              # 实时日志
docker compose logs backend         # 仅后端日志
docker compose logs frontend        # 仅前端日志
```

### 5.4 停止服务

```bash
docker compose down                 # 停止并移除容器（保留卷）
docker compose down -v              # 停止并删除卷（清除所有任务数据）
```

### 5.5 更新部署

```bash
git pull
docker compose build                # 重新构建镜像
docker compose up -d                # 滚动重启
```

## 6. 存储与备份

任务数据存储在命名卷 `storage_data` 中，挂载到容器的 `/data/storage`：

```text
/data/storage/
  tasks/
    {task_id}/
      task.json            # 任务元数据
      job.json             # 队列执行元数据
      manifest.json        # 产物索引
      uploads/             # 原始上传文件
      ocr/                 # OCR 原始结果
      debug/               # 调试输出
      reports/             # 生成的 PDF 报告
  quality/
    cases/                  # 管理员案例和镜像种子案例
      seed-manifest.json    # 已应用镜像 seed_version 与案例 ID 元数据
    runs/                   # 质量评估和回归运行输出
```

`seed-manifest.json` 只记录最近成功应用的镜像 seed manifest 版本和清单，不声明目录中只有种子案例，
也不会赋予初始化器覆盖管理员案例的权限。管理员通过质量工作台导出的案例会追加到同一 cases 目录，
以后执行初始化仍不会覆盖。

部署人员可以在容器内手工重复执行初始化：

```bash
docker compose exec backend python scripts/init_quality_cases.py
```

命令成功或失败都会输出结构化 JSON，并以非零退出码报告失败。`QUALITY_CASES_DIR` 与
`QUALITY_CASES_SEED_DIR` 的解析结果必须彼此分离，不能相同或互为父子目录（包括符号链接解析后的
关系）；违反时命令以 `QUALITY_CASES_PATH_CONFLICT` 失败，防止递归复制或修改只读种子。

### 备份存储卷

```bash
# 创建备份
docker run --rm -v rixin-contract-comparison_storage_data:/data -v $(pwd):/backup alpine \
    tar czf /backup/storage-backup-$(date +%Y%m%d).tar.gz -C /data .

# 恢复备份
docker run --rm -v rixin-contract-comparison_storage_data:/data -v $(pwd):/backup alpine \
    tar xzf /backup/storage-backup-YYYYMMDD.tar.gz -C /data
```

## 7. 验证部署

### 7.1 健康检查

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl -I http://localhost/
# HTTP/1.1 200 OK
```

### 7.2 合同比对测试

```bash
curl -X POST "http://localhost:8000/api/compare" \
  -F "original_file=@test_original.pdf" \
  -F "compare_file=@test_compare.pdf"
```

### 7.3 前端访问

浏览器打开 `http://<your-server-ip>` 即可访问。

## 8. 健康监控

| 端点 | 用途 |
|------|------|
| `GET /health` | 容器存活检测（后端） |
| `GET /` | 前端首页可达性 |
| `docker compose ps` | 容器运行状态 |
| `docker stats` | 容器资源使用 |

## 9. 故障排查

### 后端容器无法启动

```bash
# 查看容器日志
docker compose logs backend

# 进入容器检查
docker compose exec backend bash
python -c "from app.config import settings; print(settings.model_dump())"

# 测试外部服务连通性
docker compose exec backend curl -s -o /dev/null -w "%{http_code}" "${PPOCRV5_URL}"
docker compose exec backend curl -s -o /dev/null -w "%{http_code}" "${PPSTRUCTURE_URL}"
```

### 前端 API 调用失败

确认 `frontend/nginx.conf` 中 `proxy_pass http://backend:8000` 配置正确，且后端容器 health check 通过：

```bash
docker compose ps backend  # 应显示 (healthy)
docker compose exec frontend wget -qO- http://backend:8000/health
```

### 报告 PDF 中文乱码

在 `.env` 中指定中文字体路径：

```
REPORT_FONT_PATH=/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc
```

如果后端镜像内缺少中文字体，需要在 `backend/Dockerfile` 中添加字体安装步骤（见高级配置）。

### 文件上传失败 (413)

确认 `frontend/nginx.conf` 中已配置 `client_max_body_size 80m`，且上传文件未超过 `.env` 中的 `MAX_UPLOAD_SIZE_MB` 限制。

### 磁盘空间不足

```bash
# 查看卷占用
docker system df -v | grep storage_data

# 清理旧任务数据
docker compose exec backend bash
ls /data/storage/tasks/  # 手动清理旧目录
```

## 10. 高级配置

### 10.1 自定义后端端口

修改 `docker-compose.yml`：

```yaml
services:
  backend:
    ports:
      - "8001:8000"  # 宿主机端口改为 8001

  frontend:
    ports:
      - "8080:80"    # 前端对外端口改为 8080
```

同时修改 `frontend/nginx.conf` 中的 `proxy_pass`（如果后端服务名或端口变更）。

### 10.2 添加中文字体支持

在 `backend/Dockerfile` 的 `RUN apt-get` 行中追加：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
```

然后在 `.env` 中配置：

```
REPORT_FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
```

### 10.3 HTTPS 配置

在前端 Nginx 容器前面加一层反向代理（如 Traefik、Nginx Proxy Manager），或者将 SSL 证书挂载到前端容器并修改 `nginx.conf` 添加 443 端口监听。

### 10.4 资源限制

在 `docker-compose.yml` 中为每个服务添加资源限制：

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          memory: 2g
          cpus: "2"
```

## 11. 安全建议

- 不要将 `.env` 提交到 Git（已在 `.gitignore` 中排除）
- 按部署环境配置 Keycloak 公共 OIDC 客户端的 authority、client ID、精确回调地址、登出回调地址和 Web Origin；前端使用授权码 + PKCE，不得配置 Client Secret
- 配置 HTTPS（Let's Encrypt 或企业证书）
- 限制 PP-OCRv5 / PP-Structure 的网络访问范围
- 不要在公网暴露后端 8000 端口（移除 `docker-compose.yml` 中 `ports: "8000:8000"`）
- 定期备份 `storage_data` 卷
