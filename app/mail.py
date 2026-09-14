"""招待メールの送信。MAIL_MODE で送信手段を切り替える。

  none: 送信しない（招待リンクは管理画面に表示されるので、管理者が直接伝える）
  ses:  Amazon SES（boto3。認証情報は S3 と共通の AWS_* を使う）
  smtp: 任意の SMTP サーバー（標準ライブラリの smtplib）
"""
import logging
import smtplib
from email.message import EmailMessage

import boto3

from app.config import settings

logger = logging.getLogger(__name__)


class MailError(Exception):
    """メール送信の失敗。メッセージは管理画面に表示される。"""


def mail_enabled() -> bool:
    return settings.mail_mode != "none"


def send_invite_mail(to_email: str, username: str, invite_url: str) -> None:
    """招待メールを送る。MAIL_MODE=none なら何もしない。失敗時は MailError。"""
    if not mail_enabled():
        return

    subject = "[Log Server] 管理画面への招待"
    body = (
        f"{username} さん\n"
        f"\n"
        f"Log Server の管理画面に招待されました。\n"
        f"以下のリンクを開き、このメールを受信した Google アカウント（{to_email}）でログインしてください。\n"
        f"\n"
        f"{invite_url}\n"
        f"\n"
        f"リンクの有効期限は {settings.invite_expire_hours} 時間です。\n"
        f"期限が切れた場合は管理者に再発行を依頼してください。\n"
    )

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


def _send_ses(to_email: str, subject: str, body: str) -> None:
    if not settings.mail_from:
        raise MailError("MAIL_FROM が未設定です")
    client = boto3.client(
        "ses",
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
        region_name=settings.aws_region,
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
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
