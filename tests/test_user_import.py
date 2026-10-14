import asyncio
import io

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes.account import import_users_csv, router
from backend.core.auth import get_current_user, verify_password
from backend.database import Base
from backend.database.models import GeneralSettings, User
from backend.enums import Permission, UserRole
from backend.services.user_import import (
    CSV_TEMPLATE,
    MAX_CSV_BYTES,
    parse_user_csv,
    summarize_import,
)


def csv_bytes(*rows: str) -> bytes:
    return (CSV_TEMPLATE + "\n".join(rows)).encode("utf-8")


def user(username="existing", email="existing@example.com", id=1):
    result = User(
        username=username,
        email=email,
        password_hash="original",
        role=UserRole.USER,
        permissions=[],
    )
    result.id = id
    return result


def test_csv_bom_quoted_names_and_passwordless_users():
    entries = parse_user_csv(
        b"\xef\xbb\xbf"
        + csv_bytes('tester,tester@example.com,"Doe, Jane",', "localuser,,,secret"),
        [],
    )
    assert summarize_import(entries).ready == 2
    assert entries[0].request.display_name == "Doe, Jane"
    assert not entries[0].has_password
    assert entries[1].has_password


@pytest.mark.parametrize(
    "contents",
    [
        b"email,username\n",
        CSV_TEMPLATE.encode(),
        b"\xff",
        csv_bytes('"unterminated'),
        b"x" * (MAX_CSV_BYTES + 1),
        csv_bytes(*["tester,tester@example.com,," for _ in range(1001)]),
    ],
    ids=["header", "empty", "encoding", "quotes", "size", "row-limit"],
)
def test_invalid_files(contents):
    with pytest.raises(ValueError):
        parse_user_csv(contents, [])


@pytest.mark.parametrize(
    "row",
    [
        "bad,bad@example.com,,secret",
        "validuser,not-email,,secret",
        "validuser,,,",
        "validuser,valid@example.com,,x",
        "validuser,valid@example.com,xx,secret",
        "validuser,valid@example.com,,,extra",
    ],
)
def test_invalid_rows(row):
    assert summarize_import(parse_user_csv(csv_bytes(row), [])).errors == 1


def test_duplicates_conflicts_and_email_matching():
    existing = user()
    other = user("otheruser", "other@example.com", 2)
    entries = parse_user_csv(
        csv_bytes(
            "newname,EXISTING@example.com,,new-password",
            "existing,other@example.com,,secret",
            "otheruser,different@example.com,,secret",
            "duplicate,dup@example.com,,secret",
            "DUPLICATE,unique@example.com,,secret",
        ),
        [existing, other],
    )
    assert entries[0].result.status == "skipped"
    assert all(entry.result.status == "error" for entry in entries[1:])
    assert existing.password_hash == "original"


def test_ambiguous_email_and_duplicate_email():
    entries = parse_user_csv(
        csv_bytes("newuser,same@example.com,,secret"),
        [user(email="same@example.com"), user("another", "same@example.com", 2)],
    )
    assert entries[0].result.status == "error"
    entries = parse_user_csv(
        csv_bytes(
            "firstuser,same@example.com,,secret", "seconduser,SAME@example.com,,secret"
        ),
        [],
    )
    assert all(entry.result.status == "error" for entry in entries)


def test_password_never_returned():
    entries = parse_user_csv(
        csv_bytes("tester,tester@example.com,," + "PRIVATE" * 20), []
    )
    assert "PRIVATE" not in summarize_import(entries).model_dump_json()


def test_preview_import_defaults_reimport_and_atomic_validation():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            actor = user("manager", "manager@example.com")
            actor.role = UserRole.ADMIN
            db.add(actor)
            db.add(GeneralSettings(default_allowed_pages=["movies", "settings"]))
            await db.commit()

            async def upload(contents, preview):
                return await import_users_csv(
                    actor, UploadFile(io.BytesIO(contents)), preview, db
                )

            contents = csv_bytes(
                "mediauser,media@example.com,,",
                "localuser,local@example.com,Local User,temp-password",
            )
            response = await upload(contents, True)
            assert response.ready == 2 and response.created == 0
            assert len((await db.execute(select(User))).scalars().all()) == 1
            blocked = await upload(
                csv_bytes(
                    "gooduser,good@example.com,,secret", "bad,bad@example.com,,secret"
                ),
                False,
            )
            assert blocked.errors == 1 and blocked.created == 0
            assert len((await db.execute(select(User))).scalars().all()) == 1

            response = await upload(contents, False)
            assert response.created == 2 and response.ready == 0
            users = {
                u.username: u for u in (await db.execute(select(User))).scalars().all()
            }
            assert users["mediauser"].password_hash == ""
            assert not users["mediauser"].require_password_change
            local = users["localuser"]
            assert verify_password("temp-password", local.password_hash)
            assert local.require_password_change
            assert local.permissions == [Permission.REQUEST.value]
            assert local.allowed_pages == ["movies", "settings"]
            assert local.role == UserRole.USER
            assert (await upload(contents, False)).skipped == 2
            # Revalidate against changes made after preview.
            stale = csv_bytes("staleuser,stale@example.com,,secret")
            assert (await upload(stale, True)).ready == 1
            db.add(user("staleuser", "different@example.com", 10))
            await db.commit()
            assert (await upload(stale, False)).errors == 1
        await engine.dispose()

    asyncio.run(run())


def test_routes_require_manage_users_and_template_header():
    app = FastAPI()
    app.include_router(router)
    actor = user()

    async def current_user():
        return actor

    app.dependency_overrides[get_current_user] = current_user
    with TestClient(app) as client:
        assert client.get("/api/account/users/import/template").status_code == 403
        assert (
            client.post(
                "/api/account/users/import", files={"file": ("users.csv", CSV_TEMPLATE)}
            ).status_code
            == 403
        )
        actor.permissions = [Permission.MANAGE_USERS.value]
        response = client.get("/api/account/users/import/template")
        assert response.status_code == 200
        assert response.text == CSV_TEMPLATE
        assert "attachment" in response.headers["content-disposition"]


def test_database_conflict_rolls_back_whole_import(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    calls = 0

    async def fail_on_second_user(db, user):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IntegrityError("insert", {}, Exception("conflict"))

    monkeypatch.setattr(
        "backend.api.routes.account.auto_enable_system_email", fail_on_second_user
    )

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            contents = csv_bytes(
                "firstuser,first@example.com,,", "seconduser,second@example.com,,"
            )
            with pytest.raises(HTTPException) as exc:
                await import_users_csv(
                    user(), UploadFile(io.BytesIO(contents)), False, db
                )
            assert exc.value.status_code == 409
            assert not (await db.execute(select(User))).scalars().all()
        await engine.dispose()

    asyncio.run(run())
