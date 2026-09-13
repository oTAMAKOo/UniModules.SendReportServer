import bcrypt
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdminUser, SystemConfig

_serializer = URLSafeTimedSerializer(settings.secret_key)
SESSION_COOKIE = "session_token"
DEFAULT_SESSION_HOURS = 24


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


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


def check_credentials(db: Session, username: str, password: str) -> AdminUser | None:
    user = db.query(AdminUser).filter(
        AdminUser.username == username,
        AdminUser.is_active == True,
    ).first()
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
        db.commit()
