from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import ensure_admin_google_email, ensure_default_admin
from app.config import settings
from app.database import SessionLocal
from app.routers import api, admin, google_auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        ensure_default_admin(db)
        ensure_admin_google_email(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Log Server", docs_url="/docs", redoc_url=None, lifespan=lifespan)

app.include_router(api.router, prefix=settings.url_prefix)
app.include_router(admin.router, prefix=settings.url_prefix)
app.include_router(google_auth.router, prefix=settings.url_prefix)

# Jinja2テンプレートにURLプレフィックスと Google ログインの有効状態をグローバル変数として渡す
admin.templates.env.globals["PREFIX"] = settings.url_prefix
admin.templates.env.globals["GOOGLE_ENABLED"] = settings.google_enabled
# 復旧用アカウント（ADMIN_GOOGLE_EMAIL）をユーザー一覧で見分けるために渡す
admin.templates.env.globals["ADMIN_GOOGLE_EMAIL"] = settings.admin_google_email

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
