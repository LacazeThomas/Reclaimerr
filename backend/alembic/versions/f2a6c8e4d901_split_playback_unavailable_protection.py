"""Separate unavailable playback status protection from active playback protection.

Revision ID: f2a6c8e4d901
Revises: e4b7c2a9f013
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a6c8e4d901"
down_revision = "e4b7c2a9f013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("general_settings") as batch:
        batch.add_column(
            sa.Column(
                "playback_unavailable_protection_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("general_settings") as batch:
        batch.drop_column("playback_unavailable_protection_enabled")
