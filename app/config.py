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

    # S3 モードのスクリーンショット URL（署名付き）の有効期限（秒）。管理画面の表示と
    # MCP / 読み取り API の Markdown に載る URL の両方に効く。バケット自体は非公開にする。
    # 60〜604800（AWS の上限 7 日）。範囲外は起動エラー。
    s3_presign_expire_seconds: int = 1800

    admin_username: str = "admin"
    admin_password: str = "password"
    # ロックアウト復旧用。ADMIN_USERNAME のアカウントに紐付け、起動のたびに
    # superuser / 有効 を保証する Google アカウント。空なら無効。
    admin_google_email: str = ""

    secret_key: str = "change-this-to-a-random-string"

    # 全ルートの先頭に付くURLプレフィックス。空文字にするとルート直下にマウントされる。
    url_prefix: str = "/buglog"

    # 外部から到達できる公開 URL（例: https://buglog.example.com）。
    # Google のリダイレクト URI と招待リンクの組み立てに使う。nginx 経由では
    # request.url のスキームが http になるため、明示指定が必要。
    public_base_url: str = ""

    # Google OAuth クライアント。client_id / client_secret / public_base_url が
    # 揃っているときだけ Google ログインが有効になる（google_enabled）。
    google_client_id: str = ""
    google_client_secret: str = ""

    # 招待リンクの有効期限（時間）
    invite_expire_hours: int = 72

    # Claude Code 等から使う MCP サーバー（{URL_PREFIX}/mcp）を公開するか。
    # 読み取り専用 API（{URL_PREFIX}/api/reports）は常に有効で、どちらも個人 API トークンが必要。
    mcp_enabled: bool = True

    # 招待メールの送信方法: "none"（送信せず画面にリンク表示のみ） / "ses" / "smtp"
    mail_mode: str = "none"
    mail_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True

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

    @field_validator("public_base_url")
    @classmethod
    def _normalize_public_base_url(cls, value: str) -> str:
        """末尾の "/" を除去し、スキームが http(s) であることを確認する。"""
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError(
                "PUBLIC_BASE_URL は http:// または https:// で始まる URL を指定してください（例: https://buglog.example.com）"
            )
        return value

    @field_validator("admin_google_email")
    @classmethod
    def _normalize_admin_google_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("s3_presign_expire_seconds")
    @classmethod
    def _validate_s3_presign_expire_seconds(cls, value: int) -> int:
        """署名付き URL の有効期限は 60 秒〜7 日（AWS の上限）に限る。STORAGE_MODE に関わらず検証する。"""
        if not 60 <= value <= 604800:
            raise ValueError(
                "S3_PRESIGN_EXPIRE_SECONDS は 60〜604800（秒）の範囲で指定してください（既定 1800 = 30 分）"
            )
        return value

    @field_validator("mail_mode")
    @classmethod
    def _validate_mail_mode(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ("none", "ses", "smtp"):
            raise ValueError("MAIL_MODE は none / ses / smtp のいずれかを指定してください")
        return value

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.public_base_url)

    model_config = {
        "env_file": ".env",
        # 値が空のキー（SMTP_PORT= 等）は既定値にフォールバックさせる（既定では int 変換で起動失敗する）
        "env_ignore_empty": True,
        # .env に docker compose 用のキー（COMPOSE_PROJECT_NAME 等）が並んでいても起動を止めない
        "extra": "ignore",
    }


settings = Settings()
