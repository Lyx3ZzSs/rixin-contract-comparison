# Docker Compose External OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Docker Compose deployment reliable and documented for a two-container app that connects to external OCR services.

**Architecture:** Keep `backend` and `frontend` as separate compose services. The frontend Nginx container serves the SPA on port `8080` and proxies API traffic to `backend:8000`; the backend stores task artifacts in the existing `storage_data` volume and reads OCR service URLs from the root `.env`.

**Tech Stack:** Docker Compose, Python 3.12 FastAPI/Uvicorn, Node 20/Vite, Nginx Alpine.

---

## File Structure

- Modify `frontend/Dockerfile`: align the exposed port and health check with `frontend/nginx.conf`.
- Modify `docker-compose.yml`: add host gateway support for Linux deployments that point OCR URLs at `host.docker.internal`, and keep the public frontend port as `8080`.
- Create `docs/docker-deployment.md`: operator-focused deployment runbook for external OCR service URLs.
- Modify `README.md`: add a short Docker deployment entry that links to the runbook.

## Task 1: Align Frontend Container Port Metadata

**Files:**
- Modify: `frontend/Dockerfile`

- [ ] **Step 1: Update the exposed port**

Change the serve stage from:

```dockerfile
EXPOSE 80
```

to:

```dockerfile
EXPOSE 8080
```

`frontend/nginx.conf` already listens on `8080`, and the existing health check already calls `http://localhost:8080/`.

- [ ] **Step 2: Verify Dockerfile text**

Run:

```bash
rg -n "EXPOSE|HEALTHCHECK|localhost:8080" frontend/Dockerfile frontend/nginx.conf
```

Expected: `frontend/Dockerfile` exposes `8080`; both Dockerfile health check and Nginx listen path use `8080`.

- [ ] **Step 3: Commit**

Run:

```bash
git add frontend/Dockerfile
git commit -m "fix(docker): align frontend exposed port"
```

## Task 2: Make Host-Based External OCR URLs Work On Linux

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add host gateway mapping to backend**

Under the `backend` service, add:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

This lets operators set `PPOCRV5_URL=http://host.docker.internal:8866` and `PPSTRUCTURE_URL=http://host.docker.internal:8867` when OCR services run on the Docker host.

- [ ] **Step 2: Validate compose syntax**

Run:

```bash
docker compose config
```

Expected: command exits 0 and the rendered backend service contains `extra_hosts`.

- [ ] **Step 3: Commit**

Run:

```bash
git add docker-compose.yml
git commit -m "chore(docker): support host external ocr urls"
```

## Task 3: Add Docker Deployment Runbook

**Files:**
- Create: `docs/docker-deployment.md`
- Modify: `README.md`

- [ ] **Step 1: Create deployment runbook**

Create `docs/docker-deployment.md` with these sections:

```markdown
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
```

- [ ] **Step 2: Link the runbook from README**

Add a short section after the local development startup instructions:

```markdown
## Docker 部署

本项目支持 Docker Compose 部署前后端容器，OCR 和 PP-Structure 使用外部服务。详细步骤见 [Docker 部署指南](docs/docker-deployment.md)。

快速启动：

```bash
cp .env.example .env
docker compose up -d --build
```
```

- [ ] **Step 3: Validate markdown references**

Run:

```bash
rg -n "Docker 部署|docker compose|host.docker.internal" README.md docs/docker-deployment.md
```

Expected: README links to `docs/docker-deployment.md`, and the runbook includes startup and external OCR URL guidance.

- [ ] **Step 4: Commit**

Run:

```bash
git add README.md docs/docker-deployment.md
git commit -m "docs: add docker deployment runbook"
```

## Task 4: Final Verification

**Files:**
- No new code changes expected.

- [ ] **Step 1: Validate compose config**

Run:

```bash
docker compose config
```

Expected: exits 0.

- [ ] **Step 2: Build images**

Run:

```bash
docker compose build
```

Expected: backend and frontend images build successfully.

- [ ] **Step 3: Start services**

Run:

```bash
docker compose up -d
```

Expected: services start. If external OCR services are not running, container health can still pass because `/health` does not require OCR connectivity.

- [ ] **Step 4: Check health**

Run:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8080/health
```

Expected: both commands return a successful health response.

- [ ] **Step 5: Stop services**

Run:

```bash
docker compose down
```

Expected: containers stop and the `storage_data` volume remains.
