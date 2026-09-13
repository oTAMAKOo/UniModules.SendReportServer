import os
from datetime import datetime
from io import BytesIO
from base64 import b64decode

import boto3
from PIL import Image

from app.config import settings

THUMBNAIL_WIDTH = 400


def _scale_to_width(img: Image.Image, width: int) -> Image.Image:
    ratio = width / img.width
    height = int(img.height * ratio)
    return img.resize((width, height), Image.LANCZOS)


def _generate_filename() -> str:
    now = datetime.utcnow()
    return now.strftime("%Y%m%d_%H%M%S_%f") + ".png"


def save_screenshot(base64_data: str) -> tuple[str, str]:
    """スクリーンショットを保存し、(画像ファイル名, サムネイルファイル名) を返す。"""
    img_data = b64decode(base64_data)
    img = Image.open(BytesIO(img_data))

    filename = _generate_filename()
    thumbnail_filename = "thumbnail_" + filename

    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    thumb = _scale_to_width(img, THUMBNAIL_WIDTH)
    thumb_bytes = BytesIO()
    thumb.save(thumb_bytes, format="PNG")
    thumb_bytes.seek(0)

    if settings.storage_mode == "s3":
        _save_to_s3(f"report/images/{filename}", img_bytes)
        _save_to_s3(f"report/thumbnail/{thumbnail_filename}", thumb_bytes)
    else:
        _save_to_local(f"report/images/{filename}", img_bytes)
        _save_to_local(f"report/thumbnail/{thumbnail_filename}", thumb_bytes)

    return filename, thumbnail_filename


def get_image_url(path: str) -> str:
    """画像の公開URLを返す。"""
    if settings.storage_mode == "s3":
        return (
            f"https://s3.{settings.aws_region}.amazonaws.com"
            f"/{settings.aws_s3_bucket_name}/{path}"
        )
    return f"/storage/{path}"


def delete_screenshot(img_name: str, thumbnail_name: str) -> None:
    """スクリーンショットとサムネイルを削除する。

    保存先は settings.storage_mode に従う。存在しないファイル・キーを指定しても
    例外にはしない（DB行だけ消えて画像が孤児として残るのを避けるため）。
    """
    paths = []

    if img_name:
        paths.append(f"report/images/{img_name}")

    if thumbnail_name:
        paths.append(f"report/thumbnail/{thumbnail_name}")

    if not paths:
        return

    if settings.storage_mode == "s3":
        _delete_from_s3(paths)
    else:
        for path in paths:
            _delete_from_local(path)


def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )


def _save_to_s3(key: str, data: BytesIO) -> None:
    s3 = _s3_client()
    s3.upload_fileobj(
        data,
        settings.aws_s3_bucket_name,
        key,
        ExtraArgs={"ContentType": "image/png"},
    )


def _save_to_local(path: str, data: BytesIO) -> None:
    full_path = os.path.join(settings.local_storage_path, path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(data.read())


def _delete_from_s3(keys: list[str]) -> None:
    s3 = _s3_client()

    # 存在しないキーを指定しても delete_object はエラーにならない。
    for key in keys:
        s3.delete_object(Bucket=settings.aws_s3_bucket_name, Key=key)


def _delete_from_local(path: str) -> None:
    full_path = os.path.join(settings.local_storage_path, path)

    if os.path.exists(full_path):
        os.remove(full_path)
