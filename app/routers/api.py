import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import DecryptError, decrypt_report_body
from app.database import get_db
from app.models import Project, ReportData
from app.ratelimit import report_limiter
from app.schemas import ReportResponse
from app.storage import save_screenshot

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/report/{project_slug}", response_model=ReportResponse)
async def receive_report(project_slug: str, request: Request, db: Session = Depends(get_db)):
    """クライアントからの AES 暗号化クラッシュレポートを受信する。

    URL の slug でプロジェクトを特定し、そのプロジェクトの AES Key/IV で復号する。
    本文の形式（URL エンコードされた JSON、各値が AES-CBC + Base64）は従来と同じ。
    """
    report_limiter.check(request)

    project = db.query(Project).filter(Project.slug == project_slug, Project.is_active == True).first()
    if project is None:
        raise HTTPException(status_code=404, detail="プロジェクトが見つかりません")

    raw_body = await request.body()
    try:
        data = decrypt_report_body(raw_body, project.aes_key.encode("utf-8"), project.aes_iv.encode("utf-8"))
    except DecryptError as e:
        logger.warning("レポートの復号に失敗しました: project=%s", project.slug)
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="レポート本文の形式が不正です")

    title = data.pop("Title", None)
    device_model = data.pop("DeviceModel", None)
    screenshot_b64 = data.pop("ScreenShotBase64", None)
    log_text = data.pop("Log", None)
    user_id = data.pop("UserID", None)
    user_name = data.pop("UserName", None)

    # 残りのフィールドは extend_info に格納
    extend_info = json.dumps(data, ensure_ascii=False) if data else "{}"

    img_name = ""
    img_thumbnail_name = ""
    if screenshot_b64:
        try:
            img_name, img_thumbnail_name = save_screenshot(screenshot_b64, project.slug)
        except Exception:
            logger.exception("Failed to save screenshot")

    report = ReportData(
        project_id=project.id,
        title=title,
        user_id=user_id,
        user_name=user_name,
        device_model=device_model,
        log=log_text,
        img_name=img_name,
        img_thumbnail_name=img_thumbnail_name,
        extend_info=extend_info,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    # nginx が TLS を終端するため request.base_url のスキームは http になる。公開 URL が
    # 設定されていればそちらを使い、チャットで共有される URL が本番でも https になるようにする
    base_url = settings.public_base_url or str(request.base_url).rstrip("/")
    url = f"{base_url}{settings.url_prefix}/p/{project.slug}/detail/{report.id}"

    return ReportResponse(URL=url)
