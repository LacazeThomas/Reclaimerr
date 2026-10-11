"""Shared rules in docs/ must import cleanly and stay portable between installs."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.routes.rules import import_rules
from backend.core.rule_engine import collect_rule_conditions
from backend.database import Base
from backend.database.models import User
from backend.enums import UserRole
from backend.models.cleanup import RuleImportPayload

DOCS = Path(__file__).resolve().parents[1] / "docs"
COMMUNITY = DOCS / "community-rules"
COMMUNITY_FILES = sorted((COMMUNITY / "rules").glob("*.json"))
FORMAT_PAGE = DOCS / "reference" / "rule-format.md"

# Values that name things on one install only. A shared rule using them would
# import fine and then silently match nothing (or the wrong thing) elsewhere.
INSTALL_SPECIFIC_FIELDS = {
    "library.id",
    "arr.movie_ids",
    "arr.series_ids",
    "seerr.requested_by_user_ids",
    "playback.usernames",
    "playback.fully_watched_usernames",
    "favorites.usernames",
    "playback.user_watched_duration_minutes",
    "playback.user_watched_percent",
}
INSTALL_SPECIFIC_ACTION_KEYS = {
    "radarr_service_config_id",
    "sonarr_service_config_id",
    "radarr_service_config_ids",
    "sonarr_service_config_ids",
    "quality_profile_id",
}
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.json$")


def _load_rules(text: str) -> list[dict[str, Any]]:
    parsed = json.loads(text)
    return parsed if isinstance(parsed, list) else [parsed]


def _format_page_examples() -> list[str]:
    source = FORMAT_PAGE.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", source, flags=re.DOTALL)
    # skip fragments such as the bare definition snippet; only full rule files
    return [block for block in blocks if block.lstrip().startswith("[")]


def _import(rules: list[dict[str, Any]]) -> tuple[int, list[str]]:
    async def run() -> tuple[int, list[str]]:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        async with session_maker() as db:
            admin = User(
                username="admin",
                password_hash="hash",
                role=UserRole.ADMIN,
                permissions=[],
            )
            db.add(admin)
            await db.commit()
            payload = RuleImportPayload.model_validate({"rules": rules})
            result = await import_rules(payload, admin, db)
        await engine.dispose()
        return result.imported, result.errors

    return asyncio.run(run())


def test_community_rules_exist() -> None:
    assert COMMUNITY_FILES


@pytest.mark.parametrize("path", COMMUNITY_FILES, ids=lambda p: p.name)
def test_community_rule_imports_cleanly(path: Path) -> None:
    rules = _load_rules(path.read_text(encoding="utf-8"))
    imported, errors = _import(rules)
    assert errors == []
    assert imported == len(rules)


@pytest.mark.parametrize("path", COMMUNITY_FILES, ids=lambda p: p.name)
def test_community_rule_is_safe_to_share(path: Path) -> None:
    assert SLUG.match(path.name), "use lowercase words and dashes for the name"
    rules = _load_rules(path.read_text(encoding="utf-8"))
    assert len(rules) == 1, "one rule per file"
    rule = rules[0]

    assert rule.get("enabled") is False, "shared rules must be disabled"
    assert str(rule.get("description") or "").strip(), "add a description"

    used = {
        str(condition.get("field"))
        for condition in collect_rule_conditions(rule["definition"])
    }
    assert not used & INSTALL_SPECIFIC_FIELDS, (
        f"remove install-specific fields: {sorted(used & INSTALL_SPECIFIC_FIELDS)}"
    )

    action = rule.get("action") or {}
    leftovers = {key for key in INSTALL_SPECIFIC_ACTION_KEYS if action.get(key)}
    assert not leftovers, f"remove install-specific action keys: {sorted(leftovers)}"
    assert action.get("auto_delete_enabled") is not True, (
        "shared rules must not auto-delete"
    )


def test_every_community_rule_is_listed_once() -> None:
    index = (COMMUNITY / "index.md").read_text(encoding="utf-8")
    for path in COMMUNITY_FILES:
        assert index.count(f"(rules/{path.name})") == 1, (
            f"add one table row for {path.name} to docs/community-rules/index.md"
        )


def test_community_rule_names_are_unique() -> None:
    names = [
        rule["name"]
        for path in COMMUNITY_FILES
        for rule in _load_rules(path.read_text(encoding="utf-8"))
    ]
    assert len(names) == len(set(names))


def test_format_page_has_examples() -> None:
    assert _format_page_examples()


@pytest.mark.parametrize("block", _format_page_examples())
def test_format_page_example_imports_cleanly(block: str) -> None:
    rules = _load_rules(block)
    imported, errors = _import(rules)
    assert errors == []
    assert imported == len(rules)
