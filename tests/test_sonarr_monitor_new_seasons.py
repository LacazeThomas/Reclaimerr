from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.routes.rules import _normalize_rule_action, get_rules, import_rules
from backend.core.rule_actions import ARR_ACTION_MONITOR_NEW_SEASONS as ACTION
from backend.database import Base
from backend.database.models import ReclaimRule, User
from backend.enums import MediaType, UserRole
from backend.models.cleanup import CleanupRuleCreate, RuleImportPayload
from backend.services.sonarr import SonarrClient
from backend.tasks import cleanup


def test_action_normalization_and_scope():
    action = {"arr_action": ACTION, "move_instead_of_delete": True}
    normalized = _normalize_rule_action(action, "Clean watched seasons", "season")
    assert normalized["arr_action"] == ACTION
    assert normalized["move_instead_of_delete"] is True
    assert _normalize_rule_action(normalized, "Imported", "season") == normalized
    for scope in ["series", "episode", "movie_version", None]:
        with pytest.raises(ValueError, match="only available on season rules"):
            _normalize_rule_action(action, "Invalid", scope)


@pytest.mark.parametrize(
    "other,expected",
    [
        ("delete", ACTION),
        ("unmonitor", "unmonitor"),
        ("unmonitor_only", "unmonitor_only"),
        ("change_quality_profile", "change_quality_profile"),
    ],
)
def test_action_conflicts(other, expected):
    candidate = SimpleNamespace(id=1, matched_rule_ids=[1, 2])
    rules = {
        1: SimpleNamespace(action={"arr_action": ACTION}),
        2: SimpleNamespace(action={"arr_action": other}),
    }
    assert cleanup._get_arr_action(candidate, rules) == expected
    assert cleanup._merge_arr_action(ACTION, other) == expected
    assert cleanup._merge_arr_action(other, ACTION) == expected
    assert cleanup._merge_arr_action(None, ACTION) == ACTION
    assert cleanup._merge_arr_action("remove_if_empty", ACTION) == ACTION


@pytest.mark.parametrize(
    "season_number,latest,initially_monitored,new_items,enabled",
    [
        (2, 2, False, "none", True),
        (2, 2, True, "all", True),
        (1, 2, False, "none", False),
        (0, 2, False, "none", False),
        (0, 0, True, "all", False),
    ],
)
def test_live_latest_season_preserves_other_flags(
    season_number, latest, initially_monitored, new_items, enabled
):
    async def run():
        data = {
            "id": 42,
            "title": "Show",
            "monitored": initially_monitored,
            "monitorNewItems": new_items,
            "tags": [7],
            "qualityProfileId": 9,
            "seasons": [
                {
                    "seasonNumber": n,
                    "monitored": n != 1,
                    "statistics": {"episodeFileCount": 0},
                }
                for n in range(latest + 1)
            ],
        }
        original = deepcopy(data)

        async def request(method, endpoint, **kwargs):
            return 200, data if method == "GET" else kwargs["json"]

        mock = AsyncMock(side_effect=request)
        client = SonarrClient(api_key="key", base_url="http://sonarr")
        try:
            with patch.object(SonarrClient, "_make_request", mock):
                assert (
                    await client.enable_new_seasons_if_latest(42, season_number)
                    is enabled
                )
        finally:
            await client.session.close()
        assert data == original
        assert mock.await_count == (2 if enabled else 1)
        if enabled:
            payload = mock.call_args.kwargs["json"]
            assert payload["monitored"] is True
            assert payload["monitorNewItems"] == "all"
            assert payload["tags"] == [7] and payload["qualityProfileId"] == 9
            assert payload["seasons"][season_number]["monitored"] is False
            for n in range(latest + 1):
                if n != season_number:
                    assert payload["seasons"][n] == original["seasons"][n]

    asyncio.run(run())


def test_missing_season_blocks_preparation_and_finalization():
    async def run():
        client = SonarrClient(api_key="key", base_url="http://sonarr")
        request = AsyncMock(return_value=(200, {"id": 42, "seasons": []}))
        try:
            with patch.object(SonarrClient, "_make_request", request):
                with pytest.raises(ValueError, match="missing"):
                    await client.prepare_season_removal(42, 2)
                with pytest.raises(ValueError, match="missing"):
                    await client.enable_new_seasons_if_latest(42, 2)
        finally:
            await client.session.close()
        assert all(call.args[0] == "GET" for call in request.call_args_list)

    asyncio.run(run())


