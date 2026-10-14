"""Issue #431: deletion scans must use section roots, not Plex browse views."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.services.plex import PlexService


def _service(monkeypatch, sections):
    requests = []

    async def request(self, endpoint, **kwargs):
        requests.append(endpoint)
        assert endpoint == "library/sections", "Do not fetch section browse views"
        return {"MediaContainer": {"Directory": sections}}, 200

    monkeypatch.setattr(PlexService, "_make_request", request)
    service = object.__new__(PlexService)
    service.plex_url = "http://plex.local"
    service.session = SimpleNamespace(get=AsyncMock(return_value=Mock()))
    return service, requests


def test_scan_uses_section_locations_and_selects_matching_library(monkeypatch):
    service, requests = _service(
        monkeypatch,
        [
            {"key": "1", "Location": [{"path": "/data/movies"}]},
            {
                "key": "2",
                "Location": [{"path": "/data/tv"}, {"path": "/archive/tv"}],
            },
        ],
    )
    deleted_path = "/archive/tv/Will Trent (2023) {tvdb-419879}/Season 04"

    assert asyncio.run(service.scan_item_path(deleted_path)) is True

    assert requests == ["library/sections"]
    service.session.get.assert_awaited_once_with(
        "http://plex.local/library/sections/2/refresh", params={"path": deleted_path}
    )
    service.session.get.return_value.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize(
    ("root", "path", "matches"),
    [
        ("/data/tv", "/data/tv/Show/Season 01", True),
        ("/data/tv", "/data/tv", True),
        ("/data/tv/", "/data/tv", True),
        ("/data/tv/", "/data/tv/Show/Season 01", True),
        ("/data/tv", "/data/tv/", True),
        ("/data/tv", "/data/tv2/Show/Season 01", False),
        ("/data/tv/", "/data/tv2/Show/Season 01", False),
        ("/data/tv", "/data/tv-backup/Show", False),
        ("/data/tv", "/other/tv/Show", False),
        ("/", "/data/tv/Show", True),
        (r"D:\TV", r"D:\TV\Show\Season 01", True),
        (r"D:\TV", r"D:\TV2\Show\Season 01", False),
        ("", "/data/tv/Show", False),
        ("   ", "/data/tv/Show", False),
        ("/data/tv", "", False),
    ],
)
def test_scan_matches_only_library_root_or_descendants(
    monkeypatch, root, path, matches
):
    service, _ = _service(monkeypatch, [{"key": "2", "Location": [{"path": root}]}])

    assert asyncio.run(service.scan_item_path(path)) is matches

    if matches:
        service.session.get.assert_awaited_once_with(
            "http://plex.local/library/sections/2/refresh", params={"path": path}
        )
    else:
        service.session.get.assert_not_awaited()


def test_scan_skips_missing_or_malformed_locations(monkeypatch):
    service, _ = _service(
        monkeypatch,
        [
            {"Location": [{"path": "/data/tv"}]},
            {"key": "1"},
            {"key": "2", "Location": None},
            {"key": "3", "Location": {"path": "/data/tv"}},
            {"key": "4", "Location": [None, {}, {"path": None}, {"path": 42}]},
            {"key": "5", "Location": [{"path": "/data/tv"}]},
        ],
    )

    assert asyncio.run(service.scan_item_path("/data/tv/Show")) is True

    service.session.get.assert_awaited_once_with(
        "http://plex.local/library/sections/5/refresh", params={"path": "/data/tv/Show"}
    )


def test_scan_reports_refresh_failure(monkeypatch):
    service, _ = _service(
        monkeypatch, [{"key": "2", "Location": [{"path": "/data/tv"}]}]
    )
    service.session.get.return_value.raise_for_status.side_effect = RuntimeError(
        "Plex rejected the scan"
    )

    assert asyncio.run(service.scan_item_path("/data/tv/Show")) is False
    service.session.get.assert_awaited_once()
