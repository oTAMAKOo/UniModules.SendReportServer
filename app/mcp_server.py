"""Claude Code 等の MCP クライアント向けサーバー（Streamable HTTP）。

`{URL_PREFIX}/mcp` にマウントされ、管理画面で発行した個人 API トークンを
`Authorization: Bearer` で受け取る。提供するのは読み取り専用のツールだけ。

構成上のメモ:
- SDK 標準の認証（TokenVerifier + AuthSettings）は OAuth の認可サーバー前提で issuer_url が必須。
  ここでは静的なトークンを DB と照合するだけなので、MCP アプリの前に薄い ASGI ラッパーを置いて
  Authorization を検証し、失敗なら 401 を返す（MCP のハンドシェイクにも到達させない）
- stateless + JSON 応答で動かす。本番は uvicorn を複数ワーカーで動かすため、ワーカー間で
  セッションを共有しなくてよい形にしておく。SSE を使わないので nginx のバッファリング設定も不要
- DNS リバインディング保護は無効化する。nginx 越しでは Host が公開 FQDN になり、既定の
  「localhost のみ許可」では 421 になる。認証必須のエンドポイントなので保護が無くても問題ない
- ツール本体は同期関数にしている。SDK がワーカースレッドで実行するので、同期の SQLAlchemy
  セッションをそのまま使える
"""
import contextvars
import logging
from contextlib import asynccontextmanager

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from sqlalchemy import desc

from app.auth import authenticate_api_token, extract_bearer_token
from app.config import settings
from app.database import SessionLocal
from app.models import ReportData
from app.ratelimit import api_limiter
from app.report_export import (
    parse_report_reference,
    report_summary,
    report_to_markdown,
    search_reports_query,
)

logger = logging.getLogger(__name__)

# ASGI ラッパーで認証したユーザー名。ツール実行時のログ用（認可はラッパー側で済んでいる）
_current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("mcp_current_user", default=None)

MAX_SEARCH_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 20

INSTRUCTIONS = (
    "Unity クライアントから送られたクラッシュ / バグレポートを読むためのサーバーです。"
    "ユーザーがレポートの URL（例: https://<host>{prefix}/detail/123）や ID を渡してきたら、"
    "まず get_report にその URL をそのまま渡してレポート全文（基本情報・エラーの要約・"
    "時系列ログ・スタックトレース）を取得し、原因の調査に使ってください。"
    "似た不具合を探すときは search_reports、最近の投稿を見るときは list_recent_reports を使います。"
).format(prefix=settings.url_prefix)

mcp = MCPServer(
    name="buglog",
    title="Log Server (Unity bug reports)",
    instructions=INSTRUCTIONS,
    version="1.0.0",
)


def _who() -> str:
    return _current_user.get() or "unknown"


def _format_search_results(reports: list[ReportData], total: int, heading: str) -> str:
    if not reports:
        return f"{heading}\n\n該当するレポートはありません。"
    lines = [heading, "", f"{total} 件中 {len(reports)} 件を表示（新しい順）。詳細は get_report に ID か URL を渡してください。", ""]
    for r in reports:
        s = report_summary(r)
        lines.append(
            f"- #{s['id']} [{s['created_at']}] {s['title'] or '(タイトルなし)'}"
            f" / user: {s['user_name'] or 'None'} ({s['user_id'] or 'None'})"
            f" / device: {s['device_model'] or '-'}"
            f" / logs: {s['log_count']} (errors: {s['error_count']})"
            + (f" / screenshot" if s["has_screenshot"] else "")
        )
        if s["last_error"]:
            lines.append(f"  - last error: {s['last_error']}")
        lines.append(f"  - {s['detail_url']}")
    return "\n".join(lines)


