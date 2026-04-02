# RecTax Cloud Deployment Reference

This document describes a production-oriented deployment flow using:

- `docker-compose.yml` (shared base)
- `docker-compose.override.yml` (development override)
- `docker-compose.prod.yml` (production override)
- `.env` (current values)
- `.env.example` (safe placeholder template)

## 1. Server prerequisites

- Linux server with Docker Engine and Docker Compose plugin
- NVIDIA driver + NVIDIA Container Toolkit (for GPU OCR)
- Open ports in security group/firewall:
  - `80/tcp` for frontend
  - Optional: if reverse proxy is used, only expose proxy ports

Quick checks:

```bash
docker --version
docker compose version
nvidia-smi
```

## 2. Prepare project files

Place project in a server directory, for example:

```bash
mkdir -p /data/rectax
cd /data/rectax
```

Copy source code into this directory.

## 3. Prepare environment variables

Use `.env.example` as template and create `.env` for the server.

Minimum values to change before production:

- `POSTGRES_PASSWORD`
- `DATABASE_URL` (must match DB credentials)
- `JWT_SECRET_KEY`
- `BOOTSTRAP_ADMIN_PASSWORD`
- `ALLOWED_ORIGINS_JSON`
- `ALLOWED_ORIGIN_REGEX`
- `HOST_DATA_DIR` (e.g. `/data/rectax`)
- Optional `OPENAI_API_KEY` if fallback service is enabled

Notes:

- Keep `.env` out of source control.
- For multi-domain deployment, include all domains in CORS settings.

Upload protection variables:

- `MAX_FILE_SIZE`: hard per-file limit in bytes.
- `UPLOAD_RATE_LIMIT_PER_MINUTE`: per-user upload request cap per 60 seconds.
- `UPLOAD_MAX_FILES_PER_REQUEST`: max files accepted in one multipart upload.
- `UPLOAD_MAX_TOTAL_MB_PER_REQUEST`: max combined payload size per upload request.

Recommended upload-protection profiles:

- Conservative: `MAX_FILE_SIZE=8388608`, `UPLOAD_RATE_LIMIT_PER_MINUTE=4`, `UPLOAD_MAX_FILES_PER_REQUEST=5`, `UPLOAD_MAX_TOTAL_MB_PER_REQUEST=25`
- Balanced: `MAX_FILE_SIZE=10485760`, `UPLOAD_RATE_LIMIT_PER_MINUTE=10`, `UPLOAD_MAX_FILES_PER_REQUEST=20`, `UPLOAD_MAX_TOTAL_MB_PER_REQUEST=100`
- Aggressive: `MAX_FILE_SIZE=15728640`, `UPLOAD_RATE_LIMIT_PER_MINUTE=20`, `UPLOAD_MAX_FILES_PER_REQUEST=40`, `UPLOAD_MAX_TOTAL_MB_PER_REQUEST=250`

Operational guidance:

- Start with the Conservative profile for new customers or single-node deployments.
- Move to Balanced only after observing backend/OCR latency, CPU, GPU memory, and queue behavior.
- Use Aggressive only when backend and OCR containers have confirmed spare headroom and monitoring is in place.

## 4. Prepare runtime directories

Create mount targets:

```bash
mkdir -p ./uploads ./exports
mkdir -p ./paddle-model-cache ./paddle-model-cache-v4
```

If `HOST_DATA_DIR` points to a different root, create equivalent paths under that root.

## 5. Start services (production template)

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

This template uses:

- Public frontend port mapping: `${FRONTEND_PORT}:80`
- Loopback-only service mappings (safer default):
  - `127.0.0.1:${BACKEND_PORT}:8000`
  - `127.0.0.1:${DB_PORT}:5432`
  - `127.0.0.1:${OCR_V3_PORT}:8000`
  - `127.0.0.1:${OCR_V4_PORT}:8000`
  - `127.0.0.1:${RECEIPT_OCR_PORT}:8000`

## 6. Verify deployment

Check compose status:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml ps
```

Health checks from server:

```bash
curl -sS http://127.0.0.1:${BACKEND_PORT}/health
curl -sS http://127.0.0.1:${OCR_V3_PORT}/health
curl -sS http://127.0.0.1:${OCR_V4_PORT}/health
curl -I  http://127.0.0.1:${FRONTEND_PORT}
```

GPU check in OCR container:

```bash
docker exec rectax-paddle-ocr-v4 nvidia-smi || true
docker exec rectax-paddle-ocr-v4 python3 -c "import paddle; print('compiled_with_cuda=', paddle.device.is_compiled_with_cuda())"
```

## 7. Optional fallback OCR service

The fallback service `receipt-ocr` is under profile `fallback`.

Start with fallback profile:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml --profile fallback up -d --build
```

## 8. Updates and rollback

Update workflow:

```bash
git pull
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

If only backend code or backend environment variables changed, rebuild just the backend container:

```bash
docker compose --env-file .env up -d --build backend
```

If needed, roll back by checking out previous commit/tag and redeploying with the same command.

## 9. Security checklist

- Use strong random secrets for JWT and DB passwords.
- Restrict SSH source IPs.
- Use HTTPS (reverse proxy + certificates).
- Keep non-frontend ports loopback-only unless explicitly required.
- Regularly patch host OS, Docker, NVIDIA driver, and base images.

## 10. Recommended operations

- Configure log rotation for Docker daemon.
- Add nightly DB backups.
- Add uptime and health monitoring for backend and OCR services.
- Add alerting for container restart loops and GPU memory saturation.

## 11. Development run command

Use the development override to preserve current local behavior:

```bash
docker compose --env-file .env up -d --build
```

`docker compose` automatically loads `docker-compose.yml` and `docker-compose.override.yml` in development.
