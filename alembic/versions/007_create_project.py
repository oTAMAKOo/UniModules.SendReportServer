"""create project / project_member and attach reports to a project

Revision ID: 007
Revises: 006
Create Date: 2026-09-15

1 台のサーバーで複数プロジェクトを扱うための移行。

- project / project_member を作成
- 既存データから最初のプロジェクトを 1 つ作る。AES Key/IV は system_config の aes_key / aes_iv
  （無ければ環境変数 REPORT_AES_KEY / REPORT_AES_IV。どちらにも無ければ移行を失敗させる）。
  slug / 表示名は環境変数 INITIAL_PROJECT_SLUG / INITIAL_PROJECT_NAME（既定 default / Default。
  形式不正・予約語なら失敗）
- 全レポートをそのプロジェクトに所属させ、全ユーザーをメンバーにする（superuser は admin）。
  招待中（is_active=False）のユーザーも含める。除外すると招待受諾後に見えるプロジェクトが無くなる
- img_name / img_thumbnail_name をファイル名からストレージキー全体（report/images/xxx.png）へ
  書き換える。S3 / ローカルのファイルは動かさない
- system_config の aes_key / aes_iv を削除（以後はプロジェクトの設定が正）

downgrade は不可逆な部分がある: 007 以降に保存された画像（report/<slug>/images/...）は
旧スキーマ（ファイル名のみ）で表せないため、旧コードからは参照できなくなる。また system_config に
戻すのは id 最小のプロジェクトの鍵だけで、2 つ目以降のプロジェクトの鍵と所属情報は失われる。
"""
import logging
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.authz import validate_slug

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


class InitialProjectConfigError(RuntimeError):
    """最初のプロジェクトを作るための設定が不足・不正。移行を失敗させて明示的な設定を求める。"""


