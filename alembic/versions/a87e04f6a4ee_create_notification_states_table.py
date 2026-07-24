"""create notification states table"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a87e04f6a4ee"
down_revision: str | None = "24d71e4e04e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_name", sa.String(length=50), nullable=False),
        sa.Column("last_value", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("streak_length", sa.Integer(), nullable=False),
        sa.Column("last_alerted_value", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_name"),
    )


def downgrade() -> None:
    op.drop_table("notification_states")
