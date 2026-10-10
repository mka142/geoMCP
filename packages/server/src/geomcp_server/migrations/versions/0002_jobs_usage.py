"""jobs and provider usage

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("eta_s", sa.Float, nullable=False),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "usage",
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("requests", sa.Integer, nullable=False, server_default="0"),
        sa.Column("units", sa.Float, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("usage")
    op.drop_table("jobs")
