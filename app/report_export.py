"""レポートを外部クライアント（Claude Code の MCP / 読み取り API / 画面のコピー）向けに整形する。

画面表示（routers/admin.py）と MCP（mcp_server.py）と JSON API（routers/reports_api.py）で
同じ整形を使うため、ここに集約する。
"""
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy.orm import Query, Session

from app.config import settings
from app.models import ReportData
from app.storage import get_image_url

# Unity の LogType。0=Error, 1=Assert, 2=Warning, 3=Log, 4=Exception
LOG_TYPE_NAMES = {0: "Error", 1: "Assert", 2: "Warning", 3: "Log", 4: "Exception"}
ERROR_LOG_TYPES = (0, 1, 4)

# 検索結果などで本文を切り詰めるときの長さ
SUMMARY_MESSAGE_LENGTH = 200


def format_log(raw_log: str | None) -> list[dict]:
    """DB の log 列（Unity の LogContainer JSON）を {message, stacktrace, logType} の配列にする。

    JSON として読めないときはテキスト全体を 1 件の Error として扱う（旧形式・手動投稿への保険）。
    """
    if not raw_log:
        return []
    try:
        log_data = json.loads(raw_log)
    except (json.JSONDecodeError, TypeError):
        return [{"message": raw_log, "stacktrace": "", "logType": 0}]

    if isinstance(log_data, dict) and "contents" in log_data:
        entries = []
        for item in log_data["contents"]:
            if isinstance(item, dict):
                entries.append({
                    "message": item.get("message", ""),
                    "stacktrace": item.get("stackTrace", item.get("stacktrace", "")),
                    "logType": item.get("type", item.get("logType", 3)),
                })
            else:
                entries.append({"message": str(item), "stacktrace": "", "logType": 0})
        return entries

    if log_data == "" or log_data is None:
        # ログが空のとき Unity 側は JsonUtility.ToJson(string.Empty) を送る
        return []

    return [{"message": json.dumps(log_data, indent=2, ensure_ascii=False), "stacktrace": "", "logType": 0}]


