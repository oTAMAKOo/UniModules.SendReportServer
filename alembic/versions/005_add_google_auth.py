"""add google auth columns to admin_user

Revision ID: 005
Revises: 004
Create Date: 2026-09-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("admin_user", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column("admin_user", sa.Column("google_sub", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_admin_user_email", "admin_user", ["email"])
    op.create_unique_constraint("uq_admin_user_google_sub", "admin_user", ["google_sub"])
    # Google 専用ユーザーはパスワードを持たないため NULL を許容する
    op.alter_column("admin_user", "password_hash", existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    # NOT NULL に戻す前に、パスワード無しのユーザーへ検証不能なダミー値を入れる
    # （bcrypt のハッシュ形式ではないため、どのパスワードとも一致しない）
    op.execute("UPDATE admin_user SET password_hash = '!' WHERE password_hash IS NULL")
    op.alter_column("admin_user", "password_hash", existing_type=sa.String(length=255), nullable=False)
    op.drop_constraint("uq_admin_user_google_sub", "admin_user", type_="unique")
    op.drop_constraint("uq_admin_user_email", "admin_user", type_="unique")
    op.drop_column("admin_user", "google_sub")
    op.drop_column("admin_user", "email")
