from __future__ import annotations

import asyncio
import copy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database.models import EmbyHomeSection, ServiceConfig
from backend.enums import Service
from backend.models.settings import GeneralSettingsResponse
from backend.services.emby import EmbyService
from backend.services.emby_base import EmbyServiceBase
from backend.utils.helpers import LeavingSoonVisibility


class FakeEmby(EmbyService):
    def __init__(self):
        self.config_id = 1
        self.service_url = "http://emby"
        self.service_type = Service.EMBY
        self._home_lock = asyncio.Lock()
        self.users = [{"Id": "admin"}, {"Id": "friend"}]
        self.sections = {
            u["Id"]: [{"Id": "existing", "SectionType": "resume"}] for u in self.users
        }
        self.owned = []
        self.posts = []
        self.visible = True
        self.sequence = 0
        self.session = SimpleNamespace(
            post=AsyncMock(
                return_value=SimpleNamespace(
                    status_code=200, raise_for_status=lambda: None
                )
            )
        )
        self._find_collection_ids_by_title = AsyncMock(return_value=["collection"])
        self._make_request = AsyncMock(side_effect=self.request)

    async def request(self, endpoint, **kwargs):
        if endpoint == "Users":
            return copy.deepcopy(self.users)
        if endpoint == "System/Info":
            return {"Version": "4.10.1.0"}
        raise AssertionError(endpoint)

    async def _owned_home_sections(self):
        return list(self.owned)

    async def _save_home_section(self, row):
        if row not in self.owned:
            self.owned.append(row)

    async def _forget_home_section(self, row):
        self.owned.remove(row)

    async def _home_sections(self, user_id):
        return copy.deepcopy(self.sections[user_id])

    async def _visible_collection(self, user_id, collection_id, item_type):
        return self.visible and user_id != "restricted"

    async def _post_home_section(self, user_id, section):
        self.posts.append((user_id, copy.deepcopy(section)))
        section = copy.deepcopy(section)
        if "Id" not in section:
            self.sequence += 1
            section["Id"] = f"generated-{self.sequence}"
            self.sections[user_id].append(section)
        else:
            idx = next(
                i
                for i, s in enumerate(self.sections[user_id])
                if s["Id"] == section["Id"]
            )
            self.sections[user_id][idx] = section


async def sync(fake, title="Leaving Soon"):
    await fake._sync_home_sections(movie_title=title, series_title=None)


def test_setting_defaults_off():
    assert GeneralSettingsResponse().leaving_soon_emby_home_enabled is False
    assert LeavingSoonVisibility(emby_home=True)


