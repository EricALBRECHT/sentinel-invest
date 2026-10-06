"""HTML routes. They do not replace the JSON API."""

import asyncio
import json
from urllib.parse import quote

from markupsafe import Markup

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jwt.exceptions import PyJWTError
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, verify_password
from app.db.session import get_db
from app.models.user import User
from app.web import i18n, viewmodels

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
def _tojson(value: object) -> Markup:
    return Markup(json.dumps(value).replace("<", "\\u003c"))


templates = Jinja2Templates(directory=_TEMPLATES)
templates.env.filters["tojson"] = _tojson
templates.env.globals["badge_tone"] = viewmodels.badge_tone
templates.env.globals["display"] = viewmodels.display
templates.env.globals["display_ratio"] = viewmodels.display_ratio
templates.env.globals["display_compact"] = viewmodels.display_compact
for _name in (
    "translate_label",
    "translate_warning",
    "label_universe_status",
    "label_discovery_source",
    "label_universe_name",
    "label_coverage",
    "label_readiness",
    "label_conviction",
    "label_entry",
    "label_trend",
    "label_verification",
    "label_pipeline",
    "label_relationship_status",
    "label_relationship_type",
    "label_event",
    "label_importance",
    "label_candidate_status",
    "label_document_type",
    "label_direction",
    "label_system_status",
    "label_ai_analysis_status",
):
    templates.env.globals[_name] = getattr(i18n, _name)

router = APIRouter(tags=["web"], include_in_schema=False)
_COOKIE = "sentinel_token"


class LoginRequired(Exception):
    def __init__(self, target: str) -> None:
        self.target = target


async def login_required_handler(request: Request, exc: LoginRequired) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(exc.target, safe='/')}", status_code=303)


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/dashboard"
    return value


async def page_user(request: Request, db: AsyncSession) -> User:
    user = await optional_user(request, db)
    if user is None:
        raise LoginRequired(request.url.path)
    return user


async def optional_user(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get(_COOKIE)
    if not token:
        return None
    try:
        user_id = int(decode_access_token(token))
    except (PyJWTError, ValueError):
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def _cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _COOKIE,
        token,
        httponly=True,
        secure=settings.web_cookie_secure,
        samesite="lax",
        max_age=max(60, settings.access_token_expire_minutes * 60),
        path="/",
    )


@router.get("/login")
async def login_form(request: Request, db: AsyncSession = Depends(get_db), next: str | None = None):
    if await optional_user(request, db) is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    notice = i18n.SESSION_EXPIRED if request.cookies.get(_COOKIE) else None
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "user": None, "error": None, "notice": notice, "next": safe_next(next) if next else ""},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    next: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "user": None,
                "error": i18n.LOGIN_ERROR,
                "notice": None,
                "next": safe_next(next) if next else "",
            },
            status_code=401,
        )
    response = RedirectResponse(safe_next(next), status_code=303)
    _cookie(response, create_access_token(str(user.id)))
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(_COOKIE, path="/")
    return response


