"""Google ログインのルート。

GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / PUBLIC_BASE_URL が揃っていないときは
全ルートが 404 を返し、ログイン画面にも Google ボタンは表示されない。

Google ログインで AdminUser が新規作成されることはない。許可判定は
「AdminUser.email に一致するレコードが既に在るか」のみで、初回ログイン時に
行うのは google_sub の保存（と招待の受諾による有効化）だけ。
招待リンク（/invite/{token}）の受け口は routers/invite.py。そこから「Google で有効化」を
選ぶと start_google_auth(invite_email=...) でこのフローに入る。
"""
import logging
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
    cookie_secure,
    create_oauth_state,
    create_session_token,
    get_session_max_age,
    verify_oauth_state,
)
from app.config import settings
from app.database import get_db
from app.models import AdminUser
from app.ratelimit import oauth_limiter
from app.authz import post_login_path
from app.routers.common import redirect
from app.templating import templates

logger = logging.getLogger(__name__)

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


def start_google_auth(invite_email: str | None = None) -> RedirectResponse:
    """state / nonce を生成して署名付き Cookie に保存し、Google の認可画面へリダイレクトする。

    invite_email を渡すと（有効化ページからの遷移）、コールバックでそのアドレスと一致する
    Google アカウントだけを受け付ける。
    """
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
        secure=cookie_secure(),
        max_age=OAUTH_STATE_MAX_AGE,
        samesite="lax",
    )
    return response


@router.get("/auth/google")
async def google_login(request: Request):
    _require_google_enabled()
    oauth_limiter.check(request)
    return start_google_auth()


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    _require_google_enabled()
    oauth_limiter.check(request)

    payload = verify_oauth_state(request.cookies.get(OAUTH_STATE_COOKIE))
    expected_state = str(payload.get("state", "")) if payload else ""
    if not expected_state or not state or not secrets.compare_digest(expected_state.encode(), state.encode()):
        logger.info("Google callback を拒否: state が無効（Cookie 期限切れ・欠落・不一致）")
        return _login_error(request, "認証セッションが無効です。もう一度ログインしてください")

    if error or not code:
        # 同意画面でキャンセルされた等
        logger.info("Google ログインがキャンセルされました: error=%s", error)
        return _login_error(request, "Google ログインがキャンセルされました")

    try:
        claims = await google_auth.exchange_code(code)
        sub, email = google_auth.verify_claims(claims, payload.get("nonce", ""))
    except google_auth.GoogleAuthError as e:
        logger.warning("Google ログイン失敗: %s", e)
        return _login_error(request, str(e))

    invite_email = payload.get("invite_email")
    if invite_email and email != invite_email:
        logger.warning("Google ログインを拒否: 招待先 %s と異なるアカウント %s", invite_email, email)
        return _login_error(
            request,
            f"招待されたアドレス（{invite_email}）と異なる Google アカウントでログインしました",
        )

    user = db.query(AdminUser).filter(AdminUser.google_sub == sub).first()
    if user is None:
        user = db.query(AdminUser).filter(AdminUser.email == email).first()
        if user is None:
            logger.warning("Google ログインを拒否: 未招待のアドレス %s", email)
            return _login_error(request, "この Google アカウントは招待されていません。管理者に連絡してください")
        if user.google_sub is not None:
            logger.warning("Google ログインを拒否: %s には別の Google アカウントが連携済み（user=%s）", email, user.username)
            return _login_error(
                request,
                "このメールアドレスには別の Google アカウントが連携されています。管理者に連絡してください",
            )
        if user.is_invite_pending:
            # 招待の受諾: 初回ログインで有効化する
            user.is_active = True
        elif not user.is_active:
            logger.warning("Google ログインを拒否: user=%s は無効化されている", user.username)
            return _login_error(request, "このアカウントは無効化されています。管理者に連絡してください")
        user.google_sub = sub
        logger.info("Google 連携を保存: user=%s email=%s", user.username, email)
    elif not user.is_active:
        logger.warning("Google ログインを拒否: user=%s は無効化されている", user.username)
        return _login_error(request, "このアカウントは無効化されています。管理者に連絡してください")
    elif user.email != email:
        # sub は一致するが Google 側でアドレスが変わっている。識別子は sub なので通すが、記録は残す
        logger.info("Google ログイン: user=%s のアドレスが変わっている（DB=%s, Google=%s）", user.username, user.email, email)

    user.last_login_at = datetime.utcnow()
    db.commit()
    logger.info("Google ログイン成功: user=%s", user.username)

    response = redirect(post_login_path(db, user))
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.username),
        httponly=True,
        secure=cookie_secure(),
        max_age=get_session_max_age(db),
    )
    response.delete_cookie(OAUTH_STATE_COOKIE)
    return response
