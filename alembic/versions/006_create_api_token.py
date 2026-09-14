"""create api_token table

Revision ID: 006
Revises: 005
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Claude Code 等の外部クライアントが読み取り API / MCP を呼ぶための個人トークン。
    # 平文は発行時に一度だけ表示し、DB には SHA-256 ハッシュだけを保存する。
    # 失効は行の削除（履歴は残さない。残すと発行・失効の繰り返しで無限に増えるため）。
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        # 一覧で見分けるための先頭数文字（ハッシュからは復元できないため別に持つ）
        sa.Column("token_prefix", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_api_token_token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["admin_user.id"], name="fk_api_token_user_id", ondelete="CASCADE"),
    )
    op.create_index("ix_api_token_user_id", "api_token", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_token_user_id", table_name="api_token")
    op.drop_table("api_token")
