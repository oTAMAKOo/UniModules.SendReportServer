import json
import math
import re
import secrets
import string

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.auth import (
    check_credentials,
    create_invite_token,
    create_session_token,
    generate_csrf_token,
    get_session_max_age,
    hash_password,
    normalize_email,
    verify_csrf_token,
    verify_password,
    verify_session_token,
    SESSION_COOKIE,
)
from app.config import settings
from app.database import get_db
from app.mail import MailError, mail_enabled, send_invite_mail
from app.models import AdminUser, ReportData, SystemConfig
from app.ratelimit import login_limiter
from app.storage import delete_screenshot, get_image_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ITEMS_PER_PAGE = 25


def _redirect(path: str, status_code: int = 302) -> RedirectResponse:
    """プレフィックス付きリダイレクトを生成するヘルパー。

    プレフィックスは settings.url_prefix（テンプレートにはJinja2グローバル変数
    PREFIX として渡される）。
    """
    return RedirectResponse(url=f"{settings.url_prefix}{path}", status_code=status_code)


def _get_current_user(request: Request, db: Session) -> AdminUser | None:
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


def _csrf_token_for(user: AdminUser) -> str:
    """ユーザーに紐づくCSRFトークンを生成する。"""
    return generate_csrf_token(user.username)


def _verify_csrf(request_token: str | None, user: AdminUser) -> bool:
    """CSRFトークンを検証する。"""
    if not request_token:
        return False
    username = verify_csrf_token(request_token)
    return username == user.username


def _format_log(raw_log: str | None) -> list[dict]:
    """ログテキストを表示用に整形する。"""
    if not raw_log:
        return []
    try:
        log_data = json.loads(raw_log)
        if isinstance(log_data, dict) and "contents" in log_data:
            entries = []
            for item in log_data["contents"]:
                if isinstance(item, dict):
                    entries.append({
                        "message": item.get("message", ""),
                        "stacktrace": item.get("stackTrace", item.get("stacktrace", "")),
                        "logType": item.get("type", item.get("logType", 3)),
                    })
                else:
                    entries.append({
                        "message": str(item),
                        "stacktrace": "",
                        "logType": 0,
                    })
            return entries
        return [{"message": json.dumps(log_data, indent=2, ensure_ascii=False),
                 "stacktrace": "", "logType": 0}]
    except (json.JSONDecodeError, TypeError):
        return [{"message": raw_log, "stacktrace": "", "logType": 0}]


# --- Login / Logout ---


def _check_and_create_emergency_admin(db: Session) -> dict | None:
    """アクティブな管理者が0人の場合、緊急管理者を自動作成して認証情報を返す。"""
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


def _users_page(request: Request, user: AdminUser, db: Session, **context):
    """ユーザー一覧ページを返すヘルパー。

    context には error（エラー文）、emergency（緊急アカウント情報）、
    invite（発行した招待リンクの情報）を渡せる。
    """
    users = db.query(AdminUser).order_by(AdminUser.id).all()
    return templates.TemplateResponse(
        "user_list.html",
        {"request": request, "user": user.username, "users": users, "csrf_token": _csrf_token_for(user), **context},
    )


def _is_last_active_superuser(db: Session, target: AdminUser) -> bool:
    """target を降格・無効化・削除すると有効な管理者が 0 人になるか。"""
    if not (target.is_superuser and target.is_active):
        return False
    count = db.query(AdminUser).filter(
        AdminUser.is_superuser == True,
        AdminUser.is_active == True,
    ).count()
    return count <= 1


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_PATTERN.match(email)) and len(email) <= 255


def _issue_invite(target: AdminUser) -> dict:
    """招待リンクを発行し、MAIL_MODE に応じてメールを送る。画面表示用の情報を返す。"""
    token = create_invite_token(target)
    url = f"{settings.public_base_url}{settings.url_prefix}/invite/{token}"
    result = {
        "username": target.username,
        "email": target.email,
        "url": url,
        "expire_hours": settings.invite_expire_hours,
        "mail_sent": False,
        "mail_error": None,
    }
    if mail_enabled():
        try:
            send_invite_mail(target.email, target.username, url)
            result["mail_sent"] = True
        except MailError as e:
            result["mail_error"] = str(e)
    return result


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = _get_current_user(request, db)
    if user:
        return _redirect("/list")
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    login_limiter.check(request)
    admin_user = check_credentials(db, username, password)
    if not admin_user:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid credentials"}
        )
    from datetime import datetime
    admin_user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_session_token(admin_user.username)
    max_age = get_session_max_age(db)
    response = _redirect("/list")
    response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=max_age)
    return response


@router.get("/logout")
async def logout():
    response = _redirect("/login")
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- Admin Menu ---