def test_unconfirmed_monitoring_update_is_an_error():
    async def run():
        client = SonarrClient(api_key="key", base_url="http://sonarr")
        request = AsyncMock(
            side_effect=lambda *args, **kwargs: (
                200,
                {
                    "id": 42,
                    "monitored": False,
                    "seasons": [{"seasonNumber": 1, "monitored": True}],
                },
            )
        )
        try:
            with patch.object(SonarrClient, "_make_request", request):
                with pytest.raises(ValueError, match="did not unmonitor"):
                    await client.prepare_season_removal(42, 1)
                with pytest.raises(ValueError, match="did not confirm"):
                    await client.enable_new_seasons_if_latest(42, 1)
        finally:
            await client.session.close()

    asyncio.run(run())


def test_season_file_deletion_deduplicates_and_ignores_missing_files():
    async def run():
        client = SonarrClient(api_key="key", base_url="http://sonarr")
        episodes = [
            {"seasonNumber": s, "episodeFileId": f}
            for s, f in [(1, 0), (1, None), (1, -1), (1, 50), (1, 50), (2, 60)]
        ]
        request = AsyncMock(side_effect=[(200, episodes), (200, None)])
        try:
            with patch.object(SonarrClient, "_make_request", request):
                await client.delete_season_files(42, 1)
        finally:
            await client.session.close()
        assert request.call_args.kwargs["json"] == {"episodeFileIds": [50]}

    asyncio.run(run())


@pytest.mark.parametrize(
    "action",
    [ACTION, cleanup.ARR_ACTION_LIST_EXCLUSION, cleanup.ARR_ACTION_MONITOR_OR_EXCLUDE],
)
def test_rule_import_export_round_trip(monkeypatch, action):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(
            "backend.api.routes.rules._sync_stale_library_notice", AsyncMock()
        )
        user = User(username="admin", password_hash="x", role=UserRole.ADMIN)
        rule = CleanupRuleCreate(
            name="Watched seasons",
            media_type=MediaType.SERIES,
            target_scope="season",
            action={"arr_action": action, "move_instead_of_delete": True},
            definition={
                "version": 1,
                "root": {
                    "type": "group",
                    "op": "and",
                    "children": [
                        {
                            "type": "condition",
                            "field": "media.size",
                            "operator": "greater_than",
                            "value": 0,
                        }
                    ],
                },
            },
        )
        try:
            async with sessions() as db:
                result = await import_rules(RuleImportPayload(rules=[rule]), user, db)
                assert result.imported == 1 and result.errors == []
                exported = (await get_rules(user, db))[0]
                assert exported.action["arr_action"] == action
                assert exported.action["move_instead_of_delete"] is True
                imported = CleanupRuleCreate.model_validate(exported.model_dump())
                result = await import_rules(
                    RuleImportPayload(rules=[imported]), user, db
                )
                assert result.imported == 1 and result.errors == []
                stored = (await db.execute(select(ReclaimRule))).scalars().all()
                assert len(stored) == 2
                assert all(r.action["arr_action"] == action for r in stored)
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,season,latest,existing,expected",
    [
        ("ended", 2, 2, False, True),
        ("ended", 2, 2, True, True),
        ("continuing", 2, 2, False, False),
        (None, 2, 2, False, False),
        ("ended", 1, 2, False, False),
        ("ended", 2, 3, False, False),
        ("ended", 0, 0, False, False),
    ],
)
def test_exclusion_requires_ended_latest_regular_season(
    status, season, latest, existing, expected
):
    async def run():
        data = {
            "id": 42,
            "title": "Show",
            "tvdbId": 99,
            "status": status,
            "seasons": [{"seasonNumber": n} for n in range(latest + 1)],
        }

        async def request(method, endpoint, **kwargs):
            if endpoint == "series/42":
                return 200, data
            if endpoint == "importlistexclusion" and method == "GET":
                return 200, [{"tvdbId": 99}] if existing else []
            return 200, kwargs.get("json")

        mock = AsyncMock(side_effect=request)
        client = SonarrClient(api_key="key", base_url="http://sonarr")
        try:
            with patch.object(SonarrClient, "_make_request", mock):
                assert (
                    await client.exclude_ended_series_if_latest(42, season) is expected
                )
        finally:
            await client.session.close()
        posts = [c for c in mock.call_args_list if c.args[0] == "POST"]
        assert len(posts) == int(expected and not existing)
        if posts:
            assert posts[0].kwargs["json"] == {"tvdbId": 99, "title": "Show"}
        if not expected:
            assert mock.await_count == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "action", [cleanup.ARR_ACTION_LIST_EXCLUSION, cleanup.ARR_ACTION_MONITOR_OR_EXCLUDE]
)
def test_exclusion_action_scope_and_precedence(action):
    assert (
        _normalize_rule_action({"arr_action": action}, "Season", "season")["arr_action"]
        == action
    )
    if action == cleanup.ARR_ACTION_LIST_EXCLUSION:
        assert (
            _normalize_rule_action({"arr_action": action}, "Movie", "movie_version")[
                "arr_action"
            ]
            == action
        )
    else:
        with pytest.raises(ValueError):
            _normalize_rule_action({"arr_action": action}, "Movie", "movie_version")
    for scope in ["series", "episode", None]:
        with pytest.raises(ValueError):
            _normalize_rule_action({"arr_action": action}, "Invalid", scope)
    for conservative in [
        "unmonitor",
        "unmonitor_only",
        "change_quality_profile",
        ACTION,
    ]:
        assert cleanup._merge_arr_action(action, conservative) == conservative
        assert cleanup._merge_arr_action(conservative, action) == conservative
    candidate = SimpleNamespace(id=1, matched_rule_ids=[1])
    assert (
        cleanup._get_arr_action(
            candidate, {1: SimpleNamespace(action={"arr_action": action})}
        )
        == action
    )


