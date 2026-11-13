# RatingRelay developer tasks. Run `just` to list them.
#
# The API port comes from .config (copy .config.example), so `just dev` and
# `just api` always agree with the OAuth callback in `base_url`.

set shell := ["bash", "-uc"]

# API port read from .config / environment.
api_port := `uv run python -c "from ratingrelay.settings import get_settings; print(get_settings().port)"`

# Install backend and frontend dependencies.
install:
    uv sync
    cd frontend && pnpm install

# Run the API with autoreload.
api:
    uv run app.py

# Run the SvelteKit dev server on :5173, proxying /api to the API.
web:
    cd frontend && BACKEND_URL="http://localhost:{{api_port}}" pnpm dev

# Run the API and the dev server together. Ctrl-C stops both.
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT INT TERM
    uv run app.py &
    (cd frontend && BACKEND_URL="http://localhost:{{api_port}}" pnpm dev) &
    wait

# Backend tests.
test *args:
    uv run pytest {{args}}

# Backend lint.
lint:
    uv run pylint ratingrelay

# Headless: run the relays in relays.toml (cron's entry point).
relay *args:
    uv run ratingrelay run {{args}}

# Headless: the relays a config file defines, without running them.
relays:
    uv run ratingrelay list

# Headless dry run: the real pipeline, no writes. The authority on what a run does.
relay-check *args:
    uv run ratingrelay relay --target lastfm --target listenbrainz --dry-run {{args}}

# Frontend typecheck and lint. eslint is still red: see docs/plans on the v3 baseline.
check:
    cd frontend && pnpm check
    cd frontend && pnpm lint

# Build the frontend bundle that FastAPI serves.
build:
    cd frontend && pnpm build

# Apply database migrations.
migrate:
    uv run alembic upgrade head
