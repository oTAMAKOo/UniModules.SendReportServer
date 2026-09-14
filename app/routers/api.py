import json
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import decrypt_report_body
from app.database import get_db
from app.models import ReportData
from app.ratelimit import report_limiter
from app.schemas import ReportResponse
from app.storage import save_screenshot

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/report", response_model=ReportResponse)
async def receive_report(request: Request, db: Session = Depends(get_db)):
    """クライアントからのAES暗号化クラッシュレポートを受信する。

    既存APIと完全互換のエンドポイント。
    """
    report_limiter.check(request)
    raw_body = await request.body()
    data = decrypt_report_body(raw_body, db)

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
            img_name, img_thumbnail_name = save_screenshot(screenshot_b64)
        except Exception:
            logger.exception("Failed to save screenshot")

    report = ReportData(
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
    url = f"{base_url}{settings.url_prefix}/detail/{report.id}"

    return ReportResponse(URL=url)