@pytest.mark.parametrize(
    "action,excluded,expected",
    [
        (cleanup.ARR_ACTION_LIST_EXCLUSION, True, {"arr_list_exclusion": "added"}),
        (cleanup.ARR_ACTION_LIST_EXCLUSION, False, {"arr_list_exclusion": "skipped"}),
        (cleanup.ARR_ACTION_MONITOR_OR_EXCLUDE, True, {"arr_list_exclusion": "added"}),
        (
            cleanup.ARR_ACTION_MONITOR_OR_EXCLUDE,
            False,
            {"sonarr_monitor_new_seasons": "enabled"},
        ),
    ],
)
def test_combined_action_chooses_one_branch(action, excluded, expected):
    client = SimpleNamespace(
        exclude_ended_series_if_latest=AsyncMock(return_value=excluded),
        enable_new_seasons_if_latest=AsyncMock(return_value=True),
    )
    assert (
        asyncio.run(
            cleanup._finalize_new_season_monitoring(client, 42, 2, 1, action=action)
        )
        == expected
    )
    assert client.enable_new_seasons_if_latest.await_count == int(
        "sonarr_monitor_new_seasons" in expected
    )


def test_exclusion_failure_does_not_enable_monitoring():
    client = SimpleNamespace(
        exclude_ended_series_if_latest=AsyncMock(
            side_effect=RuntimeError("unavailable")
        ),
        enable_new_seasons_if_latest=AsyncMock(),
    )
    result = asyncio.run(
        cleanup._finalize_new_season_monitoring(
            client, 42, 2, 1, action=cleanup.ARR_ACTION_MONITOR_OR_EXCLUDE
        )
    )
    assert result["arr_list_exclusion"] == "failed"
    client.enable_new_seasons_if_latest.assert_not_awaited()


@pytest.mark.parametrize("existing", [True, False])
def test_radarr_exclusion_retains_entry_and_deduplicates(existing):
    from backend.services.radarr import RadarrClient

    async def run():
        async def request(method, endpoint, **kwargs):
            if endpoint == "movie/42":
                return 200, {
                    "id": 42,
                    "title": "Movie",
                    "year": 2020,
                    "tmdbId": 99,
                    "path": "/movies/Movie",
                }
            if method == "GET":
                return 200, [{"tmdbId": 99}] if existing else []
            return 201, kwargs.get("json")

        mock = AsyncMock(side_effect=request)
        client = RadarrClient(api_key="key", base_url="http://radarr")
        try:
            with patch.object(RadarrClient, "_make_request", mock):
                await client.add_import_exclusion(42)
        finally:
            await client.session.close()
        posts = [c for c in mock.call_args_list if c.args[0] == "POST"]
        assert len(posts) == int(not existing)
        if posts:
            assert posts[0].kwargs["json"] == {
                "tmdbId": 99,
                "movieTitle": "Movie",
                "movieYear": 2020,
            }
        assert not any(c.args[0] == "DELETE" for c in mock.call_args_list)

    asyncio.run(run())
