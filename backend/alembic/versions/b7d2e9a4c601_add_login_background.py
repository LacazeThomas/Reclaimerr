"""Add instance-wide login backgrounds.

Revision ID: b7d2e9a4c601
Revises: f2a6c8e4d901
"""

from alembic import op
import sqlalchemy as sa

revision = "b7d2e9a4c601"
down_revision = "f2a6c8e4d901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("general_settings") as batch:
        batch.add_column(
            sa.Column(
                "login_background_mode",
                sa.String(16),
                nullable=False,
                server_default="default",
            )
        )
        batch.add_column(
            sa.Column(
                "login_background_urls", sa.JSON(), nullable=False, server_default="[]"
            )
        )
        batch.add_column(
            sa.Column("login_background_path", sa.String(255), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "login_background_interval",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("general_settings") as batch:
        for column in (
            "login_background_interval",
            "login_background_path",
            "login_background_urls",
            "login_background_mode",
        ):
            batch.drop_column(column)
