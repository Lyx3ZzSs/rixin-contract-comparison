# Docker 部署指南

## 部署形态

本项目的 Docker Compose 部署只启动两个容器：

- `backend`：FastAPI 后端，监听容器内 `8000`。
- `frontend`：Nginx + React 静态资源，监听容器内 `8080`，并代理 `/api/` 和 `/health` 到后端。

PP-OCRV5 和 PP-Structure 不在本 compose 中启动，需要使用外部 HTTP 服务。

## 1. 准备环境变量

```bash
cp .env.example .env
```

编辑根目录 `.env`，至少配置：

```env
PPOCRV5_URL=http://host.docker.internal:8866
PPSTRUCTURE_URL=http://host.docker.internal:8867
```

如果 OCR 服务不在 Docker 宿主机上，请改成 OCR 服务可被后端容器访问的内网地址，例如：

```env
PPOCRV5_URL=http://192.168.1.20:8866
PPSTRUCTURE_URL=http://192.168.1.20:8867
```

不要在 `.env` 中使用 `127.0.0.1` 指向宿主机服务；容器内的 `127.0.0.1` 是容器自身。

## 2. 构建并启动

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

## 3. 访问地址

- 前端工作台：`http://127.0.0.1:8080`
- 后端健康检查：`http://127.0.0.1:8000/health`
- 通过前端 Nginx 代理的健康检查：`http://127.0.0.1:8080/health`
- 后端 API 文档：`http://127.0.0.1:8000/docs`

## 4. 验证

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8080/health
```

如果健康检查通过但合同比对失败，优先检查后端容器是否能访问 OCR 服务：

```bash
docker compose exec backend curl -f "$PPOCRV5_URL"
docker compose exec backend curl -f "$PPSTRUCTURE_URL"
```

也可以直接进入后端容器检查当前配置：

```bash
docker compose exec backend env | grep -E 'PPOCRV5_URL|PPSTRUCTURE_URL|STORAGE_DIR'
```

## 5. 查看日志

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

## 6. 停止和更新

停止容器但保留任务数据：

```bash
docker compose down
```

重新构建并启动：

```bash
docker compose up -d --build
```

删除任务数据卷：

```bash
docker compose down -v
```
