import json
import logging
import urllib.parse
from base64 import b64decode

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SystemConfig

logger = logging.getLogger(__name__)


def get_aes_config(db: Session) -> tuple[bytes, bytes]:
    """DBからAES Key/IVを取得する。未設定なら.envのデフォルト値を使う。"""
    key_row = db.query(SystemConfig).filter(SystemConfig.key == "aes_key").first()
    iv_row = db.query(SystemConfig).filter(SystemConfig.key == "aes_iv").first()

    aes_key = key_row.value if key_row and key_row.value else settings.report_aes_key
    aes_iv = iv_row.value if iv_row and iv_row.value else settings.report_aes_iv

    return aes_key.encode("utf-8"), aes_iv.encode("utf-8")


def decrypt_report_body(raw_body: bytes, db: Session) -> dict[str, str]:
    """既存クライアントと互換のAES-CBC復号処理。

    クライアントからは URL-encoded JSON が送られてくる。
    JSON の各値は AES-CBC で暗号化された後 Base64 エンコードされている。
    """
    aes_key, aes_iv = get_aes_config(db)

    decoded_body = urllib.parse.unquote(raw_body.decode("utf-8"))
    json_dict = json.loads(decoded_body)

    result = {}
    for key, value in json_dict.items():
        try:
            cipher = AES.new(aes_key, AES.MODE_CBC, aes_iv)
            ct_data = b64decode(value)
            d_data = cipher.decrypt(ct_data)
            pt = unpad(d_data, 16, "pkcs7")
            result[key] = pt.decode("utf-8")
        except (ValueError, KeyError):
            logger.warning("AES復号に失敗しました: key=%s", key)

    return result
