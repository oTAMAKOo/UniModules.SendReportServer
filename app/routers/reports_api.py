"""読み取り専用のレポート API（Claude Code 等の外部クライアント向け）。

管理画面はセッション Cookie が無いとログイン画面へ 302 するため、ヘッダで認証する
HTTP クライアントからは中身を読めない。ここでは Authorization: Bearer <API トークン> を
受け付け、失敗時は JSON の 401 を返す。ブラウザから（詳細画面の「Markdown をコピー」）
呼ぶ場合のためにセッション Cookie でも認証できる。

書き込み系（削除等）は意図的に提供しない。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import authenticate_api_token, extract_bearer_token
from app.database import get_db
from app.models import AdminUser, ReportData
from app.ratelimit import api_limiter
from app.report_export import (
    parse_report_reference,
    report_summary,
    report_to_dict,
    report_to_markdown,
    search_reports_query,
)
from app.routers.common import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 25


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="認証が必要です。管理画面の「API トークン」で発行したトークンを Authorization: Bearer ヘッダで送ってください",
        headers={"WWW-Authenticate": 'Bearer realm="log-server"'},
    )


def get_api_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """Bearer トークン、無ければセッション Cookie で認証する。どちらも無効なら 401。

    レート制限は認証の前に数える（トークン総当たりの抑止）。
    """
    api_limiter.check(request)

    authorization = request.headers.get("Authorization")
    token = extract_bearer_token(authorization)
    if token:
        user = authenticate_api_token(db, token)
        if user is None:
            logger.info("API トークン認証に失敗: prefix=%s", token[:12])
            raise _unauthorized()
        return user
    if authorization:
        # Bearer 以外のスキームや空のトークン
        raise _unauthorized()

    user = get_current_user(request, db)
    if user is None:
        raise _unauthorized()
    return user


def _get_report_or_404(db: Session, report_id: int) -> ReportData:
    report = db.query(ReportData).filter(ReportData.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail=f"レポート #{report_id} は存在しません（削除された可能性があります）")
    return report


def _wants_markdown(request: Request, fmt: str | None) -> bool:
    if fmt:
        return fmt.lower() in ("md", "markdown", "text")
    accept = request.headers.get("Accept", "")
    return "text/markdown" in accept or ("text/plain" in accept and "application/json" not in accept)


@router.get("/reports/{report_id:int}.md", response_class=PlainTextResponse)
async def report_markdown(
    report_id: int,
    user: AdminUser = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """レポート 1 件を Markdown で返す。"""
    report = _get_report_or_404(db, report_id)
    return PlainTextResponse(report_to_markdown(report), media_type="text/markdown; charset=utf-8")


@router.get("/reports/{report_id:int}")
async def report_get(
    request: Request,
    report_id: int,
    format: str | None = Query(None, description="json（既定）または md"),
    user: AdminUser = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """レポート 1 件を返す。?format=md か Accept: text/markdown で Markdown、それ以外は JSON。"""
    report = _get_report_or_404(db, report_id)
    if _wants_markdown(request, format):
        return PlainTextResponse(report_to_markdown(report), media_type="text/markdown; charset=utf-8")
    return JSONResponse(report_to_dict(report))


@router.get("/reports")
async def report_search(
    q: str = Query("", description="全テキストフィールドの部分一致（管理画面の検索と同じ）"),
    date_from: str = Query("", description="YYYY-MM-DD（この日を含む）"),
    date_to: str = Query("", description="YYYY-MM-DD（この日を含む）"),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
    user: AdminUser = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """レポートを検索し、新しい順に要約を返す。"""
    query = search_reports_query(db, q, date_from, date_to)
    total = query.count()
    reports = query.order_by(desc(ReportData.id)).offset((page - 1) * per_page).limit(per_page).all()
    return JSONResponse({
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [report_summary(r) for r in reports],
    })


@router.get("/resolve")
async def report_resolve(
    ref: str = Query(..., description="レポートの URL、ID、'#123' のいずれか"),
    user: AdminUser = Depends(get_api_user),
):
    """URL などからレポート ID を取り出す（クライアント側で URL を解釈したくない場合用）。"""
    report_id = parse_report_reference(ref)
    if report_id is None:
        raise HTTPException(status_code=400, detail="レポート ID を読み取れませんでした。詳細ページの URL か ID を指定してください")
    return JSONResponse({"id": report_id})
