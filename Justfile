# RatingRelay developer tasks. Run `just` to list them.
#
# The API port comes from .config (copy .config.example), so `just dev` and
# `just api` always agree with the OAuth callback in `base_url`.

set shell := ["bash", "-uc"]

# API port read from .config / environment. Read inside the recipes that need it,
# not as a top-level backtick: just evaluates those on every invocation, so
# `just check` would need uv (and CI's frontend job has none).
port_cmd := 'uv run python -c "from ratingrelay.settings import get_settings; print(get_settings().port)"'

# Install backend and frontend dependencies.
install:
    uv sync
    cd frontend && pnpm install

# Run the API with autoreload.
api:
    uv run app.py

# Run the SvelteKit dev server on :5173, proxying /api to the API.
web:
    port="$({{port_cmd}})" && cd frontend && BACKEND_URL="http://localhost:$port" pnpm dev

# Run the API and the dev server together. Ctrl-C stops both.
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT INT TERM
    # Before the cd: settings read .config from the working directory.
    port="$({{port_cmd}})"
    uv run app.py &
    (cd frontend && BACKEND_URL="http://localhost:$port" pnpm dev) &
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

# Frontend typecheck and lint.
check:
    cd frontend && pnpm check
    cd frontend && pnpm lint

# Build the frontend bundle that FastAPI serves.
build:
    cd frontend && pnpm build

# Apply database migrations.
migrate:
    uv run alembic upgrade head

# Demo: API on :8199 with a seeded throwaway database, Vite on :5174. No real services are called.
demo:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT INT TERM
    uv run python scripts/demo.py &
    (cd frontend && BACKEND_URL="http://localhost:8199" pnpm exec vite dev --port 5174 --strictPort) &
    wait

