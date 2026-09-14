import logging

import bcrypt
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdminUser, SystemConfig

logger = logging.getLogger(__name__)

_serializer = URLSafeTimedSerializer(settings.secret_key)
SESSION_COOKIE = "session_token"
OAUTH_STATE_COOKIE = "oauth_state"
DEFAULT_SESSION_HOURS = 24
# Google の認可画面を往復してコールバックに戻るまでの猶予（秒）
OAUTH_STATE_MAX_AGE = 600


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    """bcrypt ハッシュと照合する。ハッシュが無い／不正な形式なら常に False。"""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def normalize_email(email: str | None) -> str | None:
    """前後の空白を除いて小文字化する。空なら None。"""
    if email is None:
        return None
    email = email.strip().lower()
    return email or None


def cookie_secure() -> bool:
    """Cookie に Secure 属性を付けるか。

    nginx が TLS を終端するためアプリからは request のスキームで判定できない。
    公開 URL が https なら Secure を付け、ローカル開発（http://127.0.0.1）では付けない。
    """
    return settings.public_base_url.startswith("https://")


def create_session_token(username: str) -> str:
    return _serializer.dumps(username, salt="session")


def get_session_max_age(db: Session) -> int:
    """DBからセッション有効期限（秒）を取得する。未設定時はデフォルト24時間。"""
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_hours").first()
    try:
        hours = int(row.value) if row and row.value else DEFAULT_SESSION_HOURS
    except (ValueError, TypeError):
        hours = DEFAULT_SESSION_HOURS
    return max(1, hours) * 3600


def verify_session_token(token: str, max_age: int | None = None) -> str | None:
    if max_age is None:
        max_age = DEFAULT_SESSION_HOURS * 3600
    try:
        return _serializer.loads(token, salt="session", max_age=max_age)
    except Exception:
        return None


def generate_csrf_token(username: str) -> str:
    """セッションユーザーに紐づくCSRFトークンを生成する。"""
    return _serializer.dumps(username, salt="csrf")


def verify_csrf_token(token: str, max_age: int = 86400) -> str | None:
    """CSRFトークンを検証する。デフォルト24時間有効。"""
    try:
        return _serializer.loads(token, salt="csrf", max_age=max_age)
    except Exception:
        return None


def create_oauth_state(payload: dict) -> str:
    """Google 認可リクエストの state / nonce を署名付きで Cookie に保存するためのトークン。"""
    return _serializer.dumps(payload, salt="oauth_state")


def verify_oauth_state(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return _serializer.loads(token, salt="oauth_state", max_age=OAUTH_STATE_MAX_AGE)
    except Exception:
        return None


def create_invite_token(user: AdminUser) -> str:
    """招待リンク用トークン。ユーザー ID と email を署名付きで埋め込む（DB 保存は不要）。"""
    return _serializer.dumps({"uid": user.id, "email": user.email}, salt="invite")


def verify_invite_token(token: str) -> dict | None:
    try:
        return _serializer.loads(token, salt="invite", max_age=settings.invite_expire_hours * 3600)
    except Exception:
        return None


def check_credentials(db: Session, username: str, password: str) -> AdminUser | None:
    user = db.query(AdminUser).filter(
        AdminUser.username == username,
        AdminUser.is_active == True,
    ).first()
    # Google 専用ユーザー（password_hash が NULL）はここで弾かれる
    if user and verify_password(password, user.password_hash):
        return user
    return None


def ensure_default_admin(db: Session):
    """初回起動時にデフォルト管理者がいなければ作成する。"""
    if db.query(AdminUser).count() == 0:
        admin = AdminUser(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            is_superuser=True,
        )
        db.add(admin)
        try:
            db.commit()
        except IntegrityError:
            # 複数ワーカーが同時に起動して同じ行を作ろうとした場合。もう一方が成功している
            db.rollback()


def ensure_admin_google_email(db: Session):
    """ADMIN_GOOGLE_EMAIL のアカウントが superuser かつ有効であることを起動のたびに保証する。

    ロックアウト復旧用。.env を書き換えられる（= サーバーに SSH できる）人だけが
    設定できるため権限の格上げにはならず、email 自体は秘密情報でもない。
    「Google ログイン時にレコードを作らない」方針を守るため、レコードの保証は
    ログイン時ではなく起動時に行う。
    """
    email = settings.admin_google_email
    if not email:
        return

    user = db.query(AdminUser).filter(AdminUser.email == email).first()
    if user is None:
        user = db.query(AdminUser).filter(AdminUser.username == settings.admin_username).first()
        if user is not None:
            if user.email or user.google_sub:
                logger.warning(
                    "ADMIN_GOOGLE_EMAIL: ユーザー '%s' の Google 連携（%s）を %s に置き換えます",
                    user.username, user.email, email,
                )
            # 別の Google アカウントが連携済みなら解除し、この email の持ち主が連携し直せるようにする
            user.google_sub = None
            user.email = email
        else:
            # Google 専用（パスワード無し）で作る。ここで ADMIN_PASSWORD を付けると、admin を削除済みの
            # 環境で .env の平文パスワード（既定値のままの可能性がある）でログインできる管理者が復活してしまう
            logger.warning(
                "ADMIN_GOOGLE_EMAIL: ユーザー '%s' が存在しないため、%s の Google 専用管理者として作成します",
                settings.admin_username, email,
            )
            user = AdminUser(
                username=settings.admin_username,
                password_hash=None,
                email=email,
                is_superuser=True,
            )
            db.add(user)

    if not user.is_superuser or not user.is_active:
        logger.warning(
            "ADMIN_GOOGLE_EMAIL: 復旧用アカウント '%s' を管理者権限・有効状態に戻しました", user.username
        )
    user.is_superuser = True
    user.is_active = True
    try:
        db.commit()
    except IntegrityError:
        # 複数ワーカーが同時に起動して同じ行を作ろうとした場合。もう一方が成功している
        db.rollback()