@router.get("/dashboard")
async def dashboard(
    request: Request,
    universe_status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    user = await page_user(request, db)
    hx = request.headers.get("HX-Request") == "true"
    page = await viewmodels.dashboard_page(
        db, universe_status, search=q, limit=limit, offset=offset, include_live=not hx
    )
    context = {"request": request, "user": user, **page}
    if hx:
        return templates.TemplateResponse(request, "partials/dashboard_table.html", context)
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.get("/dashboard/fragments/summary")
async def dashboard_fragment_summary(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.dashboard_summary_fragment(db)
    return templates.TemplateResponse(
        request, "partials/dashboard_summary.html", {"request": request, "user": user, **page}
    )


@router.get("/dashboard/fragments/companies")
async def dashboard_fragment_companies(
    request: Request,
    universe_status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    user = await page_user(request, db)
    page = await viewmodels.dashboard_page(
        db, universe_status, search=q, limit=limit, offset=offset, include_live=False
    )
    return templates.TemplateResponse(
        request, "partials/dashboard_table.html", {"request": request, "user": user, **page}
    )


@router.get("/dashboard/fragments/bootstrap")
async def dashboard_fragment_bootstrap(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.dashboard_bootstrap_fragment(db)
    return templates.TemplateResponse(
        request, "partials/dashboard_bootstrap.html", {"request": request, "user": user, **page}
    )


@router.get("/dashboard/fragments/jobs")
async def dashboard_fragment_jobs(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.dashboard_jobs_fragment(db)
    return templates.TemplateResponse(
        request, "partials/dashboard_jobs.html", {"request": request, "user": user, **page}
    )


@router.get("/dashboard/fragments/gpu")
async def dashboard_fragment_gpu(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.dashboard_gpu_fragment(db)
    return templates.TemplateResponse(
        request, "partials/dashboard_gpu.html", {"request": request, "user": user, **page}
    )


@router.get("/companies/{company_id}/view")
async def company_view(
    request: Request,
    company_id: int,
    range: str = Query(default="1Y"),
    depth: int = Query(default=1, ge=1, le=2),
    db: AsyncSession = Depends(get_db),
):
    user = await page_user(request, db)
    page = await viewmodels.company_page(db, company_id, range, depth)
    if page is None:
        return templates.TemplateResponse(
            request,
            "not_found.html",
            {"request": request, "user": user},
            status_code=404,
        )
    return templates.TemplateResponse(request, "company_detail.html", {"request": request, "user": user, **page})


@router.get("/discovery")
async def discovery(
    request: Request,
    status: str | None = Query(default=None),
    verification_status: str | None = Query(default=None),
    depth: int | None = Query(default=None, ge=0, le=20),
    confidence_min: int | None = Query(default=None, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
):
    user = await page_user(request, db)
    page = await viewmodels.discovery_page(
        db,
        status=status,
        verification_status=verification_status,
        depth=depth,
        confidence_min=confidence_min,
    )
    context = {"request": request, "user": user, **page}
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/discovery_results.html", context)
    return templates.TemplateResponse(request, "discovery.html", context)


@router.get("/admin/view")
async def admin_view(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.admin_page(db)
    context = {"request": request, "user": user, **page}
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/admin_status.html", context)
    return templates.TemplateResponse(request, "admin.html", context)


@router.get("/admin/view/fragments/jobs")
async def admin_fragment_jobs(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.admin_jobs_fragment(db)
    return templates.TemplateResponse(
        request, "partials/admin_jobs.html", {"request": request, "user": user, **page}
    )


@router.get("/admin/view/fragments/bootstrap")
async def admin_fragment_bootstrap(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.admin_bootstrap_fragment(db)
    return templates.TemplateResponse(
        request, "partials/admin_bootstrap.html", {"request": request, "user": user, **page}
    )


@router.get("/admin/view/fragments/gpu")
async def admin_fragment_gpu(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.admin_gpu_fragment(db)
    return templates.TemplateResponse(
        request, "partials/admin_gpu.html", {"request": request, "user": user, **page}
    )


@router.get("/admin/view/fragments/overview")
async def admin_fragment_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await page_user(request, db)
    page = await viewmodels.admin_overview_fragment(db)
    return templates.TemplateResponse(
        request, "partials/admin_overview.html", {"request": request, "user": user, **page}
    )


@router.post("/admin/view/refresh-sp500")
async def admin_refresh_sp500(request: Request, db: AsyncSession = Depends(get_db)):
    await page_user(request, db)
    from app.services.universe.refresh import refresh_sp500

    await refresh_sp500(db)
    return RedirectResponse("/admin/view", status_code=303)


@router.post("/admin/view/refresh-nasdaq100")
async def admin_refresh_nasdaq100(request: Request, db: AsyncSession = Depends(get_db)):
    await page_user(request, db)
    from app.services.universe.refresh import refresh_nasdaq100

    await refresh_nasdaq100(db)
    return RedirectResponse("/admin/view", status_code=303)


@router.post("/admin/view/bootstrap")
async def admin_bootstrap(request: Request, db: AsyncSession = Depends(get_db)):
    await page_user(request, db)
    from app.services.universe.bootstrap import bootstrap_universe_data

    await bootstrap_universe_data(db)
    return RedirectResponse("/admin/view", status_code=303)


@router.post("/companies/{company_id}/documents/{document_id}/ai-analyze")
async def company_document_ai_analyze(
    request: Request,
    company_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
):
    await page_user(request, db)
    from app.jobs.queues import enqueue_ai_document_analysis

    await asyncio.to_thread(enqueue_ai_document_analysis, document_id)
    return RedirectResponse(f"/companies/{company_id}/view", status_code=303)
