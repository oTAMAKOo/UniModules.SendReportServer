from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://logserver:logserver@db:5432/logserver"

    report_aes_key: str = "0123456789abcdef"
    report_aes_iv: str = "abcdef0123456789"

    storage_mode: str = "local"  # "local" or "s3"
    local_storage_path: str = "/app/storage"

    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_bucket_name: str = ""
    aws_region: str = "ap-northeast-1"

    admin_username: str = "admin"
    admin_password: str = "password"

    secret_key: str = "change-this-to-a-random-string"

    # 全ルートの先頭に付くURLプレフィックス。空文字にするとルート直下にマウントされる。
    url_prefix: str = "/buglog"

    @field_validator("url_prefix")
    @classmethod
    def _normalize_url_prefix(cls, value: str) -> str:
        """先頭の "/" を補完し、末尾の "/" を除去する。"/" のみの場合は空文字になる。"""
        value = value.strip().rstrip("/")

        if not value:
            return ""

        if not value.startswith("/"):
            value = "/" + value

        return value

    model_config = {"env_file": ".env"}


settings = Settings()
