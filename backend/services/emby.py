from __future__ import annotations

import asyncio
import re
from typing import Any

from niquests.exceptions import HTTPError
from sqlalchemy import select

from backend.database import async_db
from backend.database.models import EmbyHomeSection
from backend.enums import Service
from backend.utils.helpers import LeavingSoonVisibility

from .emby_base import EmbyServiceBase, JsonDict


class EmbyService(EmbyServiceBase):
    """Emby media server backend, including opt-in managed home sections."""

    def __init__(
        self, api_key: str, base_url: str, config_id: int | None = None
    ) -> None:
        super().__init__(
            api_key=api_key,
            service_url=base_url,
            service_type=Service.EMBY,
        )
        self.config_id = config_id
        self._home_lock = asyncio.Lock()

    async def _owned_home_sections(self) -> list[EmbyHomeSection]:
        if self.config_id is None:
            return []
        async with async_db() as db:
            return list(
                (
                    await db.scalars(
                        select(EmbyHomeSection).where(
                            EmbyHomeSection.service_config_id == self.config_id
                        )
                    )
                ).all()
            )

    async def _save_home_section(self, row: EmbyHomeSection) -> None:
        async with async_db() as db:
            await db.merge(row)
            await db.commit()

    async def _forget_home_section(self, row: EmbyHomeSection) -> None:
        async with async_db() as db:
            stored = await db.get(EmbyHomeSection, row.id)
            if stored is not None:
                await db.delete(stored)
                await db.commit()

    async def _home_sections(self, user_id: str) -> list[JsonDict]:
        result = await self._make_request(
            f"Users/{user_id}/HomeSections", params={"IsEditMode": "true"}, timeout=30
        )
        if not isinstance(result, list):
            raise ValueError("Emby returned an invalid home-section list")
        return result

    async def _post_home_section(self, user_id: str, section: JsonDict) -> None:
        response = await self.session.post(
            f"{self.service_url}/Users/{user_id}/HomeSections",
            json=section,
            timeout=30,
        )
        response.raise_for_status()

    async def _remove_home_section(self, row: EmbyHomeSection) -> None:
        response = await self.session.post(
            f"{self.service_url}/Users/{row.user_id}/HomeSections/Delete",
            json={"Ids": [row.section_id]},
            timeout=30,
        )
        # A deleted Emby user/section is already absent. Other failures must
        # stop collection removal, otherwise an orphaned row can show all media.
        if response.status_code != 404:
            response.raise_for_status()
        await self._forget_home_section(row)

    async def _remove_collection_home_sections(self, collection_id: str) -> None:
        for row in await self._owned_home_sections():
            if row.collection_id == collection_id:
                await self._remove_home_section(row)

    async def _delete_collection(self, collection_id: str) -> None:
        await self._remove_collection_home_sections(collection_id)
        await super()._delete_collection(collection_id)

    async def _remove_items_from_collection(
        self, *, collection_id: str, item_ids: set[str]
    ) -> None:
        owned = await self._owned_home_sections()
        if any(row.collection_id == collection_id for row in owned):
            current_ids = await self._get_collection_item_ids(
                collection_id=collection_id, include_item_types="Movie,Series"
            )
            if not current_ids - item_ids:
                await self._remove_collection_home_sections(collection_id)
        await super()._remove_items_from_collection(
            collection_id=collection_id, item_ids=item_ids
        )

    async def prune_leaving_soon_items(self, **kwargs: Any) -> None:
        async with self._home_lock:
            await super().prune_leaving_soon_items(**kwargs)

    async def sync_leaving_soon_collections(
        self,
        *,
        collection_visibility: LeavingSoonVisibility = LeavingSoonVisibility(),
        **kwargs: Any,
    ) -> None:
        async with self._home_lock:
            if collection_visibility.emby_home:
                if self.config_id is None:
                    raise ValueError(
                        "Emby home sections require a saved server configuration"
                    )
                info = await self._make_request("System/Info", timeout=30)
                version = str(info.get("Version", "")) if isinstance(info, dict) else ""
                parts = tuple(int(p) for p in re.findall(r"\d+", version)[:4])
                if parts < (4, 10, 0, 4):
                    raise ValueError(
                        "Leaving Soon home sections require Emby Server 4.10 or newer"
                    )
            else:
                # Opting out only removes rows whose Emby-assigned IDs we own.
                # Never read HomeSections on older servers when no rows exist.
                for row in await self._owned_home_sections():
                    kind_title = kwargs.get(
                        "movie_title" if row.kind == "movies" else "series_title"
                    )
                    if kind_title:
                        await self._remove_home_section(row)
            await super().sync_leaving_soon_collections(
                collection_visibility=collection_visibility, **kwargs
            )
            if collection_visibility.emby_home:
                await self._sync_home_sections(
                    movie_title=kwargs.get("movie_title"),
                    series_title=kwargs.get("series_title"),
                )

    async def _visible_collection(
        self, user_id: str, collection_id: str, item_type: str
    ) -> bool:
        try:
            item = await self._make_request(
                f"Users/{user_id}/Items/{collection_id}", timeout=30
            )
            if not isinstance(item, dict) or item.get("Type") != "BoxSet":
                return False
            result = await self._make_request(
                f"Users/{user_id}/Items",
                params={
                    "ParentId": collection_id,
                    "IncludeItemTypes": item_type,
                    "Recursive": "true",
                    "Limit": 1,
                },
                timeout=30,
            )
            return isinstance(result, dict) and bool(result.get("Items"))
        except HTTPError as exc:
            if exc.response is not None and exc.response.status_code in {403, 404}:
                return False
            raise

    async def _sync_home_sections(
        self, *, movie_title: str | None, series_title: str | None
    ) -> None:
        assert self.config_id is not None
        users = await self._make_request("Users", timeout=30)
        if not isinstance(users, list):
            raise ValueError("Emby returned an invalid user list")
        owned = await self._owned_home_sections()
        user_ids = {str(user["Id"]) for user in users}
        for row in owned:
            if row.user_id not in user_ids:
                await self._forget_home_section(row)
        for kind, title, item_type in (
            ("movies", movie_title, "Movie"),
            ("series", series_title, "Series"),
        ):
            if not title:
                continue
            collection_ids = await self._find_collection_ids_by_title(title)
            collection_id = collection_ids[0] if collection_ids else None
            for user in users:
                user_id = str(user["Id"])
                row = next(
                    (r for r in owned if r.user_id == user_id and r.kind == kind), None
                )
                visible = (
                    not user.get("Policy", {}).get("IsDisabled", False)
                    and collection_id is not None
                    and await self._visible_collection(
                        user_id, collection_id, item_type
                    )
                )
                if not visible:
                    if row is not None:
                        await self._remove_home_section(row)
                    continue
                assert collection_id is not None
                sections = await self._home_sections(user_id)
                current = next(
                    (
                        s
                        for s in sections
                        if row is not None and s.get("Id") == row.section_id
                    ),
                    None,
                )
                section = dict(current) if current is not None else {}
                # Keep the user's position and presentation preferences when
                # updating an existing row, but always repair its collection link.
                section.update(
                    {
                        "SectionType": "boxset",
                        "CustomName": title,
                        "ParentId": collection_id,
                    }
                )
                section.pop("ParentItem", None)
                if current is None:
                    section.pop("Id", None)
                if current is None or any(
                    current.get(k) != section[k]
                    for k in ("SectionType", "CustomName", "ParentId")
                ):
                    await self._post_home_section(user_id, section)
                if current is None:
                    before_ids = {s["Id"] for s in sections}
                    created = [
                        s
                        for s in await self._home_sections(user_id)
                        if s.get("Id") not in before_ids
                        and s.get("ParentId") == collection_id
                        and s.get("SectionType") == "boxset"
                    ]
                    if len(created) != 1:
                        raise ValueError("Could not identify the new Emby home section")
                    if row is not None:
                        await self._forget_home_section(row)
                    row = EmbyHomeSection(
                        service_config_id=self.config_id,
                        user_id=user_id,
                        kind=kind,
                        section_id=str(created[0]["Id"]),
                        collection_id=collection_id,
                    )
                    try:
                        await self._save_home_section(row)
                    except Exception:
                        # Do not leave an untracked row behind if persistence fails.
                        response = await self.session.post(
                            f"{self.service_url}/Users/{user_id}/HomeSections/Delete",
                            json={"Ids": [row.section_id]},
                            timeout=30,
                        )
                        response.raise_for_status()
                        raise
                elif row is not None and row.collection_id != collection_id:
                    row.collection_id = collection_id
                    await self._save_home_section(row)
