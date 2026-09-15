"""Jinja2 テンプレート環境（全ルーター共通）。

ルーターごとに Jinja2Templates を作ると globals や context processor がばらけるため、
ここで 1 つだけ作って共有する。

ナビゲーションバー（プロジェクト切替など）が必要とする値は、認可の依存関数が
request.state に置いたものを context processor で全テンプレートへ渡す。
ログイン画面や 404 のように request.state に何も無いページでも動くよう、
すべて getattr の既定値で埋める。
"""
from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import settings


def nav_context(request: Request) -> dict:
    """request.state からナビゲーション用の変数を集める。"""
    state = request.state
    return {
        "current_user": getattr(state, "user", None),
        "current_project": getattr(state, "project", None),
        "current_role": getattr(state, "role", None),
        "nav_projects": getattr(state, "nav_projects", []),
    }


def project_url(slug: str, path: str = "") -> str:
    """プロジェクト配下のページ URL（プレフィックス付き）を返す。path は "/list" のように先頭スラッシュ付き。"""
    return f"{settings.url_prefix}/p/{slug}{path}"


templates = Jinja2Templates(directory="app/templates", context_processors=[nav_context])

templates.env.globals["PREFIX"] = settings.url_prefix
templates.env.globals["GOOGLE_ENABLED"] = settings.google_enabled
# 招待メールを送るか（MAIL_MODE != none）。招待フォームの説明文の出し分けに使う
templates.env.globals["MAIL_ENABLED"] = settings.mail_mode != "none"
# 復旧用アカウント（ADMIN_GOOGLE_EMAIL）をユーザー一覧で見分けるために渡す
templates.env.globals["ADMIN_GOOGLE_EMAIL"] = settings.admin_google_email
templates.env.globals["project_url"] = project_url