def parse_extend_info(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def report_detail_url(report_id: int) -> str:
    """管理画面の詳細ページ URL。PUBLIC_BASE_URL が無ければプレフィックスからの相対パス。"""
    return f"{settings.public_base_url}{settings.url_prefix}/detail/{report_id}"


def absolute_image_url(path: str) -> str:
    """画像 URL を外部クライアントが開ける形にする。S3 は元から絶対 URL、local は /storage/... なので公開 URL を前置する。"""
    url = get_image_url(path)
    if url.startswith("/") and settings.public_base_url:
        return f"{settings.public_base_url}{url}"
    return url


def parse_report_reference(text: str) -> int | None:
    """レポート ID を表す文字列（ID そのもの、詳細ページ URL、'#123' 等）から ID を取り出す。

    URL は末尾が `/detail/<id>` または `/api/reports/<id>` のものを受け付ける。プレフィックスや
    ホストは問わない（別環境の URL を貼られた場合でも ID は同じ意味で解釈する）。
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    if text.startswith("#"):
        text = text[1:]
    if text.isdigit():
        return int(text)

    path = urlparse(text).path if "://" in text else text
    match = re.search(r"/(?:detail|api/reports)/(\d+)(?:\.md)?/?$", path)
    if match:
        return int(match.group(1))
    return None


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def report_summary(report: ReportData) -> dict:
    """一覧・検索結果用の要約。ログは件数とエラー件数、先頭のエラー文だけ。"""
    entries = format_log(report.log)
    errors = [e for e in entries if e["logType"] in ERROR_LOG_TYPES]
    first_error = None
    if errors:
        # 要約なので 1 行目だけ（メッセージ自体に改行やスタックトレースが含まれることがある）
        message_lines = errors[-1]["message"].strip().splitlines()
        first_error = message_lines[0] if message_lines else "(空のメッセージ)"
    if first_error and len(first_error) > SUMMARY_MESSAGE_LENGTH:
        first_error = first_error[:SUMMARY_MESSAGE_LENGTH] + "…"
    return {
        "id": report.id,
        "title": report.title,
        "created_at": _format_datetime(report.created_at),
        "user_id": report.user_id,
        "user_name": report.user_name,
        "device_model": report.device_model,
        "log_count": len(entries),
        "error_count": len(errors),
        "last_error": first_error,
        "has_screenshot": bool(report.img_name),
        "detail_url": report_detail_url(report.id),
    }


def report_to_dict(report: ReportData) -> dict:
    """レポート全体を JSON 化できる dict にする。"""
    entries = format_log(report.log)
    return {
        "id": report.id,
        "title": report.title,
        "created_at": _format_datetime(report.created_at),
        "user_id": report.user_id,
        "user_name": report.user_name,
        "device_model": report.device_model,
        "extend_info": parse_extend_info(report.extend_info),
        "screenshot_url": absolute_image_url(report.img_name) if report.img_name else None,
        "detail_url": report_detail_url(report.id),
        "logs": [
            {
                "type": LOG_TYPE_NAMES.get(e["logType"], str(e["logType"])),
                "message": e["message"],
                "stacktrace": e["stacktrace"] or "",
            }
            for e in entries
        ],
    }


def report_to_markdown(report: ReportData) -> str:
    """Claude に読ませるための Markdown。

    ログは時系列順に全件並べる。エラー・例外にはスタックトレースを付け、Warning / Log は
    1 行にまとめて量を抑える。先頭にエラーだけの要約を置き、長いレポートでも原因箇所へ
    すぐ辿れるようにする。
    """
    entries = format_log(report.log)
    errors = [(i, e) for i, e in enumerate(entries) if e["logType"] in ERROR_LOG_TYPES]
    extend_info = parse_extend_info(report.extend_info)

    lines: list[str] = []
    title = report.title or "(タイトルなし)"
    lines.append(f"# Bug Report #{report.id}: {title}")
    lines.append("")
    lines.append("## 基本情報")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|---|---|")
    # created_at は UTC で保存されている（datetime.utcnow）。Claude が現地時刻と誤読しないよう明示する
    lines.append(f"| 投稿時間 | {_format_datetime(report.created_at) or '-'} (UTC) |")
    lines.append(f"| ユーザー | {report.user_name or 'None'} ({report.user_id or 'None'}) |")
    lines.append(f"| 端末 | {report.device_model or '-'} |")
    for key, value in extend_info.items():
        lines.append(f"| {_escape_cell(str(key))} | {_escape_cell(str(value))} |")
    lines.append(f"| ログ件数 | {len(entries)}（エラー・例外 {len(errors)} 件） |")
    if report.img_name:
        lines.append(f"| スクリーンショット | {absolute_image_url(report.img_name)} |")
    lines.append(f"| 詳細ページ | {report_detail_url(report.id)} |")
    lines.append("")

    if errors:
        lines.append("## エラー・例外の要約")
        lines.append("")
        for index, entry in errors:
            first_line = entry["message"].strip().splitlines()[0] if entry["message"].strip() else "(空のメッセージ)"
            lines.append(f"- [{index + 1}] {LOG_TYPE_NAMES.get(entry['logType'], entry['logType'])}: {first_line}")
        lines.append("")

    lines.append("## ログ（時系列）")
    lines.append("")
    if not entries:
        lines.append("(ログなし)")
    for index, entry in enumerate(entries):
        type_name = LOG_TYPE_NAMES.get(entry["logType"], str(entry["logType"]))
        message = entry["message"].rstrip()
        if entry["logType"] in ERROR_LOG_TYPES:
            lines.append(f"### [{index + 1}] {type_name}")
            lines.append("")
            lines.append("```")
            lines.append(message)
            stack = (entry["stacktrace"] or "").rstrip()
            if stack:
                lines.append("")
                lines.append(stack)
            lines.append("```")
            lines.append("")
        else:
            single = " ".join(message.split()) if message else "(空のメッセージ)"
            lines.append(f"- [{index + 1}] {type_name}: {single}")
    lines.append("")
    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    """Markdown の表セルに入れる値。縦棒と改行が表を壊すので置き換える。"""
    return value.replace("|", "\\|").replace("\r", "").replace("\n", " ")


def search_reports_query(db: Session, q: str = "", date_from: str = "", date_to: str = "", *, project_id: int | None = None) -> Query:
    """管理画面の一覧と同じ条件でレポートを絞り込むクエリを返す（並び順は呼び出し側で付ける）。

    project_id で所属プロジェクトに絞る（None は全プロジェクト。呼び出し側で認可済みの場合のみ）。
    q は全テキストフィールドへの ILIKE 部分一致。日付は YYYY-MM-DD で、date_to はその日を含む。
    形式が不正な日付は無視する（画面の挙動と同じ）。
    """
    query = db.query(ReportData)
    if project_id is not None:
        query = query.filter(ReportData.project_id == project_id)

    if q and q.strip():
        like_pattern = f"%{q.strip()}%"
        query = query.filter(
            (ReportData.title.ilike(like_pattern))
            | (ReportData.user_name.ilike(like_pattern))
            | (ReportData.user_id.ilike(like_pattern))
            | (ReportData.device_model.ilike(like_pattern))
            | (ReportData.log.ilike(like_pattern))
            | (ReportData.extend_info.ilike(like_pattern))
        )

    if date_from:
        try:
            dt_from = datetime.strptime(date_from.strip(), "%Y-%m-%d")
            query = query.filter(ReportData.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to.strip(), "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(ReportData.created_at < dt_to)
        except ValueError:
            pass

    return query
