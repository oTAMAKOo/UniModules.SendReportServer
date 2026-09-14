from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ReportData(Base):
    __tablename__ = "report_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    log: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    img_name: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    img_thumbnail_name: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    extend_info: Mapped[str | None] = mapped_column(Text, nullable=True, default="{}")


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
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def has_google(self) -> bool:
        return self.email is not None

    @property
    def is_invite_pending(self) -> bool:
        """招待済みだが初回 Google ログイン待ちの状態（他のログイン手段も持たない）。"""
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
