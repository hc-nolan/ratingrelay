import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import FileResponse


from ratingrelay.core.session import session_guard
from ratingrelay.routes.auth import router as auth_router
from ratingrelay.routes.lastfm import router as lastfm_router
from ratingrelay.routes.listenbrainz import router as listenbrainz_router
from ratingrelay.routes.plex import router as plex_router
from ratingrelay.routes.relay import router as relay_router
from ratingrelay.routes.jobs import router as jobs_router
from ratingrelay.routes.services import router as services_router
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
async def lifespan(_app: FastAPI):
    await run_migrations()
    asyncio.create_task(worker_loop())
    yield


app = FastAPI(lifespan=lifespan)
app.middleware("http")(session_guard())


app.include_router(auth_router)
app.include_router(plex_router)
app.include_router(lastfm_router)
app.include_router(listenbrainz_router)
app.include_router(relay_router)
app.include_router(jobs_router)
app.include_router(unmatched_router)
app.include_router(services_router)


def spa_file(frontend_dir: str, full_path: str) -> Path:
    """The file to serve for *full_path*: itself if it is in the bundle, else index.html.

    The path is resolved before it is checked, so ``..`` (raw or percent-encoded)
    cannot climb out of the bundle. This route is outside ``/api`` and so outside
    the session guard, and the database and environment are one directory away.
    """
    root = Path(frontend_dir).resolve()
    candidate = (root / full_path).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate
    return root / "index.html"


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    return FileResponse(spa_file(settings.frontend_dir, full_path))
