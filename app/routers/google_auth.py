"""Google ログインと招待リンクのルート。

GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / PUBLIC_BASE_URL が揃っていないときは
全ルートが 404 を返し、ログイン画面にも Google ボタンは表示されない。

Google ログインで AdminUser が新規作成されることはない。許可判定は
「AdminUser.email に一致するレコードが既に在るか」のみで、初回ログイン時に
行うのは google_sub の保存（と招待の受諾による有効化）だけ。
"""
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import google_auth
from app.auth import (
    OAUTH_STATE_COOKIE,
    OAUTH_STATE_MAX_AGE,
    SESSION_COOKIE,
    create_oauth_state,
    create_session_token,
    get_session_max_age,
    verify_invite_token,
    verify_oauth_state,
)
from app.config import settings
from app.database import get_db
from app.models import AdminUser
from app.routers.admin import _redirect, templates

router = APIRouter()


def _require_google_enabled():
    if not settings.google_enabled:
        raise HTTPException(status_code=404)


def _login_error(request: Request, message: str) -> HTMLResponse:
    """ログイン画面にエラーを表示し、OAuth 用 Cookie を破棄する。"""
    response = templates.TemplateResponse(
        "login.html", {"request": request, "error": message}, status_code=400
    )
    response.delete_cookie(OAUTH_STATE_COOKIE)
    return response


def _start_google_auth(invite_email: str | None = None) -> RedirectResponse:
    """state / nonce を生成して署名付き Cookie に保存し、Google の認可画面へリダイレクトする。"""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    payload = {"state": state, "nonce": nonce}
    if invite_email:
        payload["invite_email"] = invite_email

    url = google_auth.build_authorization_url(state, nonce, login_hint=invite_email)
    response = RedirectResponse(url=url, status_code=302)
    # Google からの戻りはトップレベル GET なので SameSite=Lax でも Cookie は送られる
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        create_oauth_state(payload),
        httponly=True,
        max_age=OAUTH_STATE_MAX_AGE,
        samesite="lax",
    )
    return response


@router.get("/auth/google")
async def google_login():
    _require_google_enabled()
    return _start_google_auth()


@router.get("/invite/{token}")
async def invite_accept(token: str, request: Request, db: Session = Depends(get_db)):
    """招待リンク。トークンを検証し、招待先アドレスをヒントにして Google 認証へ進める。

    防衛線はリンクではなくコールバックでの email 照合なので、リンク自体は
    使用済み管理をしない。有効期限切れでもログイン画面の Google ボタンから入れる。
    """
    _require_google_enabled()
    payload = verify_invite_token(token)
    if not payload:
        return _login_error(request, "招待リンクが無効か、有効期限が切れています。管理者に再発行を依頼してください")

    user = db.query(AdminUser).filter(AdminUser.id == payload.get("uid")).first()
    if user is None or user.email is None or user.email != payload.get("email"):
        return _login_error(request, "招待リンクが無効です。管理者に再発行を依頼してください")

    if user.google_sub is not None:
        # 既に連携済み。通常のログインへ
        return _redirect("/login")

    return _start_google_auth(invite_email=user.email)


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    _require_google_enabled()

    payload = verify_oauth_state(request.cookies.get(OAUTH_STATE_COOKIE))
    expected_state = str(payload.get("state", "")) if payload else ""
    if not expected_state or not state or not secrets.compare_digest(expected_state.encode(), state.encode()):
        return _login_error(request, "認証セッションが無効です。もう一度ログインしてください")

    if error or not code:
        # 同意画面でキャンセルされた等
        return _login_error(request, "Google ログインがキャンセルされました")

    try:
        claims = await google_auth.exchange_code(code)
        sub, email = google_auth.verify_claims(claims, payload.get("nonce", ""))
    except google_auth.GoogleAuthError as e:
        return _login_error(request, str(e))

    invite_email = payload.get("invite_email")
    if invite_email and email != invite_email:
        return _login_error(
            request,
            f"招待されたアドレス（{invite_email}）と異なる Google アカウントでログインしました",
        )

    user = db.query(AdminUser).filter(AdminUser.google_sub == sub).first()
    if user is None:
        user = db.query(AdminUser).filter(AdminUser.email == email).first()
        if user is None:
            return _login_error(request, "この Google アカウントは招待されていません。管理者に連絡してください")
        if user.google_sub is not None:
            return _login_error(
                request,
                "このメールアドレスには別の Google アカウントが連携されています。管理者に連絡してください",
            )
        if user.is_invite_pending:
            # 招待の受諾: 初回ログインで有効化する
            user.is_active = True
        elif not user.is_active:
            return _login_error(request, "このアカウントは無効化されています。管理者に連絡してください")
        user.google_sub = sub
    elif not user.is_active:
        return _login_error(request, "このアカウントは無効化されています。管理者に連絡してください")

    user.last_login_at = datetime.utcnow()
    db.commit()

    response = _redirect("/list")
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.username),
        httponly=True,
        max_age=get_session_max_age(db),
    )
    response.delete_cookie(OAUTH_STATE_COOKIE)
    return response
