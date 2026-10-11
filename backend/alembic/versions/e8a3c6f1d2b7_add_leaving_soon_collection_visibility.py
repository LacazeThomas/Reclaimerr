"""Add the Leaving Soon collection visibility settings.

Revision ID: e8a3c6f1d2b7
Revises: c3d9e2a7f4b1
Create Date: 2026-10-10 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e8a3c6f1d2b7"
down_revision: str | Sequence[str] | None = "c3d9e2a7f4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("leaving_soon_visible_on_recommended", "leaving_soon_visible_on_home")


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.execute(sa.text(f"PRAGMA table_info({table})"))}


def upgrade() -> None:
    # existing installs keep today's behavior: Reclaimerr leaves the Plex hub
    # visibility alone until the user opts in.
    existing = _cols("general_settings")
    missing = [name for name in _COLUMNS if name not in existing]
    if not missing:
        return
    with op.batch_alter_table("general_settings", schema=None) as batch_op:
        for name in missing:
            batch_op.add_column(
                sa.Column(
                    name,
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )


def downgrade() -> None:
    existing = _cols("general_settings")
    present = [name for name in _COLUMNS if name in existing]
    if not present:
        return
    with op.batch_alter_table("general_settings", schema=None) as batch_op:
        for name in present:
            batch_op.drop_column(name)
