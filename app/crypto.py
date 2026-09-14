import json
import logging
import urllib.parse
from base64 import b64decode

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

logger = logging.getLogger(__name__)


class DecryptError(ValueError):
    """本文はあるのに 1 つも復号できなかった（AES Key/IV がプロジェクトの設定と一致していない）。"""


def decrypt_report_body(raw_body: bytes, aes_key: bytes, aes_iv: bytes) -> dict[str, str]:
    """既存クライアントと互換の AES-CBC 復号処理。

    クライアントからは URL-encoded JSON が送られてくる。
    JSON の各値は AES-CBC で暗号化された後 Base64 エンコードされている。
    Key/IV は受信 URL の slug で特定したプロジェクトのもの（routers/api.py）。

    値ごとの復号失敗は警告ログを出して読み飛ばすが、キーが 1 つ以上あるのに 1 つも
    復号できなかった場合は DecryptError にする（鍵違いで空のレポートが無言で保存されるのを防ぐ）。
    """
    decoded_body = urllib.parse.unquote(raw_body.decode("utf-8"))
    json_dict = json.loads(decoded_body)
    if not isinstance(json_dict, dict):
        raise ValueError("レポート本文が JSON オブジェクトではありません")

    result = {}
    for key, value in json_dict.items():
        try:
            cipher = AES.new(aes_key, AES.MODE_CBC, aes_iv)
            ct_data = b64decode(value)
            d_data = cipher.decrypt(ct_data)
            pt = unpad(d_data, 16, "pkcs7")
            result[key] = pt.decode("utf-8")
        except (ValueError, KeyError, TypeError):
            logger.warning("AES復号に失敗しました: key=%s", key)

    if json_dict and not result:
        raise DecryptError("復号に失敗しました（AES Key / IV がプロジェクトの設定と一致していません）")

    return result
