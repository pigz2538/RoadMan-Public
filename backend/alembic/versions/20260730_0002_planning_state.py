"""add planning state and rendered roadbook

Revision ID: 20260730_0002
Revises: 20260730_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0002"
down_revision: str | None = "20260730_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("state_json", sa.Text(), nullable=True))
    op.add_column("trips", sa.Column("plan_markdown", sa.Text(), nullable=True))
    op.add_column(
        "trips",
        sa.Column("messages_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("trips", "messages_json")
    op.drop_column("trips", "plan_markdown")
    op.drop_column("trips", "state_json")
