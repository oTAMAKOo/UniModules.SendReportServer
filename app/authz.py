"""管理画面の認可（FastAPI の依存関数）。

管理画面は API のように 401 を返さず、未認証ならログイン画面へ 302 する設計。
依存関数の中からリダイレクトを返すため RedirectException を投げ、main.py の
exception handler で RedirectResponse に変換する。
"""
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser
from app.routers.common import get_current_user


class RedirectException(Exception):
    """依存関数から「プレフィックス付きの path へ 302」を要求する。"""

    def __init__(self, path: str):
        super().__init__(path)
        self.path = path


def require_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """ログイン済みユーザーを返す。未認証ならログイン画面へ。"""
    user = get_current_user(request, db)
    if not user:
        raise RedirectException("/login")
    request.state.user = user
    return user


def require_superuser(user: AdminUser = Depends(require_user)) -> AdminUser:
    """システム管理者（is_superuser）だけを通す。それ以外は管理メニューへ。"""
    if not user.is_superuser:
        raise RedirectException("/admin")
    return user
