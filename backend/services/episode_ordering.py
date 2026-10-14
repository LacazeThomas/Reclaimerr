"""Read-only checks for the numbering used by TV integrations."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import (
    SeriesArrRef,
    SeriesServiceRef,
    ServiceConfig,
    SupplementalMediaMatch,
)
from backend.enums import MediaType
from backend.services.media_identity import load_media_identity_ownership


def normalize_episode_order(value: object) -> str | None:
    """Only recognize identifiers whose numbering meaning is established."""
    if not isinstance(value, str):
        return None
    return {
        "official": "aired",
        "aired": "aired",
        "tmdbairing": "aired",
        "originalairdate": "aired",
        "dvd": "dvd",
        "absolute": "absolute",
    }.get(value.strip().lower())


def ordering_error(sonarr: str | None, media: str | None) -> str | None:
    if sonarr is not None and sonarr == media:
        return None
    return (
        (
            "Episode ordering could not be verified"
            if sonarr is None or media is None
            else "Episode ordering does not match"
        )
        + f" (Sonarr: {sonarr or 'unknown'}, media server: {media or 'unknown'}). "
        "Set both servers to the same supported episode order and run Sync Media."
    )


class EpisodeOrderingSnapshot:
    """Cache read-only metadata within one operation, never across executions."""

    def __init__(self) -> None:
        self._orders: dict[tuple[int, str], str | None] = {}
        self._primary: dict[int, str | None] = {}
        self._libraries: dict[int, dict[str, str | None]] = {}

    async def media_order(self, client: Any, item_id: str) -> str | None:
        key = (id(client), str(item_id))
        if key not in self._orders:
            try:
                self._orders[key] = normalize_episode_order(
                    await asyncio.wait_for(
                        client.get_series_episode_order(
                            str(item_id),
                            library_orders=self._libraries.setdefault(id(client), {}),
                        ),
                        timeout=15,
                    )
                )
            except Exception:
                self._orders[key] = None
        return self._orders[key]

    async def sonarr_order(
        self, client: Any, item_id: int, *, series: Any = None
    ) -> str | None:
        key = (id(client), str(item_id))
        if key not in self._orders:
            try:
                self._orders[key] = normalize_episode_order(
                    await asyncio.wait_for(
                        client.get_series_episode_order(item_id, series=series),
                        timeout=15,
                    )
                )
            except Exception:
                self._orders[key] = None
        return self._orders[key]

    async def preload_primary(self, db: AsyncSession, series_ids: set[int]) -> None:
        from backend.core.service_manager import service_manager

        missing = series_ids.difference(self._primary)
        if not missing:
            return
        ownership = await load_media_identity_ownership(db)
        refs = list(
            (
                await db.scalars(
                    select(SeriesServiceRef).where(
                        SeriesServiceRef.series_id.in_(missing),
                        SeriesServiceRef.service == ownership.main_service,
                    )
                )
            ).all()
        )
        client = (
            service_manager.get_media_server(
                ownership.main_service, ownership.main_config_id
            )
            if ownership.main_service is not None
            else None
        )
        self._primary.update(dict.fromkeys(missing))
        semaphore = asyncio.Semaphore(8)

        async def load(ref: SeriesServiceRef) -> None:
            async with semaphore:
                self._primary[ref.series_id] = await self.media_order(
                    client, ref.service_id
                )

        if client is not None:
            await asyncio.gather(*(load(ref) for ref in refs))

    async def primary_order(self, db: AsyncSession, series_id: int) -> str | None:
        await self.preload_primary(db, {series_id})
        return self._primary[series_id]

    async def linked_order(
        self, db: AsyncSession, series_id: int, config_id: int
    ) -> str | None:
        from backend.core.service_manager import service_manager

        row = (
            await db.execute(
                select(SupplementalMediaMatch, ServiceConfig)
                .join(
                    ServiceConfig,
                    SupplementalMediaMatch.source_service_config_id == ServiceConfig.id,
                )
                .where(
                    SupplementalMediaMatch.series_id == series_id,
                    SupplementalMediaMatch.source_service_config_id == config_id,
                    SupplementalMediaMatch.season_id.is_(None),
                    SupplementalMediaMatch.episode_id.is_(None),
                    SupplementalMediaMatch.media_type == MediaType.SERIES,
                )
            )
        ).first()
        if row is None:
            return None
        match, config = row
        client = service_manager.get_media_server(config.service_type, config.id)
        return await self.media_order(client, match.source_item_id) if client else None

    async def check_series(
        self,
        db: AsyncSession,
        series_id: int,
        allowed_config_ids: set[int] | None = None,
    ) -> str | None:
        from backend.core.service_manager import service_manager

        refs = list(
            (
                await db.scalars(
                    select(SeriesArrRef)
                    .join(
                        ServiceConfig,
                        SeriesArrRef.service_config_id == ServiceConfig.id,
                    )
                    .where(
                        SeriesArrRef.series_id == series_id,
                        ServiceConfig.enabled.is_(True),
                    )
                )
            ).all()
        )
        if allowed_config_ids is not None:
            refs = [r for r in refs if r.service_config_id in allowed_config_ids]
        if not refs:
            return None
        media = await self.primary_order(db, series_id)
        for ref in refs:
            client = service_manager.get_sonarr(ref.service_config_id)
            order = (
                await self.sonarr_order(client, ref.arr_series_id) if client else None
            )
            error = ordering_error(order, media)
            if error:
                return f"Sonarr instance {ref.service_config_id}: {error}"
        linked = (
            await db.execute(
                select(SupplementalMediaMatch, ServiceConfig)
                .join(
                    ServiceConfig,
                    SupplementalMediaMatch.source_service_config_id == ServiceConfig.id,
                )
                .where(
                    SupplementalMediaMatch.series_id == series_id,
                    SupplementalMediaMatch.media_type == MediaType.SERIES,
                    SupplementalMediaMatch.season_id.is_(None),
                    SupplementalMediaMatch.episode_id.is_(None),
                    ServiceConfig.enabled.is_(True),
                )
            )
        ).all()
        for match, config in linked:
            client = service_manager.get_media_server(config.service_type, config.id)
            linked_order = (
                await self.media_order(client, match.source_item_id) if client else None
            )
            error = ordering_error(linked_order, media)
            if error:
                return f"Linked media server {config.id}: {error.replace('Sonarr:', 'linked server:')}"
        return None
