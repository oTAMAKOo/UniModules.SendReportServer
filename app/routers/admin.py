import json
import math
import secrets
import string

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.auth import (
    check_credentials,
    create_session_token,
    generate_csrf_token,
    get_session_max_age,
    hash_password,
    verify_csrf_token,
    verify_password,
    verify_session_token,
    SESSION_COOKIE,
)
from app.config import settings
from app.database import get_db
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


def _users_page_with_emergency(request: Request, user: AdminUser, db: Session, emergency: dict | None = None):
    """ユーザー一覧ページを返すヘルパー。緊急アカウント情報がある場合は警告を表示。"""
    users = db.query(AdminUser).order_by(AdminUser.id).all()
    return templates.TemplateResponse(
        "user_list.html",
        {"request": request, "user": user.username, "users": users, "csrf_token": _csrf_token_for(user), "emergency": emergency},
    )


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
    return templates.TemplateResponse(
        "password_change.html",
        {"request": request, "user": user.username, "csrf_token": _csrf_token_for(user), "message": None, "error": None},
    )


@router.post("/password_change", response_class=HTMLResponse)
async def password_change_submit(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    if not verify_password(old_password, user.password_hash):
        return templates.TemplateResponse(
            "password_change.html",
            {"request": request, "user": user.username, "csrf_token": _csrf_token_for(user), "message": None, "error": "現在のパスワードが正しくありません"},
        )

    if len(new_password) < 4:
        return templates.TemplateResponse(
            "password_change.html",
            {"request": request, "user": user.username, "csrf_token": _csrf_token_for(user), "message": None, "error": "新しいパスワードは4文字以上で入力してください"},
        )

    user.password_hash = hash_password(new_password)
    db.commit()
    return templates.TemplateResponse(
        "password_change.html",
        {"request": request, "user": user.username, "csrf_token": _csrf_token_for(user), "message": "パスワードを変更しました", "error": None},
    )


# --- User Management (superuser only) ---


@router.get("/users", response_class=HTMLResponse)
async def user_list(request: Request, db: Session = Depends(get_db)):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    users = db.query(AdminUser).order_by(AdminUser.id).all()
    return templates.TemplateResponse(
        "user_list.html",
        {"request": request, "user": user.username, "users": users, "csrf_token": _csrf_token_for(user)},
    )


@router.post("/users/create", response_class=HTMLResponse)
async def user_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    is_superuser: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_current_user(request, db)
    if not user or not user.is_superuser:
        return _redirect("/login")

    if not _verify_csrf(csrf_token, user):
        return _redirect("/login")

    if len(username.strip()) < 1 or len(username) > 64:
        users = db.query(AdminUser).order_by(AdminUser.id).all()
        return templates.TemplateResponse(
            "user_list.html",
            {"request": request, "user": user.username, "users": users, "csrf_token": _csrf_token_for(user), "error": "ユーザー名は1〜64文字で入力してください"},
        )

    # パスワード未入力時はデフォルト値を設定.
    if not password:
        password = "password"

    if len(password) < 4:
        users = db.query(AdminUser).order_by(AdminUser.id).all()
        return templates.TemplateResponse(
            "user_list.html",
            {"request": request, "user": user.username, "users": users, "csrf_token": _csrf_token_for(user), "error": "パスワードは4文字以上で入力してください"},
        )

    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing:
        users = db.query(AdminUser).order_by(AdminUser.id).all()
        return templates.TemplateResponse(
            "user_list.html",
            {"request": request, "user": user.username, "users": users, "csrf_token": _csrf_token_for(user), "error": f"ユーザー名 '{username}' は既に存在します"},
        )

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
        db.delete(target)
        db.commit()
        emergency = _check_and_create_emergency_admin(db)
        if emergency:
            return _users_page_with_emergency(request, user, db, emergency)
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
        target.is_active = not target.is_active
        db.commit()
        emergency = _check_and_create_emergency_admin(db)
        if emergency:
            return _users_page_with_emergency(request, user, db, emergency)
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
        target.is_superuser = not target.is_superuser
        db.commit()
        emergency = _check_and_create_emergency_admin(db)
        if emergency:
            return _users_page_with_emergency(request, user, db, emergency)
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
        users = db.query(AdminUser).order_by(AdminUser.id).all()
        return templates.TemplateResponse(
            "user_list.html",
            {"request": request, "user": user.username, "users": users, "csrf_token": _csrf_token_for(user), "error": "パスワードは4文字以上で入力してください"},
        )

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target:
        target.password_hash = hash_password(new_password)
        db.commit()
    return _redirect("/users")


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
