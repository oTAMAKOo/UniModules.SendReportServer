"""プロジェクト配下（/p/{slug}/...）の管理画面。

レポート一覧・詳細・削除はメンバー以上、レポート管理（一括削除）・メンバー管理・
プロジェクト設定（AES Key/IV）はプロジェクト管理者（またはシステム管理者）のみ。
所属と役割の確認は app/authz.py の require_project が行い、ここでは ctx.project に絞った
クエリだけを書く。
"""
import json
import math
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.auth import normalize_email
from app.authz import (
    ProjectContext,
    REPORT_NOT_FOUND_MESSAGE,
    is_last_active_project_admin,
    get_membership,
    require_project,
)
from app.config import settings
from app.database import get_db
from app.models import ROLE_ADMIN, ROLE_MEMBER, AdminUser, ProjectMember, ReportData
from app.report_export import format_log, search_reports_query
from app.routers.common import create_invited_user, csrf_token_for, issue_invite, redirect, verify_csrf
from app.storage import delete_screenshot, get_image_url
from app.templating import templates

router = APIRouter(prefix="/p/{project_slug}")

ITEMS_PER_PAGE = 25

MemberCtx = Depends(require_project(ROLE_MEMBER))
AdminCtx = Depends(require_project(ROLE_ADMIN))


def _project_redirect(ctx: ProjectContext, path: str):
    return redirect(f"/p/{ctx.project.slug}{path}")


def _base_context(request: Request, ctx: ProjectContext, **extra) -> dict:
    return {
        "request": request,
        "user": ctx.user.username,
        "project": ctx.project,
        "role": ctx.role,
        "is_project_admin": ctx.role == ROLE_ADMIN,
        "csrf_token": csrf_token_for(ctx.user),
        **extra,
    }


# --- レポート一覧・詳細・削除（メンバー以上） ---


@router.get("/list", response_class=HTMLResponse)
async def report_list(
    request: Request,
    page: int = 1,
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    error: str = "",
    ctx: ProjectContext = MemberCtx,
    db: Session = Depends(get_db),
):
    query = search_reports_query(db, q, date_from, date_to, project_id=ctx.project.id)

    total = query.count()
    total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * ITEMS_PER_PAGE

    reports = (
        query
        .order_by(desc(ReportData.id))
        .offset(offset)
        .limit(ITEMS_PER_PAGE)
        .all()
    )

    # サムネイル URL を付与
    for r in reports:
        r.thumb_url = get_image_url(r.img_thumbnail_name) if r.img_thumbnail_name else None

    messages = {"forbidden": "この操作にはプロジェクト管理者の権限が必要です"}
    return templates.TemplateResponse(
        "report_list.html",
        _base_context(
            request, ctx,
            reports=reports,
            page=page,
            total_pages=total_pages,
            total=total,
            q=q,
            date_from=date_from,
            date_to=date_to,
            error=messages.get(error),
        ),
    )


def _get_project_report(db: Session, ctx: ProjectContext, report_id: int) -> ReportData | None:
    return db.query(ReportData).filter(
        ReportData.id == report_id,
        ReportData.project_id == ctx.project.id,
    ).first()


@router.get("/detail/{report_id}", response_class=HTMLResponse)
async def report_detail(
    request: Request,
    report_id: int,
    ctx: ProjectContext = MemberCtx,
    db: Session = Depends(get_db),
):
    report = _get_project_report(db, ctx, report_id)
    if not report:
        return templates.TemplateResponse(
            "error.html",
            _base_context(request, ctx, message=REPORT_NOT_FOUND_MESSAGE.format(id=report_id)),
            status_code=404,
        )

    log_entries = format_log(report.log)

    img_url = None
    thumb_url = None
    if report.img_name:
        img_url = get_image_url(report.img_name)
        thumb_url = get_image_url(report.img_thumbnail_name) if report.img_thumbnail_name else img_url

    extend_info = {}
    if report.extend_info:
        try:
            extend_info = json.loads(report.extend_info)
        except json.JSONDecodeError:
            pass

    return templates.TemplateResponse(
        "report_detail.html",
        _base_context(
            request, ctx,
            report=report,
            log_entries=log_entries,
            img_url=img_url,
            thumb_url=thumb_url,
            extend_info=extend_info,
        ),
    )


