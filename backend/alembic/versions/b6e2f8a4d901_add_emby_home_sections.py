"""Add opt-in Emby home rows and persistent ownership.

Revision ID: b6e2f8a4d901
Revises: e8a3c6f1d2b7
"""

from alembic import op
import sqlalchemy as sa

revision = "b6e2f8a4d901"
down_revision = "e8a3c6f1d2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "leaving_soon_emby_home_enabled" not in {
        col["name"] for col in inspector.get_columns("general_settings")
    }:
        with op.batch_alter_table("general_settings") as batch:
            batch.add_column(
                sa.Column(
                    "leaving_soon_emby_home_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
    if not inspector.has_table("emby_home_sections"):
        op.create_table(
            "emby_home_sections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "service_config_id",
                sa.Integer(),
                sa.ForeignKey("service_configs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("section_id", sa.String(128), nullable=False),
            sa.Column("collection_id", sa.String(64), nullable=False),
            sa.UniqueConstraint(
                "service_config_id", "user_id", "kind", name="uq_emby_home_user_kind"
            ),
        )
        op.create_index(
            "ix_emby_home_sections_service_config_id",
            "emby_home_sections",
            ["service_config_id"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("emby_home_sections"):
        op.drop_table("emby_home_sections")
    if "leaving_soon_emby_home_enabled" in {
        col["name"] for col in inspector.get_columns("general_settings")
    }:
        with op.batch_alter_table("general_settings") as batch:
            batch.drop_column("leaving_soon_emby_home_enabled")
