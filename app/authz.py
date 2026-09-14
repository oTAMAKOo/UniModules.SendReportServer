"""プロジェクト単位の認可（FastAPI の依存関数と共通の判定ロジック）。

役割:
- システム管理者（AdminUser.is_superuser）: 全プロジェクトを管理者として扱う。プロジェクトの作成・削除、
  全ユーザー管理ができる
- プロジェクト管理者（ProjectMember.role == "admin"）: そのプロジェクトのメンバー管理・AES 設定・一括削除
- メンバー（role == "member"）: レポートの閲覧・削除

管理画面は API のように 401/403 を返さず、未認証ならログイン画面へ、権限が無ければプロジェクト
選択画面へ 302 する設計。依存関数の中からリダイレクトを返すため RedirectException を投げ、
main.py の exception handler で RedirectResponse に変換する。

読み取り API / MCP 向けには例外ではなく status と message を持つ ApiProjectError を使う。
レポート ID は全プロジェクトで 1 本の連番なので、所属外のレポートは「存在しない」と同じ扱い
（404 相当）にして他プロジェクトのレポートの存在を漏らさない。
"""
import re
from typing import NamedTuple

from fastapi import Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ROLE_ADMIN, ROLE_MEMBER, AdminUser, Project, ProjectMember, ReportData
from app.routers.common import get_current_user

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
# プロジェクト配下以外のルートやマウントと衝突しうる名前は slug に使わせない
RESERVED_SLUGS = {
    "admin", "api", "auth", "delete", "detail", "docs", "invite", "list", "login", "logout", "manage", "mcp",
    "new", "p", "password_change", "projects", "report", "static", "storage", "system", "tokens", "users",
}

REPORT_NOT_FOUND_MESSAGE = "レポート #{id} は存在しないか、アクセス権がありません"

_DETAIL_PATH_RE = re.compile(r"/detail/(\d+)/?$")


class RedirectException(Exception):
    """依存関数から「プレフィックス付きの path へ 302」を要求する。"""

    def __init__(self, path: str):
        super().__init__(path)
        self.path = path


