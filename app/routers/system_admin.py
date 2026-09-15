"""システム管理者（AdminUser.is_superuser）専用の管理画面。

- /admin/projects: プロジェクトの作成・編集（表示名 / slug / 受信の有効無効）・削除
- /admin/users: 全ユーザーの管理（作成・削除・有効無効・システム管理者権限・パスワード・Google 連携）
- /system: 全体共通の設定（セッション有効期限）

プロジェクト単位のメンバー管理と AES 設定は routers/project_pages.py（プロジェクト管理者も使える）。
"""
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import hash_password, normalize_email
from app.authz import require_superuser, validate_slug
from app.config import settings
from app.database import get_db
from app.models import ROLE_ADMIN, ROLE_MEMBER, AdminUser, Project, ProjectMember, ReportData
from app.routers.common import (
    check_and_create_emergency_admin,
    create_invited_user,
    create_user,
    csrf_token_for,
    get_config_value,
    is_last_active_superuser,
    is_valid_email,
    issue_invite,
    redirect,
    set_config_value,
    verify_csrf,
)
from app.routers.project_pages import generate_aes_pair, validate_aes
from app.templating import templates

router = APIRouter()


# --- プロジェクト管理 ---


def _projects_page(request: Request, user: AdminUser, db: Session, **context):
    """プロジェクト一覧ページ。context には error / message を渡せる。"""
    report_counts = dict(db.query(ReportData.project_id, func.count(ReportData.id)).group_by(ReportData.project_id).all())
    member_counts = dict(db.query(ProjectMember.project_id, func.count(ProjectMember.id)).group_by(ProjectMember.project_id).all())
    projects = db.query(Project).order_by(Project.id).all()
    rows = [
        {"project": p, "report_count": report_counts.get(p.id, 0), "member_count": member_counts.get(p.id, 0)}
        for p in projects
    ]
    new_key, new_iv = generate_aes_pair()
    return templates.TemplateResponse(
        "admin_projects.html",
        {
            "request": request,
            "user": user.username,
            "rows": rows,
            "csrf_token": csrf_token_for(user),
            "new_aes_key": new_key,
            "new_aes_iv": new_iv,
            **context,
        },
    )


@router.get("/admin/projects", response_class=HTMLResponse)
async def admin_projects(request: Request, user: AdminUser = Depends(require_superuser), db: Session = Depends(get_db)):
    return _projects_page(request, user, db)


