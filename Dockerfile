# ------------------------------------------------------------------
# Stage 1: Build the Vite/Vue frontend bundle.
# Node is only needed here; the final image has no Node at all.
# ------------------------------------------------------------------
FROM node:20-slim AS frontend-builder

WORKDIR /build

# Install deps first for better layer caching
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend

# Bring in the rest of the frontend + shared locales and build
COPY frontend ./frontend
COPY locales ./locales
RUN npm run build --prefix frontend \
  && rm -rf frontend/node_modules


# ------------------------------------------------------------------
# Stage 2: Python runtime. Only ships backend deps + built static assets.
# ------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# uv for Python deps
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Install backend deps
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend \
  && UV_LINK_MODE=copy uv sync --no-dev \
  && rm -rf /root/.cache /tmp/* /var/tmp/*

# Copy backend source
COPY backend ./backend

# Copy only the built frontend assets from stage 1 (no node_modules)
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

# Runtime-loaded translation files (backend reads ../../../locales/*.json)
COPY locales ./locales

# Static assets referenced by the upstream README — keep for completeness
COPY static ./static

# Railway injects $PORT; default 3000 for local docker runs
ENV PORT=3000
EXPOSE 3000

# Single process: Flask serves both the API and the built SPA on $PORT
CMD ["sh", "-c", "cd backend && uv run --no-dev python run.py"]
