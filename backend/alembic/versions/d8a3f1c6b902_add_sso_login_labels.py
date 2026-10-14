"""Add customizable SSO login labels.

Revision ID: d8a3f1c6b902
Revises: b6e2f8a4d901
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8a3f1c6b902"
down_revision: str | Sequence[str] | None = "b6e2f8a4d901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("oidc_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("sso_tab_label", sa.String(16), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column(
                "sso_button_label", sa.String(64), nullable=False, server_default=""
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("oidc_settings", schema=None) as batch_op:
        batch_op.drop_column("sso_button_label")
        batch_op.drop_column("sso_tab_label")
