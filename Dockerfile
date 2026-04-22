FROM python:3.11-slim

# Install Node.js 20 (meets >=18) and minimal tooling
RUN apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
  && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

# Copy uv from the official image
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first (better layer caching)
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY backend/pyproject.toml backend/uv.lock ./backend/

# Install dependencies (Node + Python)
RUN npm ci \
  && npm ci --prefix frontend \
  && cd backend && uv sync --frozen

# Copy the rest of the source
COPY . .

# Build the frontend once at image-build time
RUN npm run build --prefix frontend

# Railway will inject $PORT; default 3000 for local runs
ENV PORT=3000
EXPOSE 3000 5001

# Start backend (5001) + vite preview serving built assets on $PORT
CMD ["npm", "run", "start"]
