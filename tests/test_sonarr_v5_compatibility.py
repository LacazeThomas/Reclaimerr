"""Sanitized v5 response contracts; these tests never contact a server."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.sonarr import SonarrClient, build_sonarr_series_from_dict

SERIES = {
    "id": 42,
    "title": "Example",
    "tvdbId": 123,
    "seasonType": "official",
    "seasonTypes": [
        {"name": "Aired Order", "type": "official"},
        {"name": "DVD Order", "type": "dvd"},
    ],
    "language": {"id": 1, "name": "English"},
    "monitorNewItems": "all",
    "monitored": True,
    "tags": [7],
    "qualityProfileId": 9,
    "seasons": [{"seasonNumber": 1, "monitored": True}],
}


@pytest.mark.parametrize(
    "version, expected",
    [("3.0.10", "aired"), ("4.0.16", "aired"), ("5.0.0.2569", None), (None, None)],
)
def test_missing_order_requires_confirmed_legacy_server(version, expected):
    async def run():
        series = build_sonarr_series_from_dict({"id": 42})
        request = AsyncMock(return_value=(200, {"version": version}))
        client = SonarrClient("synthetic", "http://sonarr.invalid")
        try:
            with patch.object(SonarrClient, "_make_request", request):
                assert (
                    await client.get_series_episode_order(42, series=series) == expected
                )
                assert (
                    await client.get_series_episode_order(42, series=series) == expected
                )
            request.assert_awaited_once_with("GET", "system/status")
        finally:
            await client.session.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("official", "aired"),
        ("dvd", "dvd"),
        ("digital", None),
        (None, None),
        ("", None),
    ],
)
def test_explicit_order_never_falls_back_to_legacy(raw, expected):
    async def run():
        series = build_sonarr_series_from_dict({**SERIES, "seasonType": raw})
        request = AsyncMock()
        client = SonarrClient("synthetic", "http://sonarr.invalid")
        try:
            with patch.object(SonarrClient, "_make_request", request):
                assert (
                    await client.get_series_episode_order(42, series=series) == expected
                )
            request.assert_not_awaited()
        finally:
            await client.session.close()

    asyncio.run(run())


def test_v5_episode_inventory_and_embedded_file_dates_use_v3_url():
    async def run():
        episodes = [
            {
                "id": 1,
                "seasonNumber": 1,
                "episodeNumber": 1,
                "hasFile": True,
                "episodeFileId": 10,
                "episodeFile": {"id": 10, "dateAdded": "2026-10-13T12:00:00Z"},
            },
            {
                "id": 2,
                "seasonNumber": 1,
                "episodeNumber": 2,
                "hasFile": False,
                "episodeFileId": 0,
            },
        ]
        response = SimpleNamespace(
            status_code=200,
            content=b"json",
            json=lambda: episodes,
            raise_for_status=lambda: None,
        )
        request = AsyncMock(return_value=response)
        client = SonarrClient("synthetic", "http://sonarr.invalid/base/")
        try:
            with patch.object(type(client.session), "request", request):
                result = await client.get_episode_sync_data(42)
            assert result.episode_numbers_by_season == {1: {1, 2}}
            assert list(result.file_dates) == [(1, 1)]
            request.assert_awaited_once_with(
                "GET",
                "http://sonarr.invalid/base/api/v3/episode",
                params={"seriesId": 42, "includeEpisodeFile": True},
                timeout=60,
            )
        finally:
            await client.session.close()

    asyncio.run(run())


def test_monitoring_preserves_v5_order_language_and_unrelated_settings():
    async def run():
        data = deepcopy(SERIES)
        data["seasonType"] = "dvd"
        original = deepcopy(data)
        request = AsyncMock(
            side_effect=lambda method, endpoint, **kwargs: (
                200 if method == "GET" else 202,
                data if method == "GET" else kwargs["json"],
            )
        )
        client = SonarrClient("synthetic", "http://sonarr.invalid")
        try:
            with patch.object(SonarrClient, "_make_request", request):
                await client.update_season_monitoring(42, 1, False)
                await client.enable_new_seasons_if_latest(42, 1)
            for call in request.call_args_list:
                if call.args[0] == "PUT":
                    payload = call.kwargs["json"]
                    assert payload["seasonType"] == "dvd"
                    assert payload["language"] == original["language"]
                    assert payload["tags"] == [7] and payload["qualityProfileId"] == 9
            assert data == original
        finally:
            await client.session.close()

    asyncio.run(run())


def test_tag_profile_and_delete_request_contracts_are_offline():
    async def run():
        request = AsyncMock(
            side_effect=[
                (202, [SERIES]),
                (202, [SERIES]),
                (202, [SERIES]),
                (200, None),
                (200, None),
                (200, None),
                (202, []),
            ]
        )
        client = SonarrClient("synthetic", "http://sonarr.invalid")
        try:
            with (
                patch.object(SonarrClient, "_make_request", request),
                patch("backend.services.sonarr.guard_arr_removal", AsyncMock()),
            ):
                await client.add_tag_to_series([42], 8)
                await client.remove_tag_from_series([42], 8)
                await client.set_series_quality_profile([42], 10)
                await client.delete_series(42)
                await client.delete_series_bulk([42])
                await client.delete_episode_file(10)
                await client.unmonitor_episode(1)
            calls = request.call_args_list
            assert calls[0].kwargs["json"] == {
                "seriesIds": [42],
                "tags": [8],
                "applyTags": "add",
            }
            assert calls[1].kwargs["json"]["applyTags"] == "remove"
            assert calls[2].kwargs["json"] == {
                "seriesIds": [42],
                "qualityProfileId": 10,
            }
            assert calls[3].args == ("DELETE", "series/42")
            assert calls[3].kwargs["params"] == {
                "deleteFiles": "true",
                "addImportListExclusion": "false",
            }
            assert calls[4].kwargs["json"] == {
                "seriesIds": [42],
                "deleteFiles": True,
                "addImportListExclusion": False,
            }
            assert calls[5].args == ("DELETE", "episodefile/10")
            assert calls[6].kwargs["json"] == {"episodeIds": [1], "monitored": False}
        finally:
            await client.session.close()

    asyncio.run(run())
