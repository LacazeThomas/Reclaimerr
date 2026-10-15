from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes.rules import check_synced_rules
from backend.database import Base
from backend.database.models import ServiceConfig, ServiceMediaLibrary
from backend.enums import MediaType, Service


@pytest.mark.parametrize(
    "media_types",
    [[MediaType.MOVIE], [MediaType.SERIES], [MediaType.MOVIE, MediaType.SERIES]],
)
@pytest.mark.parametrize(
    "synced,selected,enabled,is_main,expected",
    [
        (True, True, True, True, True),
        (False, True, True, True, False),
        (True, False, True, True, False),
        (True, True, False, True, False),
        (True, True, True, False, False),
    ],
)
def test_readiness_uses_main_sync_and_selected_libraries_even_when_empty(
    media_types, synced, selected, enabled, is_main, expected
):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            config = ServiceConfig(
                service_type=Service.JELLYFIN,
                base_url="http://example.invalid",
                api_key="synthetic",
                enabled=enabled,
                is_main=is_main,
                last_synced_at=datetime.now(UTC) if synced else None,
            )
            db.add(config)
            await db.flush()
            db.add_all(
                [
                    ServiceMediaLibrary(
                        library_id=kind.value,
                        library_name=kind.value,
                        media_type=kind,
                        service_config_id=config.id,
                        selected=selected,
                    )
                    for kind in media_types
                ]
            )
            await db.commit()
            result = await check_synced_rules(None, db)
            assert result["synced"] is expected
            assert result["movies"] == result["series"] == 0
        await engine.dispose()

    asyncio.run(run())
