"""招待リンクの受け口（アカウントの有効化ページ）。

管理者がメールアドレスだけで招待すると、招待中ユーザー（AdminUser.is_invite_pending）が作られ、
署名付きトークン入りのリンク（{prefix}/invite/{token}）がメールで届く（MAIL_MODE=none なら管理者が手渡し）。
本人はこのページで次のどちらかを選んで有効化する。

  - Google で有効化: /invite/{token}/google → 既存の Google 認証フロー（コールバックで email を照合し、
    招待中なら is_active=True にして google_sub を保存する）
  - パスワードを設定して有効化: POST /invite/{token}/activate → ユーザー名を決め、パスワードを保存し、
    is_active=True にしてそのままログイン状態にする

トークンは DB に保存しない（単回利用ではない）。防衛線は「トークンの署名 + uid/email の一致」と
Google コールバックでの email 照合であり、期限切れ・不正なリンクには再発行を依頼する案内を出す。
ログイン前のページなので CSRF トークンは使えず、URL に含まれるトークン自体が POST の正当性の根拠になる。
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    cookie_secure,
    create_session_token,
    get_session_max_age,
    hash_password,
    verify_invite_token,
)
from app.authz import post_login_path
from app.config import settings
from app.database import get_db
from app.models import AdminUser
from app.ratelimit import login_limiter, oauth_limiter
from app.routers.common import redirect, validate_new_password, validate_new_username
from app.routers.google_auth import start_google_auth
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

# 有効化ページの表示状態
STATE_PENDING = "pending"          # 招待中: Google / パスワードのどちらかで有効化できる
STATE_LINK_GOOGLE = "link_google"  # 既に有効なユーザーへの Google 連携リンク（管理者が「Google連携」で発行）
STATE_DONE = "done"                # 既に有効化・連携済み。ログイン画面へ
STATE_BLOCKED = "blocked"          # 無効化されたユーザーなど、このリンクでは有効化できない
STATE_INVALID = "invalid"          # トークンが不正・期限切れ・対象ユーザーが変わっている


def _resolve(token: str, db: Session) -> tuple[AdminUser | None, str]:
    """トークンを検証して (対象ユーザー, 表示状態) を返す。"""
    payload = verify_invite_token(token)
    if not payload:
        return None, STATE_INVALID
    user = db.query(AdminUser).filter(AdminUser.id == payload.get("uid")).first()
    if user is None or user.email is None or user.email != payload.get("email"):
        # 招待後にアドレスを変更した・ユーザーを削除した等。古いリンクは無効
        return None, STATE_INVALID
    if user.is_invite_pending:
        return user, STATE_PENDING
    if user.is_active:
        return user, STATE_LINK_GOOGLE if user.google_sub is None else STATE_DONE
    return user, STATE_BLOCKED


def _page(
    request: Request,
    token: str,
    state: str,
    user: AdminUser | None,
    *,
    username: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    projects = []
    if user is not None:
        projects = sorted((m.project.name for m in user.memberships), key=str.lower)
    return templates.TemplateResponse(
        "invite_accept.html",
        {
            "request": request,
            "token": token,
            "state": state,
            "email": user.email if user else None,
            "username": username or (user.username if user else ""),
            "project_names": projects,
            "error": error,
            "expire_hours": settings.invite_expire_hours,
        },
        status_code=status_code,
    )


@router.get("/invite/{token}", response_class=HTMLResponse)
async def invite_page(token: str, request: Request, db: Session = Depends(get_db)):
    """招待リンク。有効化ページを表示する（状態に応じてボタン・フォーム・案内を出し分ける）。"""
    oauth_limiter.check(request)
    user, state = _resolve(token, db)
    return _page(request, token, state, user, status_code=400 if state == STATE_INVALID else 200)


@router.get("/invite/{token}/google")
async def invite_google(token: str, request: Request, db: Session = Depends(get_db)):
    """「Google で有効化」。招待先アドレスをヒントにして Google 認証へ進める。"""
    oauth_limiter.check(request)
    user, state = _resolve(token, db)
    if state not in (STATE_PENDING, STATE_LINK_GOOGLE):
        return _page(request, token, state, user, status_code=400 if state == STATE_INVALID else 200)
    if not settings.google_enabled:
        return _page(request, token, state, user, error="Google ログインは現在無効になっています。パスワードを設定して有効化してください")
    return start_google_auth(invite_email=user.email)


@router.post("/invite/{token}/activate", response_class=HTMLResponse)
async def invite_activate(
    token: str,
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    """「パスワードを設定して有効化」。ユーザー名とパスワードを保存し、ログイン状態にして一覧へ。"""
    login_limiter.check(request)
    user, state = _resolve(token, db)
    if state != STATE_PENDING:
        # 既に有効化済み等。フォームは受け付けず、状態に応じた案内を出す
        return _page(request, token, state, user, status_code=400 if state == STATE_INVALID else 200)

    username = username.strip()
    error = validate_new_username(db, username, exclude_user_id=user.id) or validate_new_password(password, password_confirm)
    if error:
        return _page(request, token, state, user, username=username, error=error, status_code=400)

    user.username = username
    user.password_hash = hash_password(password)
    user.is_active = True
    user.last_login_at = datetime.utcnow()
    db.commit()
    logger.info("招待を受諾（パスワード設定）: user=%s email=%s", user.username, user.email)

    response = redirect(post_login_path(db, user))
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.username),
        httponly=True,
        secure=cookie_secure(),
        max_age=get_session_max_age(db),
    )
    return response
