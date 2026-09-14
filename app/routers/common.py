"""管理画面の各ルーターが共有するヘルパー。

リダイレクト、セッションからの現在ユーザー取得、CSRF、管理者保護（最後の有効な
superuser・緊急アカウント）、招待リンク発行、system_config の読み書きを置く。
ルーター本体（admin / project_pages / system_admin）はここを import する。
"""
import re
import secrets
import string

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import (
    SESSION_COOKIE,
    create_invite_token,
    generate_csrf_token,
    get_session_max_age,
    hash_password,
    verify_csrf_token,
    verify_session_token,
)
from app.config import settings
from app.mail import MailError, mail_enabled, send_invite_mail
from app.models import AdminUser, SystemConfig


def redirect(path: str, status_code: int = 302) -> RedirectResponse:
    """プレフィックス付きリダイレクトを生成するヘルパー。

    プレフィックスは settings.url_prefix（テンプレートには Jinja2 グローバル変数
    PREFIX として渡される）。path は "/login" のように先頭スラッシュ付き。
    """
    return RedirectResponse(url=f"{settings.url_prefix}{path}", status_code=status_code)


def get_current_user(request: Request, db: Session) -> AdminUser | None:
    """セッション Cookie から有効なユーザーを取得する。無ければ None。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    max_age = get_session_max_age(db)
    username = verify_session_token(token, max_age=max_age)
    if not username:
        return None
    return db.query(AdminUser).filter(
        AdminUser.username == username,
        AdminUser.is_active == True,
    ).first()


def csrf_token_for(user: AdminUser) -> str:
    """ユーザーに紐づく CSRF トークンを生成する。"""
    return generate_csrf_token(user.username)


def verify_csrf(request_token: str | None, user: AdminUser) -> bool:
    """CSRF トークンを検証する。"""
    if not request_token:
        return False
    username = verify_csrf_token(request_token)
    return username == user.username


def check_and_create_emergency_admin(db: Session) -> dict | None:
    """有効なシステム管理者が 0 人の場合、緊急管理者を自動作成して認証情報を返す。"""
    active_superuser_count = db.query(AdminUser).filter(
        AdminUser.is_superuser == True,
        AdminUser.is_active == True,
    ).count()
    if active_superuser_count > 0:
        return None
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(12))
    username = "emergency_admin"
    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing:
        existing.password_hash = hash_password(password)
        existing.is_superuser = True
        existing.is_active = True
    else:
        db.add(AdminUser(
            username=username,
            password_hash=hash_password(password),
            is_superuser=True,
        ))
    db.commit()
    return {"username": username, "password": password}


def is_last_active_superuser(db: Session, target: AdminUser) -> bool:
    """target を降格・無効化・削除すると有効なシステム管理者が 0 人になるか。"""
    if not (target.is_superuser and target.is_active):
        return False
    count = db.query(AdminUser).filter(
        AdminUser.is_superuser == True,
        AdminUser.is_active == True,
    ).count()
    return count <= 1


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_PATTERN.match(email)) and len(email) <= 255


async def issue_invite(target: AdminUser, note: str | None = None) -> dict:
    """招待リンクを発行し、MAIL_MODE に応じてメールを送る。画面表示用の情報を返す。

    メール送信（SMTP / SES）は同期 I/O なので、イベントループを塞いでレポート受信まで
    止めないようスレッドプールで実行する。
    """
    token = create_invite_token(target)
    url = f"{settings.public_base_url}{settings.url_prefix}/invite/{token}"
    result = {
        "username": target.username,
        "email": target.email,
        "url": url,
        "expire_hours": settings.invite_expire_hours,
        "mail_sent": False,
        "mail_error": None,
        "note": note,
    }
    if mail_enabled():
        try:
            await run_in_threadpool(send_invite_mail, target.email, target.username, url)
            result["mail_sent"] = True
        except MailError as e:
            result["mail_error"] = str(e)
    return result


def get_config_value(db: Session, key: str, default: str) -> str:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row and row.value else default


def set_config_value(db: Session, key: str, value: str):
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))
