# Docker Compose External OCR Deployment Design

## Goal

Deploy the contract comparison application with Docker Compose as two local containers: FastAPI backend and React/Nginx frontend. OCR and PP-Structure services remain external dependencies configured through environment variables.

## Scope

In scope:

- Build and run `backend` and `frontend` with the existing Dockerfiles.
- Use the root `.env` file for backend runtime configuration.
- Keep task artifacts in a Docker volume mounted at `/data/storage`.
- Serve the UI through Nginx on host port `8080`.
- Proxy `/api/` and `/health` from the frontend container to `backend:8000`.
- Document build, start, health check, log, stop, and update commands.

Out of scope:

- Running PP-OCRV5 or PP-Structure inside this compose file.
- Adding PostgreSQL, Redis, or object storage.
- Adding CI/CD image publishing.
- Changing backend or frontend API behavior.

## Architecture

`docker-compose.yml` defines two services:

- `backend`: builds from `backend/Dockerfile`, runs `uvicorn app.main:app`, loads root `.env`, listens on container port `8000`, and stores generated task files under `/data/storage`.
- `frontend`: builds from `frontend/Dockerfile`, builds the Vite app with same-origin API requests, serves static files with Nginx, and proxies backend requests over the compose network.

The only public user entrypoint is:

```text
http://<host>:8080
```

Direct backend access on `http://<host>:8000` remains available for API docs and diagnostics.

## Runtime Configuration

Operators copy `.env.example` to `.env` at the repository root and set at least:

```env
PPOCRV5_URL=http://<external-ocr-host>:8866
PPSTRUCTURE_URL=http://<external-structure-host>:8867
```

When those services run on the Docker host, Linux deployments should use the host LAN IP or configure `host.docker.internal` support. `127.0.0.1` inside the backend container points to the container itself, not the host.

## Validation

Deployment is considered working when:

- `docker compose up -d --build` completes.
- `docker compose ps` shows both services healthy or running.
- `curl http://127.0.0.1:8080/health` returns the backend health response through Nginx.
- `curl http://127.0.0.1:8000/health` returns the backend health response directly.
- Browser access to `http://127.0.0.1:8080` loads the React application.

Full contract comparison additionally requires the external OCR and PP-Structure URLs to be reachable from the backend container.