@router.post("/admin/projects/create", response_class=HTMLResponse)
async def admin_project_create(
    request: Request,
    slug: str = Form(...),
    name: str = Form(...),
    aes_key: str = Form(...),
    aes_iv: str = Form(...),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    slug = slug.strip().lower()
    name = name.strip()
    error = validate_slug(slug) or validate_aes(aes_key, aes_iv)
    if not error and not (1 <= len(name) <= 100):
        error = "表示名は 1〜100 文字で入力してください"
    if not error and db.query(Project).filter(Project.slug == slug).first():
        error = f"slug '{slug}' は既に使われています"
    if error:
        return _projects_page(request, user, db, error=error)

    project = Project(slug=slug, name=name, aes_key=aes_key, aes_iv=aes_iv, is_active=True)
    db.add(project)
    db.commit()
    return _projects_page(request, user, db, message=f"プロジェクト '{name}'（{slug}）を作成しました。メンバーは /p/{slug}/users で追加できます")


@router.post("/admin/projects/{project_id}/update", response_class=HTMLResponse)
async def admin_project_update(
    request: Request,
    project_id: int,
    name: str = Form(...),
    slug: str = Form(...),
    is_active: bool = Form(False),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    """表示名・slug・受信の有効無効を変更する。

    slug を変えると受信 URL と共有済みの /p/<slug>/ リンクが壊れるため、変更できるのはここだけ
    （画面側で確認ダイアログを出す）。
    """
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        return redirect("/admin/projects")

    slug = slug.strip().lower()
    name = name.strip()
    error = validate_slug(slug)
    if not error and not (1 <= len(name) <= 100):
        error = "表示名は 1〜100 文字で入力してください"
    if not error and slug != project.slug and db.query(Project).filter(Project.slug == slug).first():
        error = f"slug '{slug}' は既に使われています"
    if error:
        return _projects_page(request, user, db, error=error)

    project.name = name
    project.slug = slug
    project.is_active = is_active
    db.commit()
    return _projects_page(request, user, db, message=f"プロジェクト '{name}' を更新しました")


@router.post("/admin/projects/{project_id}/delete", response_class=HTMLResponse)
async def admin_project_delete(
    request: Request,
    project_id: int,
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    """レポートが残っているプロジェクトは削除できない（画像の孤児化を防ぐため先に一括削除する）。"""
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        return redirect("/admin/projects")

    report_count = db.query(func.count(ReportData.id)).filter(ReportData.project_id == project.id).scalar()
    if report_count:
        return _projects_page(request, user, db, error=f"'{project.name}' にはレポートが {report_count} 件残っています。先に /p/{project.slug}/manage で削除してください")

    db.delete(project)  # メンバーは CASCADE で消える
    db.commit()
    return _projects_page(request, user, db, message=f"プロジェクト '{project.name}' を削除しました")


# --- 全ユーザー管理 ---


def _users_page(request: Request, user: AdminUser, db: Session, **context):
    """ユーザー一覧ページを返すヘルパー。

    context には error（エラー文）、emergency（緊急アカウント情報）、
    invite（発行した招待リンクの情報）を渡せる。
    """
    users = db.query(AdminUser).order_by(AdminUser.id).all()
    projects = db.query(Project).order_by(Project.name, Project.id).all()
    return templates.TemplateResponse(
        "user_list.html",
        {
            "request": request,
            "user": user.username,
            "users": users,
            "projects": projects,
            "csrf_token": csrf_token_for(user),
            **context,
        },
    )


@router.get("/admin/users", response_class=HTMLResponse)
async def user_list(request: Request, user: AdminUser = Depends(require_superuser), db: Session = Depends(get_db)):
    return _users_page(request, user, db)


def _resolve_initial_project(db: Session, project_id: int) -> tuple[Project | None, str | None]:
    """新規ユーザーの初期プロジェクト（任意）。0 なら無し。存在しない ID はエラー文。"""
    if not project_id:
        return None, None
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        return None, "指定されたプロジェクトが存在しません"
    return project, None


@router.post("/admin/users/invite", response_class=HTMLResponse)
async def user_invite(
    request: Request,
    email: str = Form(...),
    is_superuser: bool = Form(False),
    project_id: int = Form(0),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    """メールアドレスで招待する。project_id を指定するとそのプロジェクトのメンバーとしても追加する。

    本人が有効化ページで Google ログインかパスワード設定を選ぶ（ユーザー名も本人が決める）。
    """
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    project, error = _resolve_initial_project(db, project_id)
    if error:
        return _users_page(request, user, db, error=error)

    new_user, error = create_invited_user(db, email=email, is_superuser=is_superuser)
    if error:
        return _users_page(request, user, db, error=error)

    if project is not None:
        db.add(ProjectMember(project_id=project.id, user_id=new_user.id, role=ROLE_MEMBER))
        db.commit()

    return _users_page(request, user, db, invite=await issue_invite(new_user, request=request, inviter=user, project=project))


@router.post("/admin/users/create", response_class=HTMLResponse)
async def user_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    is_superuser: bool = Form(False),
    project_id: int = Form(0),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    """パスワードを直接発行してユーザーを作る（緊急用。招待メールが使えない相手など）。

    通常は user_invite を使う。ここで作ったユーザーには初回ログイン後にパスワードを変えてもらう。
    """
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    project, error = _resolve_initial_project(db, project_id)
    if error:
        return _users_page(request, user, db, error=error)

    new_user, error = create_user(db, username=username, password=password, is_superuser=is_superuser, allow_reserved=True)
    if error:
        return _users_page(request, user, db, error=error)

    if project is not None:
        db.add(ProjectMember(project_id=project.id, user_id=new_user.id, role=ROLE_MEMBER))
        db.commit()

    return _users_page(request, user, db, message=f"'{new_user.username}' を作成しました。パスワードは本人に直接伝え、初回ログイン後に変更してもらってください")


@router.post("/admin/users/delete/{user_id}")
async def user_delete(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target and target.id != user.id:
        if is_last_active_superuser(db, target):
            return _users_page(request, user, db, error="最後の有効なシステム管理者は削除できません")
        db.delete(target)
        db.commit()
        emergency = check_and_create_emergency_admin(db)
        if emergency:
            return _users_page(request, user, db, emergency=emergency)
    return redirect("/admin/users")


@router.post("/admin/users/toggle/{user_id}")
async def user_toggle_active(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target and target.id != user.id:
        if target.is_active and is_last_active_superuser(db, target):
            return _users_page(request, user, db, error="最後の有効なシステム管理者は無効化できません")
        target.is_active = not target.is_active
        db.commit()
        emergency = check_and_create_emergency_admin(db)
        if emergency:
            return _users_page(request, user, db, emergency=emergency)
    return redirect("/admin/users")


@router.post("/admin/users/toggle_superuser/{user_id}")
async def user_toggle_superuser(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target and target.id != user.id:
        if target.is_superuser and is_last_active_superuser(db, target):
            return _users_page(request, user, db, error="最後の有効なシステム管理者の権限は解除できません")
        target.is_superuser = not target.is_superuser
        db.commit()
        emergency = check_and_create_emergency_admin(db)
        if emergency:
            return _users_page(request, user, db, emergency=emergency)
    return redirect("/admin/users")


@router.post("/admin/users/reset_password/{user_id}")
async def user_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    if len(new_password) < 4:
        return _users_page(request, user, db, error="パスワードは4文字以上で入力してください")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if target:
        target.password_hash = hash_password(new_password)
        db.commit()
    return redirect("/admin/users")


@router.post("/admin/users/set_email/{user_id}", response_class=HTMLResponse)
async def user_set_email(
    request: Request,
    user_id: int,
    email: str = Form(""),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    """既存ユーザーに Google ログインを付与・変更・解除する。

    - email を新しく設定／変更すると招待リンクを発行する（本人が初回 Google ログインで連携）。
      変更時は既存の Google 連携を解除し、新アドレスの持ち主が連携し直せるようにする
    - 連携済みのユーザーに同じ email を送ると「再連携」（連携解除 + 招待再発行）。
      Google アカウントを作り直して sub が変わった人の復旧経路
    - 空で送ると連携を解除する。パスワードを持たないユーザーの解除はログイン手段が無くなるため拒否
    - 無効化されたまま連携情報を書き換えると「招待中」と区別できず初回ログインで暗黙に有効化されるため、
      連携済みで無効なユーザーは先に有効化してもらう
    - パスワードを持たない最後の有効なシステム管理者のアドレス変更は、誰も入れなくなるため拒否
    """
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target:
        return redirect("/admin/users")

    email = normalize_email(email)
    if email is None:
        if not target.has_password:
            return _users_page(request, user, db, error=f"'{target.username}' はパスワードを持たないため、Google 連携を解除するとログインできなくなります。先にパスワードを設定してください")
        target.email = None
        target.google_sub = None
        db.commit()
        return redirect("/admin/users")

    if not settings.google_enabled:
        return _users_page(request, user, db, error="Google ログインが設定されていないため、Google 連携は設定できません（.env の GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / PUBLIC_BASE_URL）")

    if not is_valid_email(email):
        return _users_page(request, user, db, error="メールアドレスの形式が正しくありません")

    if not target.is_active and target.google_sub is not None:
        return _users_page(request, user, db, error=f"'{target.username}' は無効化されています。Google アカウントを変更・再連携する場合は先に有効化してください")

    if email == target.email:
        if target.google_sub is None:
            # 未連携で変更なし。リンクの再送は「招待リンク再発行」で行う
            return redirect("/admin/users")
        target.google_sub = None
        db.commit()
        return _users_page(request, user, db, invite=await issue_invite(
            target,
            note="既存の Google 連携を解除しました。本人がこのアドレスの Google アカウントでログインし直すと再連携されます",
            request=request,
            inviter=user,
        ))

    if not target.has_password and is_last_active_superuser(db, target):
        return _users_page(request, user, db, error=f"'{target.username}' は最後の有効なシステム管理者でパスワードを持たないため、Google アカウントを変更するとログインできなくなります。先にパスワードを設定してください")

    duplicate = db.query(AdminUser).filter(AdminUser.email == email, AdminUser.id != target.id).first()
    if duplicate:
        return _users_page(request, user, db, error=f"メールアドレス '{email}' は既に '{duplicate.username}' に登録されています")

    target.email = email
    # 別アドレスの持ち主が連携し直せるよう、既存の Google 連携は解除する
    target.google_sub = None
    db.commit()
    return _users_page(request, user, db, invite=await issue_invite(target, request=request, inviter=user))


@router.post("/admin/users/reinvite/{user_id}", response_class=HTMLResponse)
async def user_reinvite(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    """招待リンクを再発行する（有効期限切れ・メール不達時用）。

    対象は招待中のユーザーと、有効だが Google 未連携のユーザー。連携済みには発行しない。
    """
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target or not target.has_google or target.google_sub is not None:
        return redirect("/admin/users")

    if not target.is_invite_pending and not settings.google_enabled:
        return _users_page(request, user, db, error="Google ログインが設定されていないため、Google 連携のリンクは発行できません")

    return _users_page(request, user, db, invite=await issue_invite(target, request=request, inviter=user))


# --- System Config（全体共通: セッション有効期限） ---


def _system_page(request: Request, user: AdminUser, session_hours: str, message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(
        "system_config.html",
        {"request": request, "user": user.username, "session_hours": session_hours, "csrf_token": csrf_token_for(user), "message": message, "error": error},
    )


@router.get("/system", response_class=HTMLResponse)
async def system_config_page(request: Request, user: AdminUser = Depends(require_superuser), db: Session = Depends(get_db)):
    return _system_page(request, user, get_config_value(db, "session_hours", "24"))


@router.post("/system", response_class=HTMLResponse)
async def system_config_submit(
    request: Request,
    session_hours: str = Form("24"),
    csrf_token: str = Form(...),
    user: AdminUser = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, user):
        return redirect("/login")

    try:
        hours = int(session_hours)
        if hours < 1 or hours > 8760:
            raise ValueError
    except (ValueError, TypeError):
        return _system_page(request, user, session_hours, error="セッション有効期限は1〜8760（時間）の整数で入力してください")

    set_config_value(db, "session_hours", str(hours))
    db.commit()
    return _system_page(request, user, str(hours), message="設定を保存しました")
