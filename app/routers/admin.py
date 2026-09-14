"""管理画面のうちプロジェクトに依存しないルート。

ログイン / ログアウト、プロジェクト選択、管理メニュー、パスワード変更、API トークン、
および旧 URL（/list, /detail/{id} など）からの互換リダイレクト。
プロジェクト配下（/p/{slug}/...）は project_pages.py、システム管理者専用は system_admin.py。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.auth import (
    check_credentials,
    cookie_secure,
    generate_api_token,
    create_session_token,
    get_session_max_age,
    hash_password,
    verify_password,
    SESSION_COOKIE,
)
from app.authz import (
    post_login_path,
    require_user,
    resolve_report_for_user,
    user_projects,
    REPORT_NOT_FOUND_MESSAGE,
)
from app.config import settings
from app.database import get_db
from app.models import ROLE_ADMIN, AdminUser, ApiToken, ReportData
from app.ratelimit import login_limiter
from app.routers.common import (
    csrf_token_for,
    get_current_user,
    redirect,
    verify_csrf,
)
from app.templating import templates

router = APIRouter()


# --- Login / Logout ---


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return redirect(post_login_path(db, user))
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
    admin_user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_session_token(admin_user.username)
    max_age = get_session_max_age(db)
    response = redirect(post_login_path(db, admin_user))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=cookie_secure(), max_age=max_age)
    return response


@router.get("/logout")
async def logout():
    response = redirect("/login")
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- プロジェクト選択 ---


@router.get("/projects", response_class=HTMLResponse)
async def project_select(
    request: Request,
    error: str = "",
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """ログイン後に所属プロジェクトを選ぶ画面。件数と最終投稿日時も見せる。"""
    projects = user_projects(db, user)
    stats = {
        pid: (count, last)
        for pid, count, last in db.query(
            ReportData.project_id, func.count(ReportData.id), func.max(ReportData.created_at)
        ).group_by(ReportData.project_id).all()
    }
    cards = [
        {
            "project": project,
            "role": role,
            "report_count": stats.get(project.id, (0, None))[0],
            "last_report_at": stats.get(project.id, (0, None))[1],
        }
        for project, role in projects
    ]
    messages = {
        "forbidden": "そのプロジェクトへのアクセス権がありません。必要ならプロジェクト管理者に追加を依頼してください",
        "notfound": "指定されたプロジェクトは存在しません",
    }
    return templates.TemplateResponse(
        "projects.html",
        {
            "request": request,
            "user": user.username,
            "is_superuser": user.is_superuser,
            "cards": cards,
            "error": messages.get(error),
        },
    )


# --- Admin Menu ---


@router.get("/admin", response_class=HTMLResponse)
async def admin_menu(request: Request, user: AdminUser = Depends(require_user), db: Session = Depends(get_db)):
    admin_projects = [project for project, role in user_projects(db, user) if role == ROLE_ADMIN]
    return templates.TemplateResponse(
        "admin_menu.html",
        {
            "request": request,
            "user": user.username,
            "is_superuser": user.is_superuser,
            "admin_projects": admin_projects,
        },
    )


# --- Password Change ---


@router.get("/password_change", response_class=HTMLResponse)
async def password_change_page(request: Request, user: AdminUser = Depends(require_user)):
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
            "csrf_token": csrf_token_for(user),
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
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

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


# --- 旧 URL からの互換リダイレクト ---


@router.get("/list")
async def legacy_list(user: AdminUser = Depends(require_user), db: Session = Depends(get_db)):
    """プロジェクト化前の一覧 URL。所属が 1 つならその一覧へ、そうでなければ選択画面へ。"""
    return redirect(post_login_path(db, user))


@router.get("/detail/{report_id}", response_class=HTMLResponse)
async def legacy_detail(
    request: Request,
    report_id: int,
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """プロジェクト化前に共有された詳細 URL を、所属を確認してから新しい URL へ転送する。"""
    report = resolve_report_for_user(db, user, report_id)
    if report is None:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "user": user.username, "message": REPORT_NOT_FOUND_MESSAGE.format(id=report_id)},
            status_code=404,
        )
    return redirect(f"/p/{report.project.slug}/detail/{report.id}")


@router.get("/users")
async def legacy_users(user: AdminUser = Depends(require_user)):
    return redirect("/admin/users" if user.is_superuser else "/admin")


@router.get("/manage")
async def legacy_manage(user: AdminUser = Depends(require_user)):
    return redirect("/projects")


# --- API Tokens（Claude Code 等の外部クライアント用） ---

API_TOKEN_MAX_ACTIVE = 10
API_TOKEN_EXPIRE_CHOICES = (0, 30, 90, 365)  # 0 = 無期限


def _tokens_page(request: Request, user: AdminUser, db: Session, **context):
    """API トークン管理ページを返すヘルパー。context には new_token（発行直後の平文）、error を渡せる。"""
    tokens = (
        db.query(ApiToken)
        .filter(ApiToken.user_id == user.id)
        .order_by(desc(ApiToken.id))
        .all()
    )
    base_url = settings.public_base_url or str(request.base_url).rstrip("/")
    projects = user_projects(db, user)
    return templates.TemplateResponse(
        "api_tokens.html",
        {
            "request": request,
            "user": user.username,
            "tokens": tokens,
            "csrf_token": csrf_token_for(user),
            "mcp_enabled": settings.mcp_enabled,
            "mcp_url": f"{base_url}{settings.url_prefix}/mcp",
            "api_url": f"{base_url}{settings.url_prefix}/api/reports",
            "expire_choices": API_TOKEN_EXPIRE_CHOICES,
            "now": datetime.utcnow(),
            "project_slugs": [p.slug for p, _ in projects],
            **context,
        },
    )


@router.get("/tokens", response_class=HTMLResponse)
async def api_token_list(request: Request, user: AdminUser = Depends(require_user), db: Session = Depends(get_db)):
    return _tokens_page(request, user, db)


@router.post("/tokens/create", response_class=HTMLResponse)
async def api_token_create(
    request: Request,
    name: str = Form(""),
    expires_days: str = Form("0"),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """自分用の API トークンを発行する。平文はこの応答でだけ表示する。"""
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    name = name.strip()
    if len(name) < 1 or len(name) > 100:
        return _tokens_page(request, user, db, error="トークン名は1〜100文字で入力してください（例: 自分の PC 名）")

    try:
        days = int(expires_days)
    except (TypeError, ValueError):
        days = -1
    if days not in API_TOKEN_EXPIRE_CHOICES:
        return _tokens_page(request, user, db, error="有効期限の指定が不正です")

    active_count = sum(1 for t in db.query(ApiToken).filter(ApiToken.user_id == user.id).all() if t.is_usable)
    if active_count >= API_TOKEN_MAX_ACTIVE:
        return _tokens_page(request, user, db, error=f"有効なトークンは {API_TOKEN_MAX_ACTIVE} 個までです。使っていないものを削除してください")

    plain, token_hash, prefix = generate_api_token()
    row = ApiToken(
        user_id=user.id,
        name=name,
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=(datetime.utcnow() + timedelta(days=days)) if days > 0 else None,
    )
    db.add(row)
    db.commit()
    return _tokens_page(request, user, db, new_token={"name": name, "plain": plain})


@router.post("/tokens/delete/{token_id}")
async def api_token_delete(
    request: Request,
    token_id: int,
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """自分のトークンを削除する（= 失効。履歴は残さない）。他人のトークンは対象外。"""
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    row = db.query(ApiToken).filter(ApiToken.id == token_id, ApiToken.user_id == user.id).first()
    if row:
        db.delete(row)
        db.commit()
    return redirect("/tokens")
