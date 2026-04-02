RecTax — Receipt OCR + Tax Extraction
=====================================

**Overview**:
- RecTax is a small stack for extracting totals/tax information from Japanese receipts using PaddleOCR and a FastAPI backend.
- Components: backend, Paddle OCR service (v3/v4), optional receipt-ocr helper, and a frontend.

**Quick Start (development)**:
- Copy example env and edit values (especially `HOST_DATA_DIR` and OCR settings):

```bash
cp .env.example .env
# edit .env then
cd /mnt/d/WSL2/AI/RecTax
# bring up services (GPU image used where available)
docker compose --env-file .env up -d --build
```

**Health & debug**:
- Service health (Paddle OCR v4):

```bash
curl -sS http://127.0.0.1:8012/health | jq .
```

- Tail paddle-ocr-v4 logs:

```bash
docker compose logs --tail=200 paddle-ocr-v4
```

- Inspect container cache directory (to see which model files are present):

```bash
docker compose exec paddle-ocr-v4 sh -c "ls -la /root/.paddleocr/whl/det || true"
```

**How OCR model selection works**:
- The running OCR process reads the env var `PADDLE_OCR_OCR_VERSION` (set via `PADDLE_OCR_V4_VERSION` in your `.env`) and attempts to initialize `PaddleOCR(..., ocr_version=...)`.
- Deployment wiring: see [docker-compose.yml](docker-compose.yml) where `paddle-ocr-v4` passes `PADDLE_OCR_OCR_VERSION: ${PADDLE_OCR_V4_VERSION}` and mounts `${HOST_DATA_DIR}/paddle-model-cache-v4` to `/root/.paddleocr` inside the container.
- If the exact server-infer package is not present in the cache, PaddleOCR may download missing components at startup; alternatively a default model bundled in the image may be used. This is why the service can report `ocr_version: PP-OCRv4` even when you also see `Multilingual_PP-OCRv3_det_infer` cached.

**Place server inference models locally**:
- To force use of local server inference packages, place the package directories under the host cache path (default `HOST_DATA_DIR` from `.env`):

  - Det examples: `paddle-model-cache-v4/whl/det/ml/ch_PP-OCRv4_det_server_infer`
  - Rec/Cls similar paths under `whl/rec` and `whl/cls` respectively.

- Example download+extract commands (run on the host / WSL):

```bash
cd /mnt/d/WSL2/AI/RecTax/paddle-model-cache-v4/whl/det/ml
# ch_PP-OCRv4 det server
curl -fSL -o ch_PP-OCRv4_det_server_infer.tgz \
  https://paddleocr.bj.bcebos.com/whl/det/ch_PP-OCRv4_det_server_infer.tgz
tar -xzf ch_PP-OCRv4_det_server_infer.tgz && rm ch_PP-OCRv4_det_server_infer.tgz

# Multilingual PP-OCRv3 det server
curl -fSL -o Multilingual_PP-OCRv3_det_infer.tgz \
  https://paddleocr.bj.bcebos.com/whl/det/ml/Multilingual_PP-OCRv3_det_infer.tgz
tar -xzf Multilingual_PP-OCRv3_det_infer.tgz && rm Multilingual_PP-OCRv3_det_infer.tgz
```

- Set the version variable in `.env` to the desired `ocr_version` label and restart the service. Example:

```bash
# in .env
PADDLE_OCR_V4_VERSION=ch_PP-OCRv4

# restart service
cd /mnt/d/WSL2/AI/RecTax
docker compose --env-file .env up -d --build paddle-ocr-v4
```

**Useful files**:
- Paddle OCR service entry: [paddle-ocr-service/app.py](paddle-ocr-service/app.py)
- Docker compose: [docker-compose.yml](docker-compose.yml)
- Example env: [.env.example](.env.example)

**Notes**:
- On WSL/Windows setups ensure `HOST_DATA_DIR` points to a path Docker can mount (this project uses `/mnt/d/WSL2/AI/RecTax` by default in `.env`).
- To inspect which exact inference files are used inside the container run:

```bash
docker compose exec paddle-ocr-v4 sh -c "find /root/.paddleocr/whl -type f -name inference.pdmodel -exec dirname {} \;"
```

**Contributing**
- Open issues or PRs for fixes or improvements.

**License**
- See repository root for licensing (add license file as needed).
