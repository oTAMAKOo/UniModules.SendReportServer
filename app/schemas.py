from datetime import datetime

from pydantic import BaseModel


class ReportResponse(BaseModel):
    URL: str


class ReportDetail(BaseModel):
    id: int
    title: str | None
    created_at: datetime
    user_id: str | None
    user_name: str | None
    device_model: str | None
    log: str | None
    img_name: str | None
    img_thumbnail_name: str | None
    extend_info: str | None

    model_config = {"from_attributes": True}


class ReportListItem(BaseModel):
    id: int
    title: str | None
    created_at: datetime
    user_id: str | None
    user_name: str | None
    device_model: str | None

    model_config = {"from_attributes": True}