@router.get("/admin", response_class=HTMLResponse)
async def admin_menu(request: Request, db: Session = Depends(get_db)):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")
    return templates.TemplateResponse(
        "admin_menu.html",
        {"request": request, "user": user.username, "is_superuser": user.is_superuser},
    )


# --- Password Change ---


@router.get("/password_change", response_class=HTMLResponse)
async def password_change_page(request: Request, db: Session = Depends(get_db)):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")
    return _password_change_page(request, user)


def _password_change_page(request: Request, user: AdminUser, message: str | None = None, error: str | None = None):
    """パスワード変更ページを返すヘルパー。

    Google 専用ユーザー（パスワード未設定）には「パスワードを設定する」画面として
    表示し、現在のパスワードの入力は求めない。
    """
    return templates.TemplateResponse(
        "password_change.html",
        {
            "request": request,
            "user": user.username,
            "csrf_token": _csrf_token_for(user),
            "has_password": user.has_password,
            "message": message,
            "error": error,
        },
    )


@router.post("/password_change", response_class=HTMLResponse)
async def password_change_submit(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    # パスワード未設定（Google 専用）のユーザーは現在のパスワード無しで設定できる。
    # セッションが Google 認証で確立済みであることが本人確認の代わりになる。
    if user.has_password and not verify_password(old_password, user.password_hash):
        return _password_change_page(request, user, error="現在のパスワードが正しくありません")

    if len(new_password) < 4:
        return _password_change_page(request, user, error="新しいパスワードは4文字以上で入力してください")

    message = "パスワードを変更しました" if user.has_password else "パスワードを設定しました。次回からパスワードでもログインできます"
    user.password_hash = hash_password(new_password)
    db.commit()
    return _password_change_page(request, user, message=message)


# --- User Management (superuser only) ---


@router.get("/users", response_class=HTMLResponse)
async def user_list(request: Request, db: Session = Depends(get_db)):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    return _users_page(request, user, db)


@router.post("/users/create", response_class=HTMLResponse)
async def user_create(
    request: Request,
    username: str = Form(...),
    login_method: str = Form("password"),
    password: str = Form(""),
    email: str = Form(""),
    is_superuser: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """ユーザーを作成する。

    login_method が "password" なら従来どおりパスワードユーザーを作る。
    "google" なら email だけを持つ招待中ユーザー（is_active=False）を作り、
    招待リンクを発行する。本人がリンクからその Google アカウントでログインすると有効化される。
    """
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    username = username.strip()
    if len(username) < 1 or len(username) > 64:
        return _users_page(request, user, db, error="ユーザー名は1〜64文字で入力してください")

    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing:
        return _users_page(request, user, db, error=f"ユーザー名 '{username}' は既に存在します")

    if login_method == "google":
        if not settings.google_enabled:
            return _users_page(request, user, db, error="Google ログインが設定されていないため、Google ユーザーは作成できません（.env の GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / PUBLIC_BASE_URL）")

        email = normalize_email(email)
        if not email or not _is_valid_email(email):
            return _users_page(request, user, db, error="メールアドレスの形式が正しくありません")

        if db.query(AdminUser).filter(AdminUser.email == email).first():
            return _users_page(request, user, db, error=f"メールアドレス '{email}' は既に登録されています")

        new_user = AdminUser(
            username=username,
            password_hash=None,
            email=email,
            is_superuser=is_superuser,
            is_active=False,
        )
        db.add(new_user)
        db.commit()
        return _users_page(request, user, db, invite=_issue_invite(new_user))

    if login_method != "password":
        return _users_page(request, user, db, error="ログイン方法が不正です")

    # パスワード未入力時はデフォルト値を設定.
    if not password:
        password = "password"

    if len(password) < 4:
        return _users_page(request, user, db, error="パスワードは4文字以上で入力してください")

    new_user = AdminUser(
        username=username,
        password_hash=hash_password(password),
        is_superuser=is_superuser,
    )
    db.add(new_user)
    db.commit()
    return _redirect("/users")


@router.post("/users/delete/{user_id}")
async def user_delete(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target and target.id != user.id:
        if _is_last_active_superuser(db, target):
            return _users_page(request, user, db, error="最後の有効な管理者は削除できません")
        db.delete(target)
        db.commit()
        emergency = _check_and_create_emergency_admin(db)
        if emergency:
            return _users_page(request, user, db, emergency=emergency)
    return _redirect("/users")


@router.post("/users/toggle/{user_id}")
async def user_toggle_active(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target and target.id != user.id:
        if target.is_active and _is_last_active_superuser(db, target):
            return _users_page(request, user, db, error="最後の有効な管理者は無効化できません")
        target.is_active = not target.is_active
        db.commit()
        emergency = _check_and_create_emergency_admin(db)
        if emergency:
            return _users_page(request, user, db, emergency=emergency)
    return _redirect("/users")


@router.post("/users/toggle_superuser/{user_id}")
async def user_toggle_superuser(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target:
        if target.is_superuser and _is_last_active_superuser(db, target):
            return _users_page(request, user, db, error="最後の有効な管理者の権限は解除できません")
        target.is_superuser = not target.is_superuser
        db.commit()
        emergency = _check_and_create_emergency_admin(db)
        if emergency:
            return _users_page(request, user, db, emergency=emergency)
    return _redirect("/users")


@router.post("/users/reset_password/{user_id}")
async def user_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    if len(new_password) < 4:
        return _users_page(request, user, db, error="パスワードは4文字以上で入力してください")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target:
        target.password_hash = hash_password(new_password)
        db.commit()
    return _redirect("/users")


@router.post("/users/set_email/{user_id}", response_class=HTMLResponse)
async def user_set_email(
    request: Request,
    user_id: int,
    email: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """既存ユーザーに Google ログインを付与・変更・解除する。

    email を設定すると招待リンクを発行する（本人が初回 Google ログインで連携）。
    空で送ると連携を解除する。パスワードを持たないユーザーの解除は
    ログイン手段が無くなるため拒否する。
    """
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target:
        return _redirect("/users")

    email = normalize_email(email)
    if email is None:
        if not target.has_password:
            return _users_page(request, user, db, error=f"'{target.username}' はパスワードを持たないため、Google 連携を解除するとログインできなくなります。先にパスワードを設定してください")
        target.email = None
        target.google_sub = None
        db.commit()
        return _redirect("/users")

    if not settings.google_enabled:
        return _users_page(request, user, db, error="Google ログインが設定されていないため、Google 連携は設定できません（.env の GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / PUBLIC_BASE_URL）")

    if not _is_valid_email(email):
        return _users_page(request, user, db, error="メールアドレスの形式が正しくありません")

    if email == target.email:
        return _redirect("/users")

    duplicate = db.query(AdminUser).filter(AdminUser.email == email, AdminUser.id != target.id).first()
    if duplicate:
        return _users_page(request, user, db, error=f"メールアドレス '{email}' は既に '{duplicate.username}' に登録されています")

    target.email = email
    # 別アドレスの持ち主が連携し直せるよう、既存の Google 連携は解除する
    target.google_sub = None
    db.commit()
    return _users_page(request, user, db, invite=_issue_invite(target))


@router.post("/users/reinvite/{user_id}", response_class=HTMLResponse)
async def user_reinvite(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """招待リンクを再発行する（有効期限切れ・メール不達時用）。"""
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target or not target.has_google or target.google_sub is not None:
        return _redirect("/users")

    if not settings.google_enabled:
        return _users_page(request, user, db, error="Google ログインが設定されていないため、招待リンクは発行できません")

    return _users_page(request, user, db, invite=_issue_invite(target))


# --- System Config ---


def _get_config_value(db: Session, key: str, default: str) -> str:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row and row.value else default


def _set_config_value(db: Session, key: str, value: str):
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))


@router.get("/system", response_class=HTMLResponse)
async def system_config_page(request: Request, db: Session = Depends(get_db)):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    aes_key = _get_config_value(db, "aes_key", settings.report_aes_key)
    aes_iv = _get_config_value(db, "aes_iv", settings.report_aes_iv)
    session_hours = _get_config_value(db, "session_hours", "24")

    return templates.TemplateResponse(
        "system_config.html",
        {"request": request, "user": user.username, "aes_key": aes_key, "aes_iv": aes_iv, "session_hours": session_hours, "csrf_token": _csrf_token_for(user), "message": None, "error": None},
    )


@router.post("/system", response_class=HTMLResponse)
async def system_config_submit(
    request: Request,
    aes_key: str = Form(...),
    aes_iv: str = Form(...),
    session_hours: str = Form("24"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    tpl_vars = {"request": request, "user": user.username, "aes_key": aes_key, "aes_iv": aes_iv, "session_hours": session_hours, "csrf_token": _csrf_token_for(user), "message": None, "error": None}

    if len(aes_key.encode("utf-8")) != 32:
        tpl_vars["error"] = "AES Keyは32文字で指定してください"
        return templates.TemplateResponse("system_config.html", tpl_vars)
    if len(aes_iv.encode("utf-8")) != 16:
        tpl_vars["error"] = "AES IVは16文字で指定してください"
        return templates.TemplateResponse("system_config.html", tpl_vars)

    try:
        hours = int(session_hours)
        if hours < 1 or hours > 8760:
            raise ValueError
    except (ValueError, TypeError):
        tpl_vars["error"] = "セッション有効期限は1〜8760（時間）の整数で入力してください"
        return templates.TemplateResponse("system_config.html", tpl_vars)

    _set_config_value(db, "aes_key", aes_key)
    _set_config_value(db, "aes_iv", aes_iv)
    _set_config_value(db, "session_hours", str(hours))
    db.commit()

    tpl_vars["session_hours"] = str(hours)
    tpl_vars["message"] = "設定を保存しました"
    return templates.TemplateResponse("system_config.html", tpl_vars)


# --- Report Manage ---


@router.get("/manage", response_class=HTMLResponse)
async def report_manage_page(request: Request, db: Session = Depends(get_db)):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")
    if not user.is_superuser:
        return _redirect("/admin")

    total_count = db.query(func.count(ReportData.id)).scalar()
    return templates.TemplateResponse(
        "report_manage.html",
        {"request": request, "user": user.username, "total_count": total_count, "csrf_token": _csrf_token_for(user), "message": None, "error": None},
    )


@router.post("/manage/bulk_delete")
async def report_bulk_delete(
    request: Request,
    date_from: str = Form(None),
    date_to: str = Form(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")
    if not user.is_superuser:
        return _redirect("/admin")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    from datetime import datetime, timedelta

    query = db.query(ReportData)

    if not date_from and not date_to:
        total_count = db.query(func.count(ReportData.id)).scalar()
        return templates.TemplateResponse(
            "report_manage.html",
            {"request": request, "user": user.username, "total_count": total_count, "csrf_token": _csrf_token_for(user), "message": None, "error": "日付を指定してください"},
        )

    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(ReportData.created_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(ReportData.created_at < dt_to)
        except ValueError:
            pass

    # 画像ファイル削除
    reports = query.all()
    deleted_count = len(reports)
    for report in reports:
        delete_screenshot(report.img_name, report.img_thumbnail_name)
        db.delete(report)
    db.commit()

    total_count = db.query(func.count(ReportData.id)).scalar()
    return templates.TemplateResponse(
        "report_manage.html",
        {"request": request, "user": user.username, "total_count": total_count, "csrf_token": _csrf_token_for(user), "message": f"{deleted_count} 件のレポートを削除しました", "error": None},
    )


# --- Report List ---


@router.get("/list", response_class=HTMLResponse)
async def report_list(
    request: Request,
    page: int = 1,
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")

    from datetime import datetime, timedelta

    query = db.query(ReportData)

    # テキスト部分一致フィルタ（全テキストフィールド対象）
    if q.strip():
        like_pattern = f"%{q.strip()}%"
        query = query.filter(
            (ReportData.title.ilike(like_pattern))
            | (ReportData.user_name.ilike(like_pattern))
            | (ReportData.user_id.ilike(like_pattern))
            | (ReportData.device_model.ilike(like_pattern))
            | (ReportData.log.ilike(like_pattern))
            | (ReportData.extend_info.ilike(like_pattern))
        )

    # 日付フィルタ
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(ReportData.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(ReportData.created_at < dt_to)
        except ValueError:
            pass

    total = query.count()
    total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * ITEMS_PER_PAGE

    reports = (
        query
        .order_by(desc(ReportData.id))
        .offset(offset)
        .limit(ITEMS_PER_PAGE)
        .all()
    )

    # サムネイルURLを付与
    for r in reports:
        if r.img_thumbnail_name:
            r.thumb_url = get_image_url(f"report/thumbnail/{r.img_thumbnail_name}")
        else:
            r.thumb_url = None

    return templates.TemplateResponse(
        "report_list.html",
        {
            "request": request,
            "reports": reports,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "user": user.username,
            "q": q,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


# --- Report Delete ---


@router.post("/delete/{report_id}")
async def report_delete(
    request: Request,
    report_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    report = db.query(ReportData).filter(ReportData.id == report_id).first()
    if report:
        # 画像ファイルも削除
        delete_screenshot(report.img_name, report.img_thumbnail_name)
        db.delete(report)
        db.commit()
    return _redirect("/list")


# --- Report Detail ---


@router.get("/detail/{report_id}", response_class=HTMLResponse)
async def report_detail(
    request: Request, report_id: int, db: Session = Depends(get_db)
):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")

    report = db.query(ReportData).filter(ReportData.id == report_id).first()
    if not report:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "user": user.username, "content": "Report not found"},
            status_code=404,
        )

    log_entries = _format_log(report.log)

    img_url = None
    thumb_url = None
    if report.img_name:
        img_url = get_image_url(f"report/images/{report.img_name}")
        thumb_url = get_image_url(f"report/thumbnail/{report.img_thumbnail_name}")

    extend_info = {}
    if report.extend_info:
        try:
            extend_info = json.loads(report.extend_info)
        except json.JSONDecodeError:
            pass

    return templates.TemplateResponse(
        "report_detail.html",
        {
            "request": request,
            "report": report,
            "log_entries": log_entries,
            "img_url": img_url,
            "thumb_url": thumb_url,
            "extend_info": extend_info,
            "user": user.username,
            "csrf_token": _csrf_token_for(user),
        },
    )
