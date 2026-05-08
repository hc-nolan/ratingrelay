from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from plexapi.library import LibrarySection
from plexapi.audio import Track as PlexTrack

from ratingrelay.settings import logger

from ratingrelay.db import get_async_session

router = APIRouter(prefix="/api/plex", tags=["ingredient"])


@router.post("auth")
async def auth():
    return
