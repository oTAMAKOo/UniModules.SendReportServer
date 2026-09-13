from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import ensure_default_admin
from app.config import settings
from app.database import SessionLocal
from app.routers import api, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        ensure_default_admin(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Log Server", docs_url="/docs", redoc_url=None, lifespan=lifespan)

app.include_router(api.router, prefix=settings.url_prefix)
app.include_router(admin.router, prefix=settings.url_prefix)

# Jinja2テンプレートにURLプレフィックスをグローバル変数として渡す
admin.templates.env.globals["PREFIX"] = settings.url_prefix

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
