# RatingRelay: the web app and the headless CLI in one image.
#
#   docker build -t ratingrelay .
#   docker run -p 8100:8100 -v ratingrelay-data:/data -e SECRET=... ratingrelay
#
# compose.yaml is the supported way to run it. Everything the app keeps (the
# database, and relays.toml if you use the CLI) lives in /data.

# --- Frontend: build the SPA the API serves ----------------------------------
FROM node:24-slim AS frontend
RUN npm install --global pnpm@11
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# --- Backend: resolve the locked environment ---------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS backend
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-cache
COPY README.md alembic.ini app.py ./
COPY ratingrelay/ ./ratingrelay/
RUN uv sync --locked --no-cache

# --- Runtime -----------------------------------------------------------------
# Must match the builder's Python: the venv points at its interpreter path.
FROM python:3.13-slim-trixie
RUN groupadd --system --gid 1000 ratingrelay \
 && useradd --system --gid 1000 --uid 1000 --home-dir /app ratingrelay \
 && mkdir /data && chown ratingrelay:ratingrelay /data

COPY --from=backend --chown=ratingrelay:ratingrelay /app /app
COPY --from=frontend --chown=ratingrelay:ratingrelay /frontend/build /app/frontend/build

# Settings are read from the environment (see .config.example for every key).
# These are the container's defaults; compose.yaml sets the rest.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8100 \
    RELOAD=false \
    DATABASE=/data/ratingrelay.db \
    FRONTEND_DIR=/app/frontend/build

USER ratingrelay
WORKDIR /app
VOLUME /data
EXPOSE 8100

# /api/auth/status is open without a session, so it answers before login too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/api/auth/status', timeout=4)"]

# Migrations run on startup, so a new image upgrades the database in /data.
CMD ["python", "app.py"]
