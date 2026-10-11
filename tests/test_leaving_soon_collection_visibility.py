from __future__ import annotations

import importlib
import unittest.mock as mock

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from backend.models.settings import GeneralSettingsResponse
from backend.utils.helpers import LeavingSoonVisibility

MIGRATION = importlib.import_module(
    "backend.alembic.versions.e8a3c6f1d2b7_add_leaving_soon_collection_visibility"
)
_COLUMNS = {"leaving_soon_visible_on_recommended", "leaving_soon_visible_on_home"}


def test_visibility_is_falsy_only_when_all_off() -> None:
    assert not LeavingSoonVisibility()
    assert LeavingSoonVisibility(recommended=True)
    assert LeavingSoonVisibility(home=True)


def test_settings_model_defaults_to_leaving_plex_alone() -> None:
    settings = GeneralSettingsResponse()

    assert settings.leaving_soon_visible_on_recommended is False
    assert settings.leaving_soon_visible_on_home is False


def test_migration_adds_the_columns_defaulted_off(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE general_settings ("
                "id INTEGER PRIMARY KEY, "
                "leaving_soon_enabled BOOLEAN NOT NULL DEFAULT 0)"
            )
        )
        connection.execute(
            text("INSERT INTO general_settings (id, leaving_soon_enabled) VALUES (1, 1)")
        )
        operations = Operations(MigrationContext.configure(connection))

        with mock.patch.object(MIGRATION, "op", operations):
            MIGRATION.upgrade()

            columns = {
                column["name"]
                for column in inspect(connection).get_columns("general_settings")
            }
            assert _COLUMNS <= columns
            # an existing install must not start promoting its collections
            # because of the upgrade itself
            row = connection.execute(
                text(
                    "SELECT leaving_soon_visible_on_recommended, "
                    "leaving_soon_visible_on_home FROM general_settings"
                )
            ).one()
            assert tuple(row) == (0, 0)

            # re-running is a no-op rather than a duplicate-column error
            MIGRATION.upgrade()

            MIGRATION.downgrade()
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("general_settings")
            }
            assert not _COLUMNS & columns
