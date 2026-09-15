"""招待メールの送信。MAIL_MODE で送信手段を切り替える。

  none: 送信しない（招待リンクは管理画面に表示されるので、管理者が直接伝える）
  ses:  Amazon SES（boto3。認証情報は S3 と共通の AWS_* を使う）
  smtp: 任意の SMTP サーバー（標準ライブラリの smtplib）
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage

import boto3
from botocore.config import Config as BotoConfig

from app.config import settings

logger = logging.getLogger(__name__)


class MailError(Exception):
    """メール送信の失敗。メッセージは管理画面に表示される。"""


def mail_enabled() -> bool:
    return settings.mail_mode != "none"


def build_invite_mail(
    to_email: str,
    invite_url: str,
    *,
    inviter_name: str | None = None,
    project_name: str | None = None,
) -> tuple[str, str]:
    """招待メールの (件名, 本文) を組み立てる。送信しない MAIL_MODE=none でも使える（テスト用）。"""
    subject = "[Log Server] アカウントの招待"

    who = f"{inviter_name} さんから" if inviter_name else "管理者から"
    where = f"プロジェクト「{project_name}」への" if project_name else ""
    lines = [
        "Log Server（バグレポート管理画面）への招待",
        "",
        f"{who}{where}招待が届いています。",
        "以下のリンクを開いて、アカウントを有効化してください。",
        "",
        invite_url,
        "",
        "有効化ページでは、次のどちらかでログイン方法を決められます。",
    ]
    if settings.google_enabled:
        lines.append(f"  - このメールを受信した Google アカウント（{to_email}）でログインする")
    lines += [
        "  - ユーザー名とパスワードを自分で決める",
        "",
        f"リンクの有効期限は {settings.invite_expire_hours} 時間です。",
        "期限が切れた場合は、招待した管理者に再発行を依頼してください。",
        "",
        "このメールに身に覚えがない場合は、何もせず削除してください。",
        "リンクを開いても、このアドレス以外の Google アカウントでは有効化できません。",
    ]
    return subject, "\n".join(lines) + "\n"


def send_invite_mail(
    to_email: str,
    invite_url: str,
    *,
    inviter_name: str | None = None,
    project_name: str | None = None,
) -> None:
    """招待メールを送る。MAIL_MODE=none なら何もしない。失敗時は MailError。"""
    if not mail_enabled():
        return

    subject, body = build_invite_mail(to_email, invite_url, inviter_name=inviter_name, project_name=project_name)

    try:
        if settings.mail_mode == "ses":
            _send_ses(to_email, subject, body)
        elif settings.mail_mode == "smtp":
            _send_smtp(to_email, subject, body)
    except MailError:
        raise
    except Exception as e:
        logger.exception("招待メールの送信に失敗しました: to=%s", to_email)
        raise MailError(f"{type(e).__name__}: {e}") from e
    logger.info("招待メールを送信しました: to=%s mode=%s", to_email, settings.mail_mode)


def _send_ses(to_email: str, subject: str, body: str) -> None:
    if not settings.mail_from:
        raise MailError("MAIL_FROM が未設定です")
    client = boto3.client(
        "ses",
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
        region_name=settings.aws_region,
        # 既定（接続・読み取り各 60 秒 + リトライ）だと SES 障害時に管理画面の操作が分単位で止まる
        config=BotoConfig(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
    )
    client.send_email(
        Source=settings.mail_from,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )


def _send_smtp(to_email: str, subject: str, body: str) -> None:
    if not settings.mail_from or not settings.smtp_host:
        raise MailError("MAIL_FROM / SMTP_HOST が未設定です")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.mail_from
    msg["To"] = to_email
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            # 既定の starttls() は証明書もホスト名も検証しないため、明示的に検証付きコンテキストを渡す
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