@mcp.tool(
    name="get_report",
    description=(
        "バグレポート 1 件の全文を Markdown で取得する。引数には管理画面の詳細ページ URL "
        f"（https://<host>{settings.url_prefix}/detail/<id>）をそのまま渡すか、ID（'123' や '#123'）を渡す。"
        "基本情報（投稿時間・ユーザー・端末・ビルド情報）、エラー / 例外の要約、時系列の全ログ、"
        "エラーごとのスタックトレース、スクリーンショット URL が含まれる。"
    ),
)
def get_report(report: str, ctx: Context) -> str:
    report_id = parse_report_reference(report)
    if report_id is None:
        return (
            f"レポート ID を読み取れませんでした: {report!r}\n"
            f"詳細ページの URL（https://<host>{settings.url_prefix}/detail/<id>）か ID を指定してください。"
        )
    db = SessionLocal()
    try:
        row = db.query(ReportData).filter(ReportData.id == report_id).first()
        if row is None:
            return f"レポート #{report_id} は存在しません（削除された可能性があります）。"
        logger.info("MCP get_report: user=%s report=%s", _who(), report_id)
        return report_to_markdown(row)
    finally:
        db.close()


@mcp.tool(
    name="search_reports",
    description=(
        "バグレポートを検索して要約一覧を返す。query はタイトル・ユーザー名・ユーザー ID・端末名・"
        "ログ本文・追加情報（ビルド番号やブランチ名など）に対する部分一致（大文字小文字を区別しない）。"
        "date_from / date_to は YYYY-MM-DD（その日を含む）。同じ例外が他でも起きているか、"
        "特定ユーザーや特定ビルドの報告を探すときに使う。"
    ),
)
def search_reports(
    ctx: Context,
    query: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> str:
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    db = SessionLocal()
    try:
        q = search_reports_query(db, query, date_from, date_to)
        total = q.count()
        rows = q.order_by(desc(ReportData.id)).limit(limit).all()
        logger.info("MCP search_reports: user=%s query=%r hits=%s", _who(), query, total)
        conditions = []
        if query.strip():
            conditions.append(f"query={query.strip()!r}")
        if date_from:
            conditions.append(f"from={date_from}")
        if date_to:
            conditions.append(f"to={date_to}")
        heading = "## 検索結果" + (f"（{', '.join(conditions)}）" if conditions else "")
        return _format_search_results(rows, total, heading)
    finally:
        db.close()


@mcp.tool(
    name="list_recent_reports",
    description="最近投稿されたバグレポートを新しい順に一覧する（要約のみ）。どのレポートを見るべきか分からないときの入口。",
)
def list_recent_reports(ctx: Context, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    db = SessionLocal()
    try:
        q = db.query(ReportData)
        total = q.count()
        rows = q.order_by(desc(ReportData.id)).limit(limit).all()
        logger.info("MCP list_recent_reports: user=%s limit=%s", _who(), limit)
        return _format_search_results(rows, total, "## 最近のレポート")
    finally:
        db.close()


class BearerAuthASGI:
    """MCP アプリの前で Authorization: Bearer <API トークン> を検証する ASGI ラッパー。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        try:
            api_limiter.check(request)
        except HTTPException as e:
            response = JSONResponse({"error": e.detail}, status_code=e.status_code)
            await response(scope, receive, send)
            return

        token = extract_bearer_token(request.headers.get("authorization"))
        user = None
        if token:
            user = await run_in_threadpool(_authenticate, token)
        if user is None:
            logger.info("MCP 認証失敗: prefix=%s path=%s", (token or "")[:12], scope.get("path"))
            response = JSONResponse(
                {
                    "error": "unauthorized",
                    "message": "管理画面の「API トークン」で発行したトークンを Authorization: Bearer ヘッダで送ってください",
                },
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="log-server"'},
            )
            await response(scope, receive, send)
            return

        reset = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(reset)


def _authenticate(token: str) -> str | None:
    db = SessionLocal()
    try:
        user = authenticate_api_token(db, token)
        return user.username if user else None
    finally:
        db.close()


def build_asgi_app():
    """FastAPI に add_route で登録する ASGI アプリを返す。main.py の lifespan で session_lifespan() を入れること。"""
    # Mount ではなく Route として登録するため、内側の Starlette には完全なパスで一致させる。
    # Mount だと末尾スラッシュ無し（{URL_PREFIX}/mcp）が 307 で /mcp/ へリダイレクトされ、
    # リダイレクトを追わない MCP クライアントが接続に失敗する
    inner = mcp.streamable_http_app(
        streamable_http_path=f"{settings.url_prefix}/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return BearerAuthASGI(inner)


@asynccontextmanager
async def session_lifespan():
    """マウントしたサブアプリの lifespan は動かないため、ホスト側の lifespan から呼ぶ。"""
    async with mcp.session_manager.run():
        yield
