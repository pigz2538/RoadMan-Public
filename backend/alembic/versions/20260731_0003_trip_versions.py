"""add user-managed trip versions

Revision ID: 20260731_0003
Revises: 20260730_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0003"
down_revision: str | None = "20260730_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trip_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("trip_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("trip_document", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=True),
        sa.Column("plan_markdown", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trip_versions_trip_id", "trip_versions", ["trip_id"])
    op.create_index("ix_trip_versions_created_at", "trip_versions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_trip_versions_created_at", table_name="trip_versions")
    op.drop_index("ix_trip_versions_trip_id", table_name="trip_versions")
    op.drop_table("trip_versions")
