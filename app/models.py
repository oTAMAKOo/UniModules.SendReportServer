from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# プロジェクト内の役割
ROLE_ADMIN = "admin"    # プロジェクト管理者: メンバー管理・AES 設定・一括削除
ROLE_MEMBER = "member"  # メンバー: レポートの閲覧・削除


class Project(Base):
    """レポートの所属先（Unity プロジェクト）。

    1 台のサーバーで複数プロジェクトを扱うための単位。AES Key/IV はプロジェクトごとに持ち、
    受信 URL（{prefix}/report/{slug}）で振り分ける。slug は URL に使うため小文字英数字とハイフンのみ。
    """

    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aes_key: Mapped[str] = mapped_column(String(32), nullable=False)
    aes_iv: Mapped[str] = mapped_column(String(16), nullable=False)
    # False にすると受信（POST /report/{slug}）が 404 になる。既存レポートの閲覧はできる
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    members: Mapped[list["ProjectMember"]] = relationship(
        "ProjectMember", back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base):
    """ユーザーのプロジェクトへの所属と役割。

    ユーザーアカウント（AdminUser）は全体で 1 つで、この行があるプロジェクトだけが見える。
    システム管理者（AdminUser.is_superuser）は所属が無くても全プロジェクトを管理者として扱う。
    """

    __tablename__ = "project_member"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member_project_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("admin_user.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_MEMBER)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship("Project", back_populates="members")
    user: Mapped["AdminUser"] = relationship("AdminUser", back_populates="memberships")

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class ReportData(Base):
    __tablename__ = "report_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # レポートは必ず 1 つのプロジェクトに属する。プロジェクトはレポートが残っている間は削除できない
    # （CASCADE にすると delete_screenshot が走らず画像が孤児になるため RESTRICT）
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    log: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    # 画像・サムネイルのストレージキー全体（例: report/<slug>/images/xxx.png）。
    # 007 より前の行はマイグレーションで report/images/xxx.png の形に書き換えている
    img_name: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    img_thumbnail_name: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    extend_info: Mapped[str | None] = mapped_column(Text, nullable=True, default="{}")

    # プロジェクトは小さいテーブルなので常に JOIN で取る（一覧・Markdown で slug を参照するため）
    project: Mapped["Project"] = relationship("Project", lazy="joined")


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # Google 専用ユーザーは NULL（パスワードログイン不可）
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Google ログインの許可リスト。小文字で保存する。NULL なら Google ログイン不可。
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # Google アカウントの一意 ID（ID トークンの sub）。初回 Google ログインで紐付く。
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # システム管理者。プロジェクトの作成・削除、全ユーザー管理、全プロジェクトの管理ができる
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    memberships: Mapped[list["ProjectMember"]] = relationship(
        "ProjectMember", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def has_google(self) -> bool:
        return self.email is not None

    @property
    def is_invite_pending(self) -> bool:
        """招待済みだが本人の有効化待ちの状態（Google ログインもパスワードもまだ無い）。

        有効化ページ（/invite/{token}）で本人が Google ログインかパスワード設定を選ぶと
        is_active=True になる。明示カラムは無く、この組み合わせから推論する。
        """
        return (
            self.email is not None
            and self.google_sub is None
            and self.password_hash is None
            and not self.is_active
        )


class ApiToken(Base):
    """読み取り API / MCP 用の個人トークン。

    平文トークンは発行時に一度だけ表示し、DB には SHA-256 ハッシュのみ保存する。
    失効は行の削除で行う（履歴は残さない）。ユーザー削除時は CASCADE で消える。
    トークンはユーザーに属し、見える範囲はそのユーザーの所属プロジェクトで決まる。
    """

    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("admin_user.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["AdminUser"] = relationship("AdminUser")

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.utcnow()

    @property
    def is_usable(self) -> bool:
        return not self.is_expired