def test_migration_defaults_off_and_is_repeatable(tmp_path):
    migration = importlib.import_module(
        "backend.alembic.versions.b6e2f8a4d901_add_emby_home_sections"
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE general_settings (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE service_configs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO general_settings (id) VALUES (1)"))
        with patch.object(
            migration, "op", Operations(MigrationContext.configure(conn))
        ):
            migration.upgrade()
            migration.upgrade()
            assert (
                conn.scalar(
                    text("SELECT leaving_soon_emby_home_enabled FROM general_settings")
                )
                == 0
            )
            assert inspect(conn).has_table("emby_home_sections")
            migration.downgrade()
            assert not inspect(conn).has_table("emby_home_sections")
            assert "leaving_soon_emby_home_enabled" not in {
                c["name"] for c in inspect(conn).get_columns("general_settings")
            }


def test_all_users_get_separate_rows_without_changing_existing_sections():
    async def run():
        fake = FakeEmby()
        await fake._sync_home_sections(movie_title="Movies", series_title="Series")
        assert len(fake.owned) == 4
        assert {r.user_id for r in fake.owned} == {"admin", "friend"}
        assert all(
            s[0] == {"Id": "existing", "SectionType": "resume"}
            for s in fake.sections.values()
        )
        assert {r.kind for r in fake.owned} == {"movies", "series"}
        for user_id, section in fake.posts:
            assert "Id" not in section  # Emby must allocate new IDs.
            assert section["ParentId"] == "collection"
            assert section["SectionType"] == "boxset"

    asyncio.run(run())


def test_repeat_sync_preserves_position_and_user_presentation():
    async def run():
        fake = FakeEmby()
        await sync(fake)
        fake.sections["admin"][1]["ImageType"] = "Thumb"
        before = copy.deepcopy(fake.sections)
        fake.posts.clear()
        await sync(fake)
        assert not fake.posts
        assert fake.sections == before
        assert len(fake.owned) == 2

    asyncio.run(run())


def test_recreated_collection_repairs_existing_row_without_moving_it():
    async def run():
        fake = FakeEmby()
        await sync(fake)
        old_ids = [r.section_id for r in fake.owned]
        fake.sections["admin"][1]["ViewType"] = "spotlight"
        fake._find_collection_ids_by_title.return_value = ["replacement"]
        await sync(fake, "Renamed")
        assert [r.section_id for r in fake.owned] == old_ids
        assert all(r.collection_id == "replacement" for r in fake.owned)
        assert fake.sections["admin"][1]["ViewType"] == "spotlight"
        assert fake.sections["admin"][1]["CustomName"] == "Renamed"

    asyncio.run(run())


def test_manually_removed_row_is_recreated_without_duplicates():
    async def run():
        fake = FakeEmby()
        await sync(fake)
        fake.sections["admin"] = fake.sections["admin"][:1]
        await sync(fake)
        assert len(fake.owned) == 2
        assert len(fake.sections["admin"]) == 2
        assert fake.owned[-1].section_id == "generated-3"

    asyncio.run(run())


def test_restricted_and_disabled_users_get_no_row_and_new_users_do():
    async def run():
        fake = FakeEmby()
        fake.users.extend(
            [
                {"Id": "restricted"},
                {"Id": "disabled", "Policy": {"IsDisabled": True}},
                {"Id": "new"},
            ]
        )
        fake.sections["new"] = []
        await sync(fake)
        assert {r.user_id for r in fake.owned} == {"admin", "friend", "new"}

    asyncio.run(run())


def test_empty_or_inaccessible_collection_removes_only_owned_rows():
    async def run():
        fake = FakeEmby()
        await sync(fake)
        fake.visible = False
        await sync(fake)
        assert not fake.owned
        assert fake.session.post.await_count == 2
        assert fake.session.post.await_args.kwargs["json"] == {"Ids": ["generated-2"]}

    asyncio.run(run())


def test_collection_deletion_waits_for_home_row_cleanup():
    async def run():
        fake = FakeEmby()
        await sync(fake)
        events = []
        fake._remove_home_section = AsyncMock(
            side_effect=lambda row: events.append("row")
        )
        with patch.object(
            EmbyServiceBase,
            "_delete_collection",
            AsyncMock(side_effect=lambda cid: events.append("collection")),
        ):
            await fake._delete_collection("collection")
        assert events == ["row", "row", "collection"]

    asyncio.run(run())


def test_cleanup_failure_stops_collection_deletion():
    async def run():
        fake = FakeEmby()
        await sync(fake)
        fake._remove_home_section = AsyncMock(side_effect=RuntimeError("unavailable"))
        with patch.object(EmbyServiceBase, "_delete_collection", AsyncMock()) as delete:
            with pytest.raises(RuntimeError, match="unavailable"):
                await fake._delete_collection("collection")
            delete.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    "remaining,removed", [({"last"}, True), ({"last", "other"}, False)]
)
def test_prune_removes_rows_before_last_member(remaining, removed):
    async def run():
        fake = FakeEmby()
        await sync(fake)
        fake._get_collection_item_ids = AsyncMock(return_value=remaining)
        events = []
        fake._remove_collection_home_sections = AsyncMock(
            side_effect=lambda cid: events.append("rows")
        )
        with patch.object(
            EmbyServiceBase,
            "_remove_items_from_collection",
            AsyncMock(side_effect=lambda **kw: events.append("members")),
        ):
            await fake._remove_items_from_collection(
                collection_id="collection", item_ids={"last"}
            )
        assert events == (["rows", "members"] if removed else ["members"])

    asyncio.run(run())


def test_opt_out_removes_owned_rows_even_if_collections_are_already_gone():
    async def run():
        fake = FakeEmby()
        await sync(fake)
        with patch.object(
            EmbyServiceBase, "sync_leaving_soon_collections", AsyncMock()
        ):
            await fake.sync_leaving_soon_collections(
                movie_title="Movies", series_title="Series"
            )
        assert not fake.owned

    asyncio.run(run())


def test_old_servers_work_with_home_promotion_off():
    async def run():
        fake = FakeEmby()
        with patch.object(
            EmbyServiceBase, "sync_leaving_soon_collections", AsyncMock()
        ) as sync_collections:
            await fake.sync_leaving_soon_collections(
                movie_title="Movies", series_title="Series"
            )
            sync_collections.assert_awaited_once()
        fake._make_request.assert_not_awaited()

    asyncio.run(run())


def test_old_server_rejects_home_promotion_before_collection_changes():
    async def run():
        fake = FakeEmby()
        fake._make_request = AsyncMock(return_value={"Version": "4.9.3.0"})
        with patch.object(
            EmbyServiceBase, "sync_leaving_soon_collections", AsyncMock()
        ) as sync_collections:
            with pytest.raises(ValueError, match="4.10"):
                await fake.sync_leaving_soon_collections(
                    collection_visibility=LeavingSoonVisibility(emby_home=True)
                )
            sync_collections.assert_not_awaited()

    asyncio.run(run())


def test_owned_section_ids_survive_client_recreation(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ownership.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: ServiceConfig.__table__.create(c))
            await conn.run_sync(lambda c: EmbyHomeSection.__table__.create(c))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        with patch("backend.services.emby.async_db", sessions):
            fake = EmbyService("test", "http://emby", config_id=1)
            try:
                await fake._save_home_section(
                    EmbyHomeSection(
                        service_config_id=1,
                        user_id="user",
                        kind="movies",
                        section_id="server-assigned",
                        collection_id="collection",
                    )
                )
                other = EmbyService("test", "http://emby", config_id=1)
                try:
                    rows = await other._owned_home_sections()
                    assert len(rows) == 1
                    assert rows[0].section_id == "server-assigned"
                    await other._forget_home_section(rows[0])
                    assert not await fake._owned_home_sections()
                finally:
                    await other.session.close()
            finally:
                await fake.session.close()
        await engine.dispose()

    asyncio.run(run())


def test_emby_toggle_does_not_change_plex_hubs():
    from backend.services.plex import PlexService

    async def run():
        fake = SimpleNamespace(session=SimpleNamespace(put=AsyncMock()))
        await PlexService._apply_collection_visibility(
            fake,
            collection_id="collection",
            section_id="library",
            collection_title="Leaving Soon",
            visibility=LeavingSoonVisibility(emby_home=True),
        )
        fake.session.put.assert_not_awaited()

    asyncio.run(run())
