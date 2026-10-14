import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import require_admin
from backend.core.logger import LOG
from backend.core.utils.login_background import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_BYTES,
    background_response,
    delete_background,
    save_background,
)
from backend.database import get_db
from backend.database.models import GeneralSettings, User
from backend.models.login_background import (
    LoginBackgroundResponse,
    LoginBackgroundUpdate,
)

router = APIRouter(prefix="/general/login-background", tags=["settings", "general"])


async def get_row(db: AsyncSession) -> GeneralSettings:
    row = (await db.execute(select(GeneralSettings))).scalars().first()
    if row is None:
        row = GeneralSettings()
        db.add(row)
        await db.flush()
    return row


async def commit(row: GeneralSettings, admin: User, db: AsyncSession) -> None:
    row.updated_at = datetime.now(UTC)
    row.updated_by_user_id = admin.id
    await db.commit()


def cleanup(filename: str | None) -> None:
    try:
        delete_background(filename)
    except OSError:
        LOG.warning("Could not remove old login background file")


@router.get("")
async def get_settings(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginBackgroundResponse:
    row = (await db.execute(select(GeneralSettings))).scalars().first()
    return background_response(row)


@router.put("")
async def update_settings(
    request: LoginBackgroundUpdate,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginBackgroundResponse:
    row = await get_row(db)
    if request.mode == "upload" and not row.login_background_path:
        raise HTTPException(400, "Upload a background image first")
    row.login_background_mode = request.mode
    row.login_background_urls = request.urls
    row.login_background_interval = request.interval_seconds
    await commit(row, admin, db)
    return background_response(row)


@router.post("/image")
async def upload_image(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    image: UploadFile = File(...),
) -> LoginBackgroundResponse:
    if image.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, "Use a JPEG, PNG, or WebP image")
    contents = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image must be 10 MB or smaller")
    row = await get_row(db)
    try:
        filename = await asyncio.to_thread(save_background, contents)
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            400,
            "Could not read that image; use a valid JPEG, PNG, or WebP up to 25 million pixels",
        ) from exc
    old_filename = row.login_background_path
    row.login_background_path = filename
    try:
        await commit(row, admin, db)
    except Exception:
        cleanup(filename)
        raise
    cleanup(old_filename)
    return background_response(row)


@router.delete("/image")
async def remove_image(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginBackgroundResponse:
    row = await get_row(db)
    old_filename = row.login_background_path
    row.login_background_path = None
    if row.login_background_mode == "upload":
        row.login_background_mode = "default"
    await commit(row, admin, db)
    cleanup(old_filename)
    return background_response(row)
