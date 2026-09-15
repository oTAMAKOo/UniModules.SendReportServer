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
    normalize_email,
    verify_csrf_token,
    verify_session_token,
)
from app.config import settings
from app.mail import MailError, mail_enabled, send_invite_mail
from app.models import AdminUser, Project, SystemConfig


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
    username = EMERGENCY_ADMIN_USERNAME
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


async def issue_invite(
    target: AdminUser,
    note: str | None = None,
    *,
    request: Request | None = None,
    inviter: AdminUser | None = None,
    project: Project | None = None,
) -> dict:
    """招待リンクを発行し、MAIL_MODE に応じてメールを送る。画面表示用の情報を返す。

    リンク先は有効化ページ（/invite/{token}）で、本人が Google ログインかパスワード設定を選ぶ。
    リンクのホストは PUBLIC_BASE_URL。未設定なら request.base_url（nginx 越しでは http になる）で補う。
    inviter / project はメール本文に載せる（誰がどのプロジェクトに招待したか）。
    メール送信（SMTP / SES）は同期 I/O なので、イベントループを塞いでレポート受信まで
    止めないようスレッドプールで実行する。
    """
    token = create_invite_token(target)
    base_url = settings.public_base_url or (str(request.base_url).rstrip("/") if request is not None else "")
    url = f"{base_url}{settings.url_prefix}/invite/{token}"
    result = {
        "username": target.username,
        "email": target.email,
        "url": url,
        "expire_hours": settings.invite_expire_hours,
        "mail_enabled": mail_enabled(),
        "mail_sent": False,
        "mail_error": None,
        "note": note,
        "pending": target.is_invite_pending,
    }
    if mail_enabled():
        try:
            await run_in_threadpool(
                send_invite_mail,
                target.email,
                url,
                inviter_name=inviter.username if inviter else None,
                project_name=project.name if project else None,
            )
            result["mail_sent"] = True
        except MailError as e:
            result["mail_error"] = str(e)
    return result


EMERGENCY_ADMIN_USERNAME = "emergency_admin"
MIN_PASSWORD_LENGTH = 6


def reserved_usernames() -> set[str]:
    """起動時・緊急時の処理が名前で特定するアカウント。プロジェクト管理者には作らせない。

    ensure_admin_google_email は ADMIN_USERNAME のアカウントを superuser に昇格させるため、
    先取りで作られると権限昇格の経路になる。
    """
    return {settings.admin_username, EMERGENCY_ADMIN_USERNAME}


USERNAME_MAX_LENGTH = 64


def validate_new_username(
    db: Session,
    username: str,
    *,
    allow_reserved: bool = False,
    exclude_user_id: int | None = None,
) -> str | None:
    """新しいユーザー名として使えるか検証する。問題なければ None、あればエラー文。

    ユーザー名に '@' は使えない（メンバー追加でメールアドレスと区別するため）。予約名
    （ADMIN_USERNAME / emergency_admin）はシステム管理者（allow_reserved=True）だけが使える。
    exclude_user_id は「自分の現在のユーザー名」を重複扱いしないためのもの（有効化ページで使う）。
    """
    if len(username) < 1 or len(username) > USERNAME_MAX_LENGTH:
        return f"ユーザー名は1〜{USERNAME_MAX_LENGTH}文字で入力してください"
    if "@" in username:
        return "ユーザー名に '@' は使えません（メールアドレスと区別するため）"
    if not allow_reserved and username in reserved_usernames():
        return f"ユーザー名 '{username}' はシステムで予約されているため使えません"
    query = db.query(AdminUser).filter(AdminUser.username == username)
    if exclude_user_id is not None:
        query = query.filter(AdminUser.id != exclude_user_id)
    if query.first():
        return f"ユーザー名 '{username}' は既に存在します"
    return None


def validate_new_password(password: str, confirm: str | None = None) -> str | None:
    """パスワードの長さ（と確認入力の一致）を検証する。問題なければ None。"""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"パスワードは{MIN_PASSWORD_LENGTH}文字以上で入力してください"
    if confirm is not None and password != confirm:
        return "確認用のパスワードが一致しません"
    return None


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    is_superuser: bool = False,
    allow_reserved: bool = False,
) -> tuple[AdminUser | None, str | None]:
    """パスワードでログインするユーザーを管理者が直接作る（緊急用。システム管理者の全ユーザー管理のみ）。

    通常のユーザー追加は create_invited_user + issue_invite（本人が有効化ページでログイン方法を決める）。
    (user, None) を返す。入力に問題があれば (None, エラー文)。commit はここで行う。
    """
    username = username.strip()
    error = validate_new_username(db, username, allow_reserved=allow_reserved)
    if error:
        return None, error
    # 推測されやすい既定パスワードは設定しない。空なら作成させない
    error = validate_new_password(password)
    if error:
        return None, error

    new_user = AdminUser(username=username, password_hash=hash_password(password), is_superuser=is_superuser)
    db.add(new_user)
    db.commit()
    return new_user, None


_USERNAME_SANITIZE_RE = re.compile(r"[^a-z0-9._-]+")


def suggest_username(db: Session, email: str) -> str:
    """メールアドレスの @ より前から、衝突しないユーザー名の初期値を作る。

    英数字と . _ - 以外は '-' に置き換える。既存ユーザーや予約名と衝突したら数字を付ける。
    本人が有効化ページで変えられるので、あくまで初期値。
    """
    local = email.split("@", 1)[0].lower()
    base = _USERNAME_SANITIZE_RE.sub("-", local).strip("-._") or "user"
    base = base[: USERNAME_MAX_LENGTH - 4]
    candidate = base
    suffix = 2
    while validate_new_username(db, candidate) is not None:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate


def create_invited_user(
    db: Session,
    *,
    email: str,
    is_superuser: bool = False,
) -> tuple[AdminUser | None, str | None]:
    """メールアドレスだけで招待中ユーザーを作る。(user, None) か (None, エラー文)。

    password_hash=None / google_sub=None / is_active=False で作るので is_invite_pending が真になる。
    ユーザー名は仮の値（メールアドレス由来）で、本人が有効化ページで決め直す。
    招待リンクの発行とメール送信は呼び出し側（issue_invite）で行う。commit はここで行う。
    Google ログインが未設定でも招待できる（本人はパスワード設定で有効化する）。
    """
    email = normalize_email(email)
    if not email or not is_valid_email(email):
        return None, "メールアドレスの形式が正しくありません"
    if db.query(AdminUser).filter(AdminUser.email == email).first():
        return None, f"メールアドレス '{email}' は既に登録されています"

    new_user = AdminUser(
        username=suggest_username(db, email),
        password_hash=None,
        email=email,
        is_superuser=is_superuser,
        is_active=False,
    )
    db.add(new_user)
    db.commit()
    return new_user, None


def get_config_value(db: Session, key: str, default: str) -> str:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row and row.value else default


def set_config_value(db: Session, key: str, value: str):
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))