class ApiProjectError(Exception):
    """読み取り API / MCP でプロジェクトを特定できなかった。status は HTTP の意味（400 / 403 / 404）。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ProjectContext(NamedTuple):
    user: AdminUser
    project: Project
    role: str  # ROLE_ADMIN / ROLE_MEMBER（superuser は常に ROLE_ADMIN）


# --- 判定ロジック ---


def validate_slug(slug: str) -> str | None:
    """slug が使える形式なら None、使えなければエラー文を返す。"""
    if not SLUG_RE.match(slug):
        return "slug は小文字英数字とハイフンのみ、先頭は英数字、2〜40 文字で指定してください"
    if slug in RESERVED_SLUGS:
        return f"'{slug}' はシステムで使用しているため slug に使えません"
    return None


def get_project_by_slug(db: Session, slug: str) -> Project | None:
    return db.query(Project).filter(Project.slug == slug).first()


def get_membership(db: Session, user_id: int, project_id: int) -> ProjectMember | None:
    return db.query(ProjectMember).filter(
        ProjectMember.user_id == user_id,
        ProjectMember.project_id == project_id,
    ).first()


def effective_role(db: Session, user: AdminUser, project: Project) -> str | None:
    """ユーザーがそのプロジェクトで持つ役割。所属が無ければ None。superuser は常に admin。"""
    if user.is_superuser:
        return ROLE_ADMIN
    membership = get_membership(db, user.id, project.id)
    return membership.role if membership else None


def user_projects(db: Session, user: AdminUser) -> list[tuple[Project, str]]:
    """ユーザーが見えるプロジェクトと役割の一覧（表示名順）。superuser は全プロジェクト。"""
    if user.is_superuser:
        projects = db.query(Project).order_by(Project.name, Project.id).all()
        return [(p, ROLE_ADMIN) for p in projects]
    rows = (
        db.query(ProjectMember, Project)
        .join(Project, Project.id == ProjectMember.project_id)
        .filter(ProjectMember.user_id == user.id)
        .order_by(Project.name, Project.id)
        .all()
    )
    return [(project, membership.role) for membership, project in rows]


def post_login_path(db: Session, user: AdminUser) -> str:
    """ログイン直後の遷移先。所属が 1 つならその一覧へ、それ以外はプロジェクト選択画面へ。"""
    projects = user_projects(db, user)
    if len(projects) == 1:
        return f"/p/{projects[0][0].slug}/list"
    return "/projects"


def resolve_report_for_user(db: Session, user: AdminUser, report_id: int) -> ReportData | None:
    """レポートを取得し、ユーザーがその所属プロジェクトを見られなければ None（存在しないのと同じ扱い）。"""
    report = db.query(ReportData).filter(ReportData.id == report_id).first()
    if report is None:
        return None
    if effective_role(db, user, report.project) is None:
        return None
    return report


def is_last_active_project_admin(db: Session, project: Project, target: AdminUser) -> bool:
    """target を降格・除外するとプロジェクトの有効な管理者が 0 人になるか。"""
    membership = get_membership(db, target.id, project.id)
    if membership is None or membership.role != ROLE_ADMIN or not target.is_active:
        return False
    count = (
        db.query(func.count(ProjectMember.id))
        .join(AdminUser, AdminUser.id == ProjectMember.user_id)
        .filter(
            ProjectMember.project_id == project.id,
            ProjectMember.role == ROLE_ADMIN,
            AdminUser.is_active == True,
        )
        .scalar()
    )
    return count <= 1


def api_project_or_error(db: Session, user: AdminUser, slug: str | None) -> Project:
    """読み取り API / MCP 用: slug からプロジェクトを特定する。

    slug が空なら所属が 1 つのときだけそれを使う（複数なら 400 で所属一覧を案内、0 なら 400）。
    slug を指定して所属外なら 403、存在しなければ 404。
    """
    projects = user_projects(db, user)
    slug = (slug or "").strip()
    if not slug:
        if len(projects) == 1:
            return projects[0][0]
        if not projects:
            raise ApiProjectError(400, "参加しているプロジェクトがありません。プロジェクト管理者に追加を依頼してください")
        names = ", ".join(p.slug for p, _ in projects)
        raise ApiProjectError(400, f"project を指定してください。参加しているプロジェクト: {names}")
    for project, _ in projects:
        if project.slug == slug:
            return project
    if get_project_by_slug(db, slug) is not None:
        names = ", ".join(p.slug for p, _ in projects) or "（なし）"
        raise ApiProjectError(403, f"プロジェクト '{slug}' へのアクセス権がありません。参加しているプロジェクト: {names}")
    raise ApiProjectError(404, f"プロジェクト '{slug}' は存在しません")


# --- 管理画面向けの依存関数 ---


def require_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """ログイン済みユーザーを返す。未認証ならログイン画面へ。ナビ用に所属一覧も request.state に置く。"""
    user = get_current_user(request, db)
    if not user:
        raise RedirectException("/login")
    request.state.user = user
    request.state.nav_projects = user_projects(db, user)
    return user


def require_superuser(user: AdminUser = Depends(require_user)) -> AdminUser:
    """システム管理者（is_superuser）だけを通す。それ以外は管理メニューへ。"""
    if not user.is_superuser:
        raise RedirectException("/admin")
    return user


def require_project(min_role: str = ROLE_MEMBER):
    """`/p/{project_slug}/...` 配下のルート用。所属と役割を確認して ProjectContext を返す。

    slug が存在しない・所属していない → プロジェクト選択画面へ（理由付き）。
    管理者権限が必要なのにメンバー → そのプロジェクトの一覧へ。
    """

    def dependency(
        project_slug: str,
        request: Request,
        user: AdminUser = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> ProjectContext:
        project = get_project_by_slug(db, project_slug)
        if project is None:
            # slug が変更された後に共有済みの /p/<旧slug>/detail/<id> を開いたときの救済。
            # レポート ID は全体で通し番号なので、所属を確認できれば新しい slug へ転送できる
            match = _DETAIL_PATH_RE.search(request.url.path)
            if match:
                report = resolve_report_for_user(db, user, int(match.group(1)))
                if report is not None:
                    raise RedirectException(f"/p/{report.project.slug}/detail/{report.id}")
            raise RedirectException("/projects?error=notfound")
        role = effective_role(db, user, project)
        if role is None:
            raise RedirectException("/projects?error=forbidden")
        if min_role == ROLE_ADMIN and role != ROLE_ADMIN:
            raise RedirectException(f"/p/{project.slug}/list?error=forbidden")
        request.state.project = project
        request.state.role = role
        return ProjectContext(user=user, project=project, role=role)

    return dependency
