"""Add administrator login method controls.

Revision ID: e4b7c2a9f013
Revises: d8a3f1c6b902
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c2a9f013"
down_revision: str | Sequence[str] | None = "d8a3f1c6b902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("oidc_settings", schema=None) as batch_op:
        for name in ("allow_local_login", "allow_media_login", "allow_sso_login"):
            batch_op.add_column(
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true())
            )


def downgrade() -> None:
    with op.batch_alter_table("oidc_settings", schema=None) as batch_op:
        for name in ("allow_sso_login", "allow_media_login", "allow_local_login"):
            batch_op.drop_column(name)
