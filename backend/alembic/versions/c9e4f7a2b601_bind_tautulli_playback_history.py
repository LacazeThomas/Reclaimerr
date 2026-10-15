"""Bind legacy Tautulli history to an unambiguous Plex server.

Revision ID: c9e4f7a2b601
Revises: b7d2e9a4c601
"""

import sqlalchemy as sa
from alembic import op

revision = "c9e4f7a2b601"
down_revision = "b7d2e9a4c601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    configs = sa.table(
        "service_configs",
        sa.column("id", sa.Integer),
        sa.column("service_type", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("is_main", sa.Boolean),
        sa.column("extra_settings", sa.JSON),
    )
    rows = bind.execute(sa.select(configs)).mappings().all()
    plex = [row for row in rows if row["service_type"] == "PLEX" and row["enabled"]]
    main = next((row for row in plex if row["is_main"]), None)
    default_id = main["id"] if main else plex[0]["id"] if len(plex) == 1 else None
    events = sa.table(
        "playback_history_events",
        sa.column("source_service_config_id", sa.Integer),
        sa.column("observed_service_config_id", sa.Integer),
    )
    for row in rows:
        if row["service_type"] != "TAUTULLI":
            continue
        settings = dict(row["extra_settings"] or {})
        if "plex_service_config_id" in settings:
            continue
        settings["plex_service_config_id"] = default_id
        state = dict(settings.get("playback_history_sync") or {})
        state.pop("last_attempt_at", None)
        state["aggregate_format_version"] = 0
        settings["playback_history_sync"] = state
        bind.execute(
            configs.update()
            .where(configs.c.id == row["id"])
            .values(extra_settings=settings)
        )
        if default_id is not None:
            bind.execute(
                events.update()
                .where(
                    events.c.source_service_config_id == row["id"],
                    events.c.observed_service_config_id.is_(None),
                )
                .values(observed_service_config_id=default_id)
            )


def downgrade() -> None:
    # The extra-settings key and event attribution are compatible with the
    # previous schema; preserve explicit ownership rather than erase it.
    pass
