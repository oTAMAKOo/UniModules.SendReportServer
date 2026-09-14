from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import ensure_admin_google_email, ensure_default_admin
from app.config import settings
from app.database import SessionLocal
from app import mcp_server
from app.authz import RedirectException
from app.routers.common import redirect
from app.routers import api, admin, google_auth, project_pages, reports_api, system_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        ensure_default_admin(db)
        ensure_admin_google_email(db)
    finally:
        db.close()
    async with AsyncExitStack() as stack:
        if settings.mcp_enabled:
            # マウントしたサブアプリの lifespan は動かないため、MCP のセッション管理をここで開始する
            await stack.enter_async_context(mcp_server.session_lifespan())
        yield


app = FastAPI(title="Log Server", docs_url="/docs", redoc_url=None, lifespan=lifespan)


@app.exception_handler(RedirectException)
async def _redirect_exception_handler(request, exc: RedirectException):
    # 認可の依存関数（app/authz.py）が要求したリダイレクト（未認証 → ログイン画面など）
    return redirect(exc.path)


app.include_router(api.router, prefix=settings.url_prefix)
app.include_router(admin.router, prefix=settings.url_prefix)
app.include_router(project_pages.router, prefix=settings.url_prefix)
app.include_router(system_admin.router, prefix=settings.url_prefix)
app.include_router(google_auth.router, prefix=settings.url_prefix)
app.include_router(reports_api.router, prefix=settings.url_prefix)

if settings.mcp_enabled:
    # Claude Code 等の MCP クライアント向け（Streamable HTTP、API トークン認証）
    # Mount ではなく Route にする（Mount は末尾スラッシュ無しを 307 で /mcp/ へ飛ばしてしまう）
    app.add_route(f"{settings.url_prefix}/mcp", mcp_server.build_asgi_app(), name="mcp", include_in_schema=False)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

if settings.storage_mode == "local":
    app.mount(
        "/storage",
        StaticFiles(directory=settings.local_storage_path),
        name="storage",
    )


@app.get("/")
async def root():
    return {"status": "ok", "service": "log-server"}