@router.post("/delete/{report_id}")
async def report_delete(
    report_id: int,
    csrf_token: str = Form(...),
    ctx: ProjectContext = MemberCtx,
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")

    report = _get_project_report(db, ctx, report_id)
    if report:
        # 画像ファイルも削除
        delete_screenshot(report.img_name, report.img_thumbnail_name)
        db.delete(report)
        db.commit()
    return _project_redirect(ctx, "/list")


# --- レポート管理（一括削除、プロジェクト管理者） ---


def _manage_page(request: Request, ctx: ProjectContext, db: Session, message: str | None = None, error: str | None = None):
    total_count = db.query(func.count(ReportData.id)).filter(ReportData.project_id == ctx.project.id).scalar()
    return templates.TemplateResponse(
        "report_manage.html",
        _base_context(request, ctx, total_count=total_count, message=message, error=error),
    )


@router.get("/manage", response_class=HTMLResponse)
async def report_manage_page(request: Request, ctx: ProjectContext = AdminCtx, db: Session = Depends(get_db)):
    return _manage_page(request, ctx, db)


@router.post("/manage/bulk_delete")
async def report_bulk_delete(
    request: Request,
    date_from: str = Form(None),
    date_to: str = Form(None),
    csrf_token: str = Form(...),
    ctx: ProjectContext = AdminCtx,
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")

    if not date_from and not date_to:
        return _manage_page(request, ctx, db, error="日付を指定してください")

    # 日付の形式不正は無視せずエラーにする（無視すると条件無しの全件削除になってしまう）
    query = db.query(ReportData).filter(ReportData.project_id == ctx.project.id)
    try:
        if date_from:
            query = query.filter(ReportData.created_at >= datetime.strptime(date_from.strip(), "%Y-%m-%d"))
        if date_to:
            query = query.filter(ReportData.created_at < datetime.strptime(date_to.strip(), "%Y-%m-%d") + timedelta(days=1))
    except ValueError:
        return _manage_page(request, ctx, db, error="日付は YYYY-MM-DD の形式で指定してください")

    # 画像ファイルも削除
    reports = query.all()
    for report in reports:
        delete_screenshot(report.img_name, report.img_thumbnail_name)
        db.delete(report)
    db.commit()

    return _manage_page(request, ctx, db, message=f"{len(reports)} 件のレポートを削除しました")


# --- メンバー管理（プロジェクト管理者） ---


def _members_page(request: Request, ctx: ProjectContext, db: Session, **context):
    """メンバー一覧ページ。context には error / message / invite を渡せる。"""
    rows = (
        db.query(ProjectMember, AdminUser)
        .join(AdminUser, AdminUser.id == ProjectMember.user_id)
        .filter(ProjectMember.project_id == ctx.project.id)
        .order_by(AdminUser.id)
        .all()
    )
    members = [{"membership": m, "user": u} for m, u in rows]
    return templates.TemplateResponse(
        "project_members.html",
        _base_context(request, ctx, members=members, **context),
    )


@router.get("/users", response_class=HTMLResponse)
async def member_list(request: Request, ctx: ProjectContext = AdminCtx, db: Session = Depends(get_db)):
    return _members_page(request, ctx, db)


@router.post("/users/add", response_class=HTMLResponse)
async def member_add(
    request: Request,
    identifier: str = Form(...),
    role: str = Form(ROLE_MEMBER),
    csrf_token: str = Form(...),
    ctx: ProjectContext = AdminCtx,
    db: Session = Depends(get_db),
):
    """既存ユーザーをユーザー名またはメールアドレスでプロジェクトに追加する。

    '@' を含む識別子はメールアドレスとしてだけ探す（ユーザー名には '@' を使えないので、
    メールアドレスそっくりのユーザー名で別人を紛れ込ませることはできない）。
    プロジェクト管理者が全ユーザーを探る手段にならないよう、「見つからない」と「既にメンバー」は
    同じ文言で返し、成功時も入力された識別子だけを表示する（相手のユーザー名は一覧で分かる範囲に留める）。
    """
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")
    if role not in (ROLE_ADMIN, ROLE_MEMBER):
        return _members_page(request, ctx, db, error="役割の指定が不正です")

    identifier = identifier.strip()
    if not identifier:
        return _members_page(request, ctx, db, error="ユーザー名またはメールアドレスを入力してください")
    if "@" in identifier:
        email = normalize_email(identifier)
        target = db.query(AdminUser).filter(AdminUser.email == email).first() if email else None
    else:
        target = db.query(AdminUser).filter(AdminUser.username == identifier).first()

    not_added = (
        f"'{identifier}' は追加できませんでした（該当するユーザーが存在しないか、既にメンバーです）。"
        "アカウントが無い人は下のフォームから作成してください"
    )
    if target is None or get_membership(db, target.id, ctx.project.id):
        return _members_page(request, ctx, db, error=not_added)

    db.add(ProjectMember(project_id=ctx.project.id, user_id=target.id, role=role))
    db.commit()
    return _members_page(request, ctx, db, message=f"'{identifier}' を追加しました")


@router.post("/users/invite", response_class=HTMLResponse)
async def member_invite(
    request: Request,
    email: str = Form(...),
    role: str = Form(ROLE_MEMBER),
    csrf_token: str = Form(...),
    ctx: ProjectContext = AdminCtx,
    db: Session = Depends(get_db),
):
    """メールアドレスで新しい人を招待し、このプロジェクトのメンバーにする。

    招待中ユーザーを作って招待リンクを発行する（MAIL_MODE によりメール送信）。本人が有効化ページで
    Google ログインかパスワード設定を選ぶと有効になる。ユーザー名やパスワードを管理者は決めない。
    role="sysadmin"（システム管理者として招待。このプロジェクトには管理者として所属）は操作者が
    システム管理者のときだけ受け付ける。プロジェクト管理者が自分より強い権限を作れないようにするため。
    """
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")
    is_superuser = False
    if role == "sysadmin":
        if not ctx.user.is_superuser:
            return _members_page(request, ctx, db, error="システム管理者として招待できるのはシステム管理者だけです")
        is_superuser = True
        role = ROLE_ADMIN
    elif role not in (ROLE_ADMIN, ROLE_MEMBER):
        return _members_page(request, ctx, db, error="役割の指定が不正です")

    new_user, error = create_invited_user(db, email=email, is_superuser=is_superuser)
    if error:
        return _members_page(request, ctx, db, error=error)

    db.add(ProjectMember(project_id=ctx.project.id, user_id=new_user.id, role=role))
    db.commit()
    return _members_page(request, ctx, db, invite=await issue_invite(new_user, request=request, inviter=ctx.user, project=ctx.project))


@router.post("/users/role/{user_id}")
async def member_change_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    csrf_token: str = Form(...),
    ctx: ProjectContext = AdminCtx,
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")
    if role not in (ROLE_ADMIN, ROLE_MEMBER):
        return _members_page(request, ctx, db, error="役割の指定が不正です")

    membership = get_membership(db, user_id, ctx.project.id)
    if membership is None:
        return _project_redirect(ctx, "/users")
    if membership.role == role:
        return _project_redirect(ctx, "/users")
    if role == ROLE_MEMBER and not ctx.user.is_superuser and is_last_active_project_admin(db, ctx.project, membership.user):
        return _members_page(request, ctx, db, error="最後のプロジェクト管理者は降格できません。先に別の管理者を指定してください")

    membership.role = role
    db.commit()
    return _project_redirect(ctx, "/users")


@router.post("/users/remove/{user_id}")
async def member_remove(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    ctx: ProjectContext = AdminCtx,
    db: Session = Depends(get_db),
):
    """プロジェクトから外す（アカウントは削除しない）。"""
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")

    if user_id == ctx.user.id:
        return _members_page(request, ctx, db, error="自分をプロジェクトから外すことはできません")
    membership = get_membership(db, user_id, ctx.project.id)
    if membership is None:
        return _project_redirect(ctx, "/users")
    if not ctx.user.is_superuser and is_last_active_project_admin(db, ctx.project, membership.user):
        return _members_page(request, ctx, db, error="最後のプロジェクト管理者は外せません。先に別の管理者を指定してください")

    db.delete(membership)
    db.commit()
    return _project_redirect(ctx, "/users")


@router.post("/users/reinvite/{user_id}", response_class=HTMLResponse)
async def member_reinvite(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    ctx: ProjectContext = AdminCtx,
    db: Session = Depends(get_db),
):
    """メンバーの招待リンクを再発行する（有効期限切れ・メール不達時用）。

    対象は招待中のユーザーと、有効だが Google 未連携（管理者が「Google連携」で email を付けた）のユーザー。
    連携済みのユーザーには発行しない（リンクは単回利用ではないので、有効化済みへの再送は意味が無い）。
    """
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")

    membership = get_membership(db, user_id, ctx.project.id)
    target = membership.user if membership else None
    if not target or not target.has_google or target.google_sub is not None:
        return _project_redirect(ctx, "/users")
    if not target.is_invite_pending and not settings.google_enabled:
        return _members_page(request, ctx, db, error="Google ログインが設定されていないため、Google 連携のリンクは発行できません")

    return _members_page(request, ctx, db, invite=await issue_invite(target, request=request, inviter=ctx.user, project=ctx.project, resend=True))


# --- プロジェクト設定（AES Key/IV、プロジェクト管理者） ---


def _settings_page(request: Request, ctx: ProjectContext, aes_key: str, aes_iv: str, message: str | None = None, error: str | None = None):
    base_url = settings.public_base_url or str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        "project_settings.html",
        _base_context(
            request, ctx,
            aes_key=aes_key,
            aes_iv=aes_iv,
            ingest_url=f"{base_url}{settings.url_prefix}/report/{ctx.project.slug}",
            message=message,
            error=error,
        ),
    )


@router.get("/settings", response_class=HTMLResponse)
async def project_settings_page(request: Request, ctx: ProjectContext = AdminCtx):
    return _settings_page(request, ctx, ctx.project.aes_key, ctx.project.aes_iv)


@router.post("/settings", response_class=HTMLResponse)
async def project_settings_submit(
    request: Request,
    aes_key: str = Form(...),
    aes_iv: str = Form(...),
    csrf_token: str = Form(...),
    ctx: ProjectContext = AdminCtx,
    db: Session = Depends(get_db),
):
    if not verify_csrf(csrf_token, ctx.user):
        return redirect("/login")

    error = validate_aes(aes_key, aes_iv)
    if error:
        return _settings_page(request, ctx, aes_key, aes_iv, error=error)

    ctx.project.aes_key = aes_key
    ctx.project.aes_iv = aes_iv
    db.commit()
    return _settings_page(request, ctx, aes_key, aes_iv, message="AES Key / IV を保存しました。クライアント側の鍵も同じ値にしてください")


def validate_aes(aes_key: str, aes_iv: str) -> str | None:
    """AES Key（32 バイト）/ IV（16 バイト）の長さを確認する。問題なければ None。"""
    if len(aes_key.encode("utf-8")) != 32:
        return "AES Key は 32 文字（AES-256）で指定してください"
    if len(aes_iv.encode("utf-8")) != 16:
        return "AES IV は 16 文字で指定してください"
    return None


def generate_aes_pair() -> tuple[str, str]:
    """新規プロジェクト用のランダムな AES Key（32 文字）/ IV（16 文字）を作る。"""
    return secrets.token_hex(16), secrets.token_hex(8)
