from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ratingrelay.settings import settings
from ratingrelay.models.models import Loves, Hates, Reset
from ratingrelay.models.services import ServiceCredential  # noqa: F401 — ensure table is registered
import ratingrelay.models.jobs  # noqa: F401 — register Job and TrackEvent tables
import ratingrelay.models.sync  # noqa: F401 — register SyncRecord table
import ratingrelay.models.mappings  # noqa: F401 — register TrackMapping table


SQLITE_URL = f"sqlite+aiosqlite:///{settings.database}"
engine = create_async_engine(SQLITE_URL, echo=False)
session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_maker() as session:
        yield session