def _initial_project_values(conn) -> dict:
    """最初のプロジェクトに使う slug / name / AES Key / IV を決める。

    不足や形式不正は黙って補わず例外にする。ランダム鍵で作ると受信が 400 のまま原因が
    マイグレーションログにしか残らず、slug を勝手に default にすると配布済みクライアントの
    送信先とずれる。マイグレーションは 1 トランザクションなので失敗しても DB は変わらない。
    """
    slug = (os.environ.get("INITIAL_PROJECT_SLUG") or "default").strip().lower()
    slug_error = validate_slug(slug)
    if slug_error:
        raise InitialProjectConfigError(f"INITIAL_PROJECT_SLUG '{slug}' は使えません: {slug_error}")
    name = (os.environ.get("INITIAL_PROJECT_NAME") or "").strip() or ("Default" if slug == "default" else slug)

    rows = dict(conn.execute(sa.text("SELECT key, value FROM system_config WHERE key IN ('aes_key', 'aes_iv')")).fetchall())
    aes_key = (rows.get("aes_key") or "").strip() or (os.environ.get("REPORT_AES_KEY") or "").strip()
    aes_iv = (rows.get("aes_iv") or "").strip() or (os.environ.get("REPORT_AES_IV") or "").strip()
    if not aes_key or not aes_iv:
        raise InitialProjectConfigError(
            "最初のプロジェクトの AES Key/IV が決まりません。.env に REPORT_AES_KEY（32 文字）と "
            "REPORT_AES_IV（16 文字）を設定してから起動してください（起動後はプロジェクト設定画面 "
            "/p/<slug>/settings で管理します）"
        )
    key_len, iv_len = len(aes_key.encode("utf-8")), len(aes_iv.encode("utf-8"))
    if key_len not in (16, 24, 32) or iv_len != 16:
        raise InitialProjectConfigError(
            f"AES Key は 16 / 24 / 32 バイト、IV は 16 バイトである必要があります（現在 Key={key_len}, IV={iv_len}）"
        )
    if key_len != 32:
        logger.warning("AES Key が %d バイトです。プロジェクト設定画面は 32 文字（AES-256）を要求するため、画面から保存し直すには 32 文字の鍵が必要です", key_len)
    return {"slug": slug, "name": name[:100], "aes_key": aes_key, "aes_iv": aes_iv}


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("aes_key", sa.String(length=32), nullable=False),
        sa.Column("aes_iv", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_project_slug"),
    )
    op.create_table(
        "project_member",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member_project_user"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_project_member_project_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["admin_user.id"], name="fk_project_member_user_id", ondelete="CASCADE"),
    )
    op.create_index("ix_project_member_user_id", "project_member", ["user_id"])

    op.add_column("report_data", sa.Column("project_id", sa.Integer(), nullable=True))

    conn = op.get_bind()
    values = _initial_project_values(conn)
    project_id = conn.execute(
        sa.text(
            "INSERT INTO project (slug, name, aes_key, aes_iv, is_active, created_at) "
            "VALUES (:slug, :name, :aes_key, :aes_iv, TRUE, now()) RETURNING id"
        ),
        values,
    ).scalar_one()
    logger.info("最初のプロジェクトを作成しました: slug=%s name=%s", values["slug"], values["name"])

    conn.execute(sa.text("UPDATE report_data SET project_id = :pid"), {"pid": project_id})
    conn.execute(
        sa.text(
            "INSERT INTO project_member (project_id, user_id, role, created_at) "
            "SELECT :pid, id, CASE WHEN is_superuser THEN 'admin' ELSE 'member' END, now() FROM admin_user"
        ),
        {"pid": project_id},
    )

    # ファイル名だけだった画像列をストレージキー全体にする（既に report/ で始まる行は触らない）
    conn.execute(sa.text(
        "UPDATE report_data SET img_name = 'report/images/' || img_name "
        "WHERE img_name IS NOT NULL AND img_name <> '' AND img_name NOT LIKE 'report/%'"
    ))
    conn.execute(sa.text(
        "UPDATE report_data SET img_thumbnail_name = 'report/thumbnail/' || img_thumbnail_name "
        "WHERE img_thumbnail_name IS NOT NULL AND img_thumbnail_name <> '' AND img_thumbnail_name NOT LIKE 'report/%'"
    ))

    op.alter_column("report_data", "project_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key("fk_report_data_project_id", "report_data", "project", ["project_id"], ["id"], ondelete="RESTRICT")
    # プロジェクト内の一覧は project_id で絞って id 降順に並べるため複合 index にする
    op.create_index("ix_report_data_project_id_id", "report_data", ["project_id", "id"])

    conn.execute(sa.text("DELETE FROM system_config WHERE key IN ('aes_key', 'aes_iv')"))


def downgrade() -> None:
    conn = op.get_bind()

    # 最初のプロジェクト（id 最小）の鍵を system_config に戻す
    row = conn.execute(sa.text("SELECT aes_key, aes_iv FROM project ORDER BY id LIMIT 1")).first()
    if row is not None:
        for key, value in (("aes_key", row[0]), ("aes_iv", row[1])):
            conn.execute(sa.text("DELETE FROM system_config WHERE key = :k"), {"k": key})
            conn.execute(sa.text("INSERT INTO system_config (key, value) VALUES (:k, :v)"), {"k": key, "v": value})

    # 旧形式（ファイル名のみ）に戻せる行だけ戻す。report/<slug>/... の行はそのまま残る
    conn.execute(sa.text("UPDATE report_data SET img_name = regexp_replace(img_name, '^report/images/', '') WHERE img_name LIKE 'report/images/%'"))
    conn.execute(sa.text("UPDATE report_data SET img_thumbnail_name = regexp_replace(img_thumbnail_name, '^report/thumbnail/', '') WHERE img_thumbnail_name LIKE 'report/thumbnail/%'"))

    op.drop_index("ix_report_data_project_id_id", table_name="report_data")
    op.drop_constraint("fk_report_data_project_id", "report_data", type_="foreignkey")
    op.drop_column("report_data", "project_id")
    op.drop_index("ix_project_member_user_id", table_name="project_member")
    op.drop_table("project_member")
    op.drop_table("project")
