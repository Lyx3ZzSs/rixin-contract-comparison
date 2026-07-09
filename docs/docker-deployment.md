# Docker 部署指南

## 部署形态

本项目的 Docker Compose 部署只启动两个容器：

- `backend`：FastAPI 后端，监听容器内 `8000`，只暴露在 Docker 内部网络。
- `frontend`：Nginx + React 静态资源，监听容器内 `8080`，绑定到宿主机 `127.0.0.1:18080`，并代理 `/api/` 和 `/health` 到后端。

生产部署时，服务器外层统一入口 Nginx 只需要把项目 URI 前缀转发到 `frontend` 的宿主机端口；后端不需要单独对宿主机或公网开放端口。

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

默认配置适用于服务器外层统一入口 Nginx 通过 URI 前缀区分项目，例如：

```text
http://server:8080/contract/
```

`.env.example` 已经给出对应默认值；需要改项目路径或本机 upstream 端口时，调整：

```env
FRONTEND_BIND_ADDR=127.0.0.1
FRONTEND_HOST_PORT=18080
VITE_BASE_PATH=/contract
VITE_API_BASE_PATH=
VITE_API_BASE_URL=
```

`VITE_API_BASE_PATH` 通常留空，前端会默认使用 `VITE_BASE_PATH + /api`，即 `/contract/api`。这些 `VITE_*` 变量会在前端 Docker 镜像构建时写入静态资源；修改后必须重新执行 `docker compose up -d --build`。

## 2. 构建并启动

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

## 3. 外层 Nginx 转发

前端容器端口 `127.0.0.1:18080` 只作为外层 Nginx upstream 使用，不作为最终浏览器入口。外层 Nginx 需要去掉 `/contract/` 前缀后转发到前端容器：

```nginx
location = /contract {
    return 301 /contract/;
}

location ^~ /contract/ {
    proxy_pass http://127.0.0.1:18080/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /contract;

    client_max_body_size 80m;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    proxy_buffering off;
}
```

不要再单独配置 `/contract/api/` 转发到宿主机 `8000`。当前 compose 中后端只在 Docker 内部网络暴露，浏览器 API 请求链路是：

```text
/contract/api/* -> 外层 Nginx -> 127.0.0.1:18080/api/* -> frontend 容器 Nginx -> backend:8000/api/*
```

浏览器访问外层入口：

- 前端工作台：`http://server:8080/contract/`
- 对比记录：`http://server:8080/contract/compare/records`
- API：`http://server:8080/contract/api/compare/records`

## 4. 验证

```bash
curl http://127.0.0.1:18080/health
```

外层 Nginx 配置生效后：

```bash
curl http://server:8080/contract/health
curl http://server:8080/contract/api/compare/records
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
