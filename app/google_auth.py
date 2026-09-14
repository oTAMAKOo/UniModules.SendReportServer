"""Google OAuth 2.0 / OpenID Connect（Authorization Code フロー）のクライアント実装。

依存を増やさないため専用ライブラリは使わず、必要な処理だけを実装している。

ID トークンの署名は検証しない。認可コードを Google の token エンドポイントへ
サーバー間の TLS で直接交換しており、OpenID Connect Core 1.0 §3.1.3.7 により
TLS のサーバー検証が署名検証の代わりになるため。フローを implicit / hybrid に
変更する（ID トークンをブラウザ経由で受け取る）場合は、JWKS による署名検証が必須になる。
"""
import base64
import json
import logging
import time
from urllib.parse import urlencode

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ALLOWED_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
REQUEST_TIMEOUT = 10.0


class GoogleAuthError(Exception):
    """Google 認証の失敗。メッセージはログイン画面にそのまま表示される。"""


def redirect_uri() -> str:
    """GCP コンソールの「承認済みのリダイレクト URI」に登録する値と一致させる。"""
    return f"{settings.public_base_url}{settings.url_prefix}/auth/google/callback"


def build_authorization_url(state: str, nonce: str, login_hint: str | None = None) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "nonce": nonce,
        # 複数アカウントを持つ人が意図しないアカウントで入るのを防ぐ
        "prompt": "select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """認可コードをトークンに交換し、ID トークンのクレーム（未検証）を返す。"""
    data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(TOKEN_ENDPOINT, data=data)
    except httpx.HTTPError as e:
        logger.warning("Google token エンドポイントへの要求に失敗: %s: %s", type(e).__name__, e)
        raise GoogleAuthError("Google との通信に失敗しました。しばらくしてから再試行してください") from e

    if resp.status_code != 200:
        # invalid_client（シークレット誤り）や redirect_uri_mismatch の詳細は応答本文にしか出ない。
        # 本文にシークレットは含まれないのでそのまま記録する
        logger.warning("Google token 交換に失敗: status=%s body=%s", resp.status_code, resp.text[:500])
        raise GoogleAuthError("Google の認証に失敗しました（トークン交換エラー）")

    try:
        body = resp.json()
    except ValueError as e:
        raise GoogleAuthError("Google からの応答を解釈できませんでした") from e

    id_token = body.get("id_token") if isinstance(body, dict) else None
    if not id_token:
        raise GoogleAuthError("Google から ID トークンが返されませんでした")
    return _decode_claims(id_token)


def _decode_claims(id_token: str) -> dict:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise GoogleAuthError("ID トークンの形式が不正です")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError) as e:
        raise GoogleAuthError("ID トークンの形式が不正です") from e
    if not isinstance(claims, dict):
        raise GoogleAuthError("ID トークンの形式が不正です")
    return claims


def verify_claims(claims: dict, nonce: str) -> tuple[str, str]:
    """token エンドポイントから直接受け取った ID トークンのクレームを検証し (sub, email) を返す。

    署名は検証しない。この関数に渡してよいのは exchange_code() が Google の token
    エンドポイントから TLS 経由で受け取った ID トークンのクレームだけ。ブラウザ経由で
    受け取った ID トークン（Google Identity Services の credential 等）に対して使うと
    署名検証を欠いた認証バイパスになるので、その用途には JWKS 検証を別途実装すること。
    email は小文字に正規化して返す。
    """
    if claims.get("aud") != settings.google_client_id:
        raise GoogleAuthError("ID トークンの aud が一致しません")
    if claims.get("iss") not in ALLOWED_ISSUERS:
        raise GoogleAuthError("ID トークンの iss が不正です")
    try:
        exp = int(claims.get("exp", 0))
    except (TypeError, ValueError):
        exp = 0
    if exp <= time.time():
        raise GoogleAuthError("ID トークンの有効期限が切れています")
    if not nonce or claims.get("nonce") != nonce:
        raise GoogleAuthError("ID トークンの nonce が一致しません")
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleAuthError("メールアドレスが Google で確認されていません")

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise GoogleAuthError("ID トークンに sub / email が含まれていません")
    return str(sub), str(email).strip().lower()
