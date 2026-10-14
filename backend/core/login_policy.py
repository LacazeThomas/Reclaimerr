from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.settings import settings
from backend.database.models import OIDCSettings
from backend.models.auth import LoginMethodsResponse
from backend.services.media_auth import list_media_auth_providers

LoginMethod = Literal["local", "media", "sso"]


async def get_login_methods(db: AsyncSession) -> LoginMethodsResponse:
    row = (
        await db.execute(
            select(
                OIDCSettings.allow_local_login,
                OIDCSettings.allow_media_login,
                OIDCSettings.allow_sso_login,
            )
        )
    ).first()
    return LoginMethodsResponse(
        local_enabled=settings.auth_force_local_login
        or row is None
        or row.allow_local_login,
        media_enabled=row is None or row.allow_media_login,
        sso_enabled=row is None or row.allow_sso_login,
        local_recovery_active=settings.auth_force_local_login,
    )


async def require_login_method(db: AsyncSession, method: LoginMethod) -> None:
    methods = await get_login_methods(db)
    if not getattr(methods, f"{method}_enabled"):
        label = {"local": "Local", "media": "Media", "sso": "SSO"}[method]
        raise HTTPException(
            status_code=403, detail=f"{label} login is disabled by the administrator"
        )


async def validate_login_methods(
    db: AsyncSession,
    *,
    allow_local: bool,
    allow_media: bool,
    allow_sso: bool,
    sso_configured: bool,
    excluded_media_id: int | None = None,
) -> None:
    # Validate the saved policy, not the temporary environment override. Removing
    # the recovery flag must never leave a policy saved with no login methods.
    if allow_local:
        return
    if allow_sso and sso_configured:
        return
    if allow_media:
        providers = await list_media_auth_providers(db)
        if any(
            provider.service_config_id != excluded_media_id for provider in providers
        ):
            return
    raise HTTPException(
        status_code=400,
        detail="Keep at least one configured login method enabled. Enable Local login, "
        "configure and allow SSO, or enable and allow a media server login.",
    )


async def validate_saved_login_methods(
    db: AsyncSession, *, excluded_media_id: int | None = None
) -> None:
    row = (await db.execute(select(OIDCSettings))).scalars().first()
    if row is None:
        return
    await validate_login_methods(
        db,
        allow_local=row.allow_local_login,
        allow_media=row.allow_media_login,
        allow_sso=row.allow_sso_login,
        sso_configured=bool(
            row.enabled and row.issuer_url and row.client_id and row.client_secret
        ),
        excluded_media_id=excluded_media_id,
    )
