from __future__ import annotations

import asyncio
import importlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes.info.info import router as info_router
from backend.api.routes.settings.login_background import router
from backend.core.auth import get_current_user
from backend.core.settings import settings
from backend.core.utils.login_background import save_background
from backend.database import get_db
from backend.database.models import GeneralSettings, User
from backend.enums import UserRole

ENDPOINT = "/api/settings/general/login-background"


def image_bytes(size=(120, 80)):
    stream = BytesIO()
    Image.new("RGB", size, "red").save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "static_dir", tmp_path / "static")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(GeneralSettings.__table__.create)

    asyncio.run(setup())
    app = FastAPI()
    app.include_router(router, prefix="/api/settings")
    app.include_router(info_router, prefix="/api/info")

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role=UserRole.ADMIN, id=None
    )
    with TestClient(app) as http:
        yield http
    asyncio.run(engine.dispose())


def test_defaults_and_public_response_excludes_private_settings(client):
    payload = client.get("/api/info/login-background").json()
    assert payload == {
        "mode": "default",
        "urls": [],
        "interval_seconds": 5,
        "image_url": None,
    }
    assert client.get(ENDPOINT).json() == payload


def test_custom_urls_persist_normalize_and_replace_defaults(client):
    payload = {
        "mode": "urls",
        "urls": [
            " https://example.com/a.jpg ",
            "https://example.com/a.jpg",
            "https://example.com/b",
        ],
        "interval_seconds": 12,
    }
    response = client.put(ENDPOINT, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["urls"] == [
        "https://example.com/a.jpg",
        "https://example.com/b",
    ]
    assert client.get("/api/info/login-background").json() == response.json()
    assert (
        client.put(
            ENDPOINT, json={"mode": "default", "urls": response.json()["urls"]}
        ).status_code
        == 200
    )
    assert client.get("/api/info/login-background").json()["mode"] == "default"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:image/png;base64,a",
        "file:///tmp/a",
        "//example.com/a",
        "https://user:secret@example.com/a",
        "https://example.com:bad/a",
        "https://example.com/a b",
    ],
)
def test_invalid_urls_are_rejected(client, url):
    assert client.put(ENDPOINT, json={"mode": "urls", "urls": [url]}).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "urls", "urls": []},
        {"mode": "urls", "urls": ["https://example.com/a"] * 51},
        {"mode": "default", "interval_seconds": 0},
        {"mode": "default", "interval_seconds": 301},
        {"mode": "unknown"},
    ],
)
def test_invalid_configuration_is_rejected(client, payload):
    assert client.put(ENDPOINT, json=payload).status_code == 422


def test_upload_replace_remove_and_mode_switch(client):
    assert client.put(ENDPOINT, json={"mode": "upload"}).status_code == 400
    first = client.post(
        ENDPOINT + "/image", files={"image": ("test.png", image_bytes(), "image/png")}
    )
    assert first.status_code == 200, first.text
    first_path = settings.static_dir / first.json()["image_url"].removeprefix(
        "/static/"
    )
    with Image.open(first_path) as saved:
        assert saved.format == "JPEG"
        assert saved.size == (120, 80)
    assert client.put(ENDPOINT, json={"mode": "upload"}).status_code == 200
    second = client.post(
        ENDPOINT + "/image", files={"image": ("test.webp", image_bytes(), "image/webp")}
    )
    assert second.status_code == 200
    assert not first_path.exists()
    second_path = settings.static_dir / second.json()["image_url"].removeprefix(
        "/static/"
    )
    assert second_path.exists()
    removed = client.delete(ENDPOINT + "/image").json()
    assert removed["mode"] == "default"
    assert removed["image_url"] is None
    assert not second_path.exists()


def test_bad_upload_does_not_replace_existing_image(client):
    good = client.post(
        ENDPOINT + "/image", files={"image": ("test.png", image_bytes(), "image/png")}
    ).json()
    bad = client.post(
        ENDPOINT + "/image", files={"image": ("test.png", b"invalid", "image/png")}
    )
    assert bad.status_code == 400
    assert client.get(ENDPOINT).json()["image_url"] == good["image_url"]
    assert (
        client.post(
            ENDPOINT + "/image",
            files={"image": ("test.svg", b"<svg/>", "image/svg+xml")},
        ).status_code
        == 400
    )
    assert (
        client.post(
            ENDPOINT + "/image",
            files={"image": ("big.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")},
        ).status_code
        == 413
    )


def test_admin_required_for_every_settings_action(client):
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role=UserRole.USER
    )
    assert client.get(ENDPOINT).status_code == 403
    assert client.put(ENDPOINT, json={"mode": "default"}).status_code == 403
    assert (
        client.post(
            ENDPOINT + "/image",
            files={"image": ("test.png", image_bytes(), "image/png")},
        ).status_code
        == 403
    )
    assert client.delete(ENDPOINT + "/image").status_code == 403
    assert client.get("/api/info/login-background").status_code == 200


def test_storage_bounds_large_image_and_preserves_aspect(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "static_dir", tmp_path)
    filename = save_background(image_bytes((4000, 2000)))
    with Image.open(tmp_path / "login-backgrounds" / filename) as saved:
        assert saved.size == (3840, 1920)


def test_migration_preserves_existing_rows_and_round_trips():
    migration = importlib.import_module(
        "backend.alembic.versions.b7d2e9a4c601_add_login_background"
    )
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE general_settings (id INTEGER PRIMARY KEY, application_url TEXT)"
            )
        )
        conn.execute(
            text("INSERT INTO general_settings VALUES (1, 'https://reclaimerr.test')")
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            row = conn.execute(text("SELECT * FROM general_settings")).mappings().one()
            assert row["login_background_mode"] == "default"
            assert row["login_background_urls"] == "[]"
            assert row["login_background_interval"] == 5
            migration.downgrade()
        assert [c["name"] for c in inspect(conn).get_columns("general_settings")] == [
            "id",
            "application_url",
        ]
        assert (
            conn.execute(text("SELECT application_url FROM general_settings")).scalar()
            == "https://reclaimerr.test"
        )
