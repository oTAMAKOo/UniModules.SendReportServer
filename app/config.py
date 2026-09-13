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

    model_config = {"env_file": ".env"}


settings = Settings()
