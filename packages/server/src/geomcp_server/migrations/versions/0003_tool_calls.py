"""tool call log

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("client", sa.String(200), nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("duration_s", sa.Float, nullable=False),
        sa.Column("units", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_tool_calls_at", "tool_calls", ["at"])


def downgrade() -> None:
    op.drop_index("ix_tool_calls_at", "tool_calls")
    op.drop_table("tool_calls")
