import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import logging

from ratingrelay.routes.lastfm import router as lastfm_router
from ratingrelay.routes.listenbrainz import router as listenbrainz_router
from ratingrelay.routes.plex import router as plex_router
from ratingrelay.routes.relay import router as relay_router
from ratingrelay.routes.jobs import router as jobs_router
from ratingrelay.routes.unmatched import router as unmatched_router
from ratingrelay.core.worker import worker_loop
from ratingrelay.settings import get_settings

settings = get_settings()


def _run_migrations_sync() -> None:
    """Run alembic upgrade head (sync, intended to be called in a thread)."""
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")


async def run_migrations() -> None:
    """Run pending Alembic migrations from an async context."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_migrations_sync)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations()
    asyncio.create_task(worker_loop())
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(plex_router)
app.include_router(lastfm_router)
app.include_router(listenbrainz_router)
app.include_router(relay_router)
app.include_router(jobs_router)
app.include_router(unmatched_router)


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = Path(settings.frontend_dir) / full_path

    # If the exact file exists, serve it
    if file_path.is_file():
        return FileResponse(file_path)

    # Otherwise, serve index.html (SPA fallback)
    return FileResponse(Path(settings.frontend_dir) / "index.html")
