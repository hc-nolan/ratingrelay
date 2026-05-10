## Project Configuration

- **Language**: Python 3.13+
- **Package Manager**: uv
- **Framework**: FastAPI
- **Database**: SQLite via async SQLAlchemy / SQLModel + Alembic migrations
- **Entry point**: `app.py` → `ratingrelay/main.py`

---

## Running the Backend

```shell
uv sync
uv run alembic upgrade head   # required before first run and after new migrations
uv run app.py
```

Tests:

```shell
uv run pytest
```

---

## Project Layout

```
ratingrelay/
  main.py          # FastAPI app, lifespan, router registration, SPA fallback
  settings.py      # Pydantic-settings config; read via get_settings() / settings singleton
  core/
    thread.py      # run_sync() — wraps blocking calls for async contexts
    plex_auth.py   # In-memory PIN session store for Plex OAuth flow
  db/
    __init__.py    # Async engine, session_maker, get_async_session() dependency
    credentials.py # get_credential / upsert_credential helpers
  models/
    models.py      # Loves / Hates / Reset SQLModel tables
    services.py    # ServiceCredential table + ServiceName enum
  routes/
    plex.py        # /api/plex/* — OAuth PIN flow + server selection
    lastfm.py      # /api/lastfm/* — API-key + password auth
    listenbrainz.py# /api/listenbrainz/* — token auth
  schemas/
    *.py           # Pydantic request/response models (one file per service)
```

---

## Key Conventions

**Blocking I/O** — third-party clients (plexapi, pylast, liblistenbrainz) are synchronous. Always wrap them with `run_sync` from `ratingrelay.core.thread`:

```python
result = await run_sync(lambda: some_blocking_call())
```

**Database sessions** — inject via `Depends(get_async_session)`. Never create sessions manually in routes.

**Settings override hierarchy** — `.config` env file → environment variables. Settings values always take precedence over DB credentials (dev override pattern). When reading credentials in a route, always check `settings.*` first, then fall back to the DB row.

**Credential storage** — one `ServiceCredential` row per service (primary key: `ServiceName` enum). Use `get_credential` / `upsert_credential` from `ratingrelay.db.credentials`; never write raw SQL.

**Schemas** — keep request/response Pydantic models in `ratingrelay/schemas/`, one file per service. Do not reuse SQLModel table classes as API schemas.

**Migrations** — use Alembic. After changing a SQLModel table, generate a migration:

```shell
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

**Logging** — use the module-level `logger` from `ratingrelay.settings` (`logging.getLogger("ratingrelay")`). Do not call `logging.basicConfig` in library code.
