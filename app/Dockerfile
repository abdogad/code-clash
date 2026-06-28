# syntax=docker/dockerfile:1
#
# Multi-stage: the box builds the React SPA itself (no committed bundle, no CI
# node step), then bakes it into the FastAPI image. Build context is the repo
# root (see docker-compose.yml: build.context = .) so both app/ and frontend/
# are reachable here.

# --- stage 1: build the React SPA -------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /build
# deps first so this layer caches unless the lockfile changes
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Override outDir to a local path so the image build doesn't depend on the host
# layout (vite.config points it at ../app/static for host dev builds).
RUN npm run build -- --outDir dist --emptyOutDir

# --- stage 2: the FastAPI app -----------------------------------------------
FROM python:3.12-slim
WORKDIR /srv
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
# Schema migrations + seed, applied at startup by codeclash/migrate.py (DB_DIR
# defaults to /srv/db).
COPY db/migrations ./db/migrations
COPY db/seed ./db/seed
# served by StaticFiles(directory="static", html=True) in codeclash/main.py
COPY --from=frontend /build/dist ./static
CMD ["uvicorn", "codeclash.main:app", "--host", "0.0.0.0", "--port", "8000"]
