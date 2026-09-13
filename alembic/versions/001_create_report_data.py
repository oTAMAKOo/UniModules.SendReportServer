"""create report_data table

Revision ID: 001
Revises:
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_data",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.String(length=255), nullable=True),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        sa.Column("device_model", sa.String(length=255), nullable=True),
        sa.Column("log", sa.Text(), nullable=True, server_default=""),
        sa.Column("img_name", sa.Text(), nullable=True, server_default=""),
        sa.Column("img_thumbnail_name", sa.Text(), nullable=True, server_default=""),
        sa.Column("extend_info", sa.Text(), nullable=True, server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("report_data")
