"""Read models for the HTML pages. One query batch per section, no internal HTTP."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.company_score import CompanyScore
from app.models.financial_metric import FinancialMetric
from app.models.intelligence import DocumentCompany, ExternalDocument, ExternalSource
from app.models.investment_view import InvestmentView
from app.models.market_price import MarketPrice
from app.models.opportunity_score import OpportunityScore
from app.models.supply_chain import DiscoveredCompany
from app.models.technical_snapshot import TechnicalSnapshot
from app.models.universe_membership import UniverseMembership
from app.services.admin.status import build_admin_status
from app.services.gpu.workers import gpu_workers_for_page
from app.services.intelligence.documents import intelligence_counts
from app.services.supply_chain.graph import get_company_graph
from app.services.universe.bootstrap import completeness_for, universe_market_status
from app.services.universe.manager import count_universe, list_universe
from app.web.i18n import format_date, format_number, label_relationship_status, label_relationship_type

_STATUS_ORDER = {
    "PORTFOLIO": 0,
    "DEEP_ANALYSIS": 1,
    "WATCHED": 2,
    "SCREENED": 3,
    "DISCOVERED": 4,
    "ARCHIVED": 5,
}
_DASHBOARD_DEFAULT = ("PORTFOLIO", "DEEP_ANALYSIS", "WATCHED", "DISCOVERED")
_DASHBOARD_PAGE_SIZE = 50
_RANGES = {"3M": 92, "6M": 183, "1Y": 366, "3Y": 1095, "5Y": 1826, "MAX": None}
_GOOD = frozenset(
    {"HIGH", "VERY_HIGH", "STRONG", "CONFIRMED", "VERIFIED", "COMPLETE", "USABLE", "GOOD", "ANALYZED", "PUBLIC_COMPANY"}
)
_WARN = frozenset(
    {
        "PARTIAL",
        "MODERATE",
        "NEUTRAL",
        "FAVORABLE",
        "DISCOVERED",
        "COLLECTING",
        "READY",
        "NEW",
        "UNVERIFIED",
        "CANDIDATE",
        "WATCHED",
        "DEEP_ANALYSIS",
        "PORTFOLIO",
        "SCREENED",
    }
)
_BAD = frozenset({"LOW", "POOR", "REJECTED", "BLOCKED", "INCOMPLETE", "EXTENDED_OR_EXCEPTIONAL"})
_ROLES = frozenset(
    {
        "CUSTOMER",
        "SUPPLIER",
        "PARTNER",
        "COMPETITOR",
        "INVESTOR",
        "SUBCONTRACTOR",
        "EQUIPMENT_PROVIDER",
        "MATERIAL_PROVIDER",
        "INFRASTRUCTURE_PROVIDER",
        "OTHER",
    }
)


def badge_tone(value: object) -> str:
    text = "" if value is None else str(value).strip().upper()
    if not text:
        return "muted"
    if text in _BAD:
        return "bad"
    if text in _WARN:
        return "warn"
    if text in _GOOD:
        return "good"
    return "info"


def display(value: object, digits: int = 2) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, datetime):
        return format_date(value.date())
    if isinstance(value, date):
        return format_date(value)
    if isinstance(value, (Decimal, float, int)):
        return format_number(value, digits)
    return str(value)


def display_ratio(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    return f"{format_number(number * Decimal(100), 1)} %"


def display_compact(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    absolute = abs(number)
    for scale, suffix in ((Decimal("1000000000000"), "T"), (Decimal("1000000000"), "B"), (Decimal("1000000"), "M")):
        if absolute >= scale:
            return f"{format_number(number / scale, 2)} {suffix}"
    return format_number(number, 0)


async def dashboard_page(
    session: AsyncSession,
    universe_status: str | None,
    *,
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    page_size = _DASHBOARD_PAGE_SIZE if limit is None else max(1, min(200, limit))
    page_offset = max(0, offset)
    statuses = None if universe_status else list(_DASHBOARD_DEFAULT)
    status = universe_status or None
    companies = await list_universe(
        session,
        status=status,
        statuses=statuses,
        active=True,
        search=search,
        require_membership=False,
        limit=page_size,
        offset=page_offset,
    )
    total = await count_universe(
        session,
        status=status,
        statuses=statuses,
        active=True,
        search=search,
        require_membership=False,
    )
    companies.sort(
        key=lambda company: (
            _STATUS_ORDER.get(company.universe_status, 9),
            -int(company.universe_priority or 0),
            company.ticker,
        )
    )
    ids = [company.id for company in companies]
    quality = await _latest(session, CompanyScore, "score_date", ids)
    opportunity = await _latest(session, OpportunityScore, "score_date", ids)
    technical = await _latest(session, TechnicalSnapshot, "as_of_date", ids)
    views = await _latest(session, InvestmentView, "as_of_date", ids)
    snapshots = await _snapshots(session, ids)
    completeness = await completeness_for(session, ids)
    rows = []
    for company in companies:
        view = views.get(company.id)
        score = quality.get(company.id)
        opp = opportunity.get(company.id)
        tech = technical.get(company.id)
        market = snapshots.get(company.id)
        ready = completeness.get(company.id, {})
        rows.append(
            {
                "company": company,
                "quality": None if score is None else score.quality_score,
                "opportunity": None if opp is None else opp.opportunity_score,
                "coverage": None if opp is None else opp.coverage_score,
                "coverage_status": None if opp is None else opp.coverage_status,
                "technical": None if tech is None else tech.technical_score,
                "conviction": None if view is None else view.long_term_conviction_score,
                "conviction_label": None if view is None else view.long_term_conviction_label,
                "entry": None if view is None else view.entry_attractiveness_score,
                "entry_label": None if view is None else view.entry_attractiveness_label,
                "readiness": None if view is None else view.analysis_readiness_score,
                "readiness_label": None if view is None else view.analysis_readiness_label,
                "market_date": None if market is None else market.last_market_date,
                "price": None if market is None else market.price,
                "market_ready": ready.get("market_ready", False),
                "sec_ready": ready.get("sec_ready", False),
            }
        )
    counts = await _summary(session)
    return {
        "rows": rows,
        "summary": counts,
        "universe_status": universe_status or "",
        "search": search or "",
        "limit": page_size,
        "offset": page_offset,
        "total": total,
        "has_prev": page_offset > 0,
        "has_next": page_offset + page_size < total,
        "prev_offset": max(0, page_offset - page_size),
        "next_offset": page_offset + page_size,
    }


async def company_page(session: AsyncSession, company_id: int, chart_range: str, depth: int) -> dict | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    selected = chart_range if chart_range in _RANGES else "1Y"
    graph_depth = 1 if depth <= 1 else 2
    quality = (await _latest(session, CompanyScore, "score_date", [company.id])).get(company.id)
    opportunity = (await _latest(session, OpportunityScore, "score_date", [company.id])).get(company.id)
    technical = (await _latest(session, TechnicalSnapshot, "as_of_date", [company.id])).get(company.id)
    view = (await _latest(session, InvestmentView, "as_of_date", [company.id])).get(company.id)
    market = (await _snapshots(session, [company.id])).get(company.id)
    fiscal = await session.scalar(
        select(FinancialMetric)
        .where(FinancialMetric.company_id == company.id, FinancialMetric.fiscal_period == "FY")
        .order_by(FinancialMetric.fiscal_year.desc(), FinancialMetric.id.desc())
    )
    parent = None
    if company.discovered_parent_company_id is not None:
        parent = await session.get(Company, company.discovered_parent_company_id)
    memberships = list(
        (
            await session.scalars(
                select(UniverseMembership.universe_name).where(
                    UniverseMembership.company_id == company.id,
                    UniverseMembership.is_active.is_(True),
                )
            )
        ).all()
    )
    documents = await _documents(session, company.id)
    graph = await get_company_graph(session, company.id, graph_depth)
    chart = await chart_series(session, company.id, selected)
    metrics = {} if quality is None else dict(quality.metrics_json or {})
    warnings = []
    if view is not None:
        warnings = list((view.components_json or {}).get("warnings") or [])
    return {
        "company": company,
        "parent": parent,
        "memberships": memberships,
        "quality": quality,
        "metrics": metrics,
        "fiscal": fiscal,
        "opportunity": opportunity,
        "technical": technical,
        "view": view,
        "warnings": warnings,
        "market": market,
        "documents": documents,
        "graph": graph,
        "mermaid": mermaid_graph(company, graph),
        "chart": chart,
        "chart_range": selected,
        "ranges": list(_RANGES),
        "graph_depth": graph_depth,
        "confirmed": [edge for edge in graph["edges"] if edge["status"] == "CONFIRMED"],
        "discovered_edges": [edge for edge in graph["edges"] if edge["status"] == "DISCOVERED"],
    }


async def chart_series(session: AsyncSession, company_id: int, chart_range: str) -> dict:
    selected = chart_range if chart_range in _RANGES else "1Y"
    prices = list(
        (
            await session.scalars(
                select(MarketPrice)
                .where(MarketPrice.company_id == company_id)
                .order_by(MarketPrice.trade_date.asc(), MarketPrice.id.asc())
            )
        ).all()
    )
    closes: list[float | None] = []
    volumes: list[int | None] = []
    labels: list[str] = []
    for row in prices:
        price = row.adjusted_close if row.adjusted_close is not None else row.close
        labels.append(row.trade_date.isoformat())
        closes.append(None if price is None else float(price))
        volumes.append(None if row.volume is None else int(row.volume))
    start = _range_start(labels, selected)
    warmup = 200
    begin = max(0, start - warmup)
    window_labels = labels[begin:]
    window_closes = closes[begin:]
    sma20 = _sma(window_closes, 20)
    sma50 = _sma(window_closes, 50)
    sma200 = _sma(window_closes, 200)
    offset = start - begin
    return {
        "labels": window_labels[offset:],
        "price": window_closes[offset:],
        "sma20": sma20[offset:],
        "sma50": sma50[offset:],
        "sma200": sma200[offset:],
        "volume": volumes[start:],
    }


async def discovery_page(
    session: AsyncSession,
    *,
    status: str | None,
    verification_status: str | None,
    depth: int | None,
    confidence_min: int | None,
) -> dict:
    statement = select(DiscoveredCompany).order_by(DiscoveredCompany.confidence.desc(), DiscoveredCompany.id.asc())
    if status:
        statement = statement.where(DiscoveredCompany.status == status)
    if verification_status:
        statement = statement.where(DiscoveredCompany.verification_status == verification_status)
    if confidence_min is not None:
        statement = statement.where(DiscoveredCompany.confidence >= confidence_min)
    candidates = list((await session.scalars(statement)).all())
    companies = list(
        (
            await session.scalars(
                select(Company).where(Company.universe_status == "DISCOVERED", Company.is_active.is_(True))
            )
        ).all()
    )
    if depth is not None:
        companies = [company for company in companies if int(company.discovery_depth or 0) == depth]
    companies.sort(key=lambda company: (int(company.discovery_depth or 0), company.ticker))
    parent_ids = {row.discovered_from_company_id for row in candidates}
    parent_ids.update(company.discovered_parent_company_id for company in companies if company.discovered_parent_company_id)
    parents = {}
    if parent_ids:
        parents = {
            company.id: company
            for company in (await session.scalars(select(Company).where(Company.id.in_(parent_ids)))).all()
        }
    promoted_ids = [row.promoted_company_id for row in candidates if row.promoted_company_id]
    promoted = {}
    if promoted_ids:
        promoted = {
            company.id: company
            for company in (await session.scalars(select(Company).where(Company.id.in_(promoted_ids)))).all()
        }
    return {
        "candidates": [
            {
                "row": row,
                "parent": parents.get(row.discovered_from_company_id),
                "promoted": promoted.get(row.promoted_company_id) if row.promoted_company_id else None,
                "role": proposed_role(row.discovery_reason),
                "depth": _candidate_depth(parents.get(row.discovered_from_company_id)),
            }
            for row in candidates
        ],
        "companies": [
            {"company": company, "parent": parents.get(company.discovered_parent_company_id)}
            for company in companies
        ],
        "filters": {
            "status": status or "",
            "verification_status": verification_status or "",
            "depth": "" if depth is None else str(depth),
            "confidence_min": "" if confidence_min is None else str(confidence_min),
        },
    }


async def admin_page(session: AsyncSession) -> dict:
    status = await build_admin_status(session)
    deepest = await session.scalar(select(func.max(Company.discovery_depth)))
    market = await universe_market_status(session)
    return {
        "status": status,
        "deepest_depth": int(deepest or 0),
        "netdata_url": "http://192.168.1.116:19999",
        "gpu_workers": [present_gpu_worker(row) for row in gpu_workers_for_page()],
        "universe_market": present_universe_market(market),
    }


def present_universe_market(market: dict) -> dict:
    bootstrap = market.get("bootstrap") or {}
    pending = int(bootstrap.get("pending_market") or 0) + int(bootstrap.get("pending_sec") or 0)
    ready = int(bootstrap.get("ready") or 0)
    total = pending + ready
    progress = "—" if total == 0 else f"{ready} / {total}"
    return {
        "sp500_members": (market.get("SP500") or {}).get("members", 0),
        "sp500_refresh": _gpu_moment((market.get("SP500") or {}).get("last_refresh")),
        "nasdaq_members": (market.get("NASDAQ100") or {}).get("members", 0),
        "nasdaq_refresh": _gpu_moment((market.get("NASDAQ100") or {}).get("last_refresh")),
        "pending_market": bootstrap.get("pending_market", 0),
        "pending_sec": bootstrap.get("pending_sec", 0),
        "ready": ready,
        "progress": progress,
        "last_bootstrap": _gpu_moment(bootstrap.get("last_bootstrap")),
        "max_per_run": bootstrap.get("max_per_run"),
    }


def present_gpu_worker(row: dict) -> dict:
    probe = row.get("probe") if isinstance(row.get("probe"), dict) else None
    gpu_name = row.get("gpu_name") or (probe or {}).get("gpu_name")
    memory = row.get("gpu_memory_total")
    if memory is None and probe is not None:
        memory = probe.get("gpu_memory_total_mb")
    status = str(row.get("status") or "offline")
    return {
        "name": row.get("name") or "—",
        "presence_label": "En ligne" if row.get("online") else "Hors ligne",
        "presence_tone": "good" if row.get("online") else "bad",
        "gpu_name": gpu_name or "—",
        "memory_label": f"{int(memory)} Mo" if isinstance(memory, int) else "—",
        "heartbeat_label": _gpu_moment(row.get("last_heartbeat")),
        "status_label": {"online": "En ligne", "offline": "Hors ligne", "degraded": "GPU indisponible"}.get(status, status),
        "probe_label": _probe_label(probe),
    }


def _probe_label(probe: dict | None) -> str:
    if not probe:
        return "—"
    when = _gpu_moment(probe.get("timestamp"))
    if probe.get("gpu_available"):
        name = probe.get("gpu_name") or "GPU"
        return f"{name} · {when}"
    return f"indisponible · {when}"


def _gpu_moment(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    local = moment.astimezone()
    return local.strftime("%d/%m/%Y %H:%M")


def proposed_role(reason: str | None) -> str:
    token = ((reason or "").split(" ", 1)[0] or "").upper()
    if token in _ROLES:
        return token
    return "—"


def mermaid_graph(company: Company, graph: dict) -> str:
    names = {node["company_id"]: node for node in graph["nodes"]}
    lines = ["flowchart LR"]
    for node in graph["nodes"]:
        label = _safe_label(f"{node['ticker']} {node['name']}")
        lines.append(f"n{node['company_id']}[{label}]")
    if company.id not in names:
        lines.append(f"n{company.id}[{_safe_label(company.ticker)}]")
    for edge in graph["edges"]:
        label = _safe_label(
            f"{label_relationship_type(edge['relationship_type'])} {edge['confidence']} {label_relationship_status(edge['status'])}"
        )
        lines.append(f"n{edge['source_company_id']} -->|{label}| n{edge['target_company_id']}")
    return "\n".join(lines)


async def _summary(session: AsyncSession) -> dict:
    active = int(
        (await session.execute(select(func.count()).select_from(Company).where(Company.is_active.is_(True)))).scalar_one()
    )
    watched = int(
        (
            await session.execute(
                select(func.count()).select_from(Company).where(Company.is_active.is_(True), Company.universe_status == "WATCHED")
            )
        ).scalar_one()
    )
    discovered = int(
        (
            await session.execute(
                select(func.count()).select_from(Company).where(Company.is_active.is_(True), Company.universe_status == "DISCOVERED")
            )
        ).scalar_one()
    )
    candidates = int(
        (
            await session.execute(
                select(func.count())
                .select_from(DiscoveredCompany)
                .where(DiscoveredCompany.promoted_company_id.is_(None), DiscoveredCompany.status != "REJECTED")
            )
        ).scalar_one()
    )
    news = await intelligence_counts(session)
    admin = await build_admin_status(session)
    return {
        "active": active,
        "watched": watched,
        "discovered": discovered,
        "candidates": candidates,
        "documents_24h": news["documents_last_24h"],
        "events_24h": news["events_last_24h"],
        "jobs_failed": None if admin.jobs.failed is None else admin.jobs.failed,
        "last_market_sync": None if admin.market is None else admin.market.last_market_sync,
        "last_sec_sync": None if admin.sync is None else admin.sync.sec_last_success,
    }


async def _documents(session: AsyncSession, company_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(ExternalDocument, DocumentCompany.confidence, ExternalSource.name)
            .join(DocumentCompany, DocumentCompany.document_id == ExternalDocument.id)
            .join(ExternalSource, ExternalSource.id == ExternalDocument.source_id)
            .where(DocumentCompany.company_id == company_id)
            .order_by(ExternalDocument.published_at.desc(), ExternalDocument.id.desc())
            .limit(10)
        )
    ).all()
    return [
        {"document": document, "confidence": confidence, "source": source_name}
        for document, confidence, source_name in rows
    ]


async def _latest(session: AsyncSession, model, date_name: str, company_ids: list[int]) -> dict:
    if not company_ids:
        return {}
    column = getattr(model, date_name)
    rows = (
        await session.scalars(
            select(model).where(model.company_id.in_(company_ids)).order_by(column.desc(), model.id.desc())
        )
    ).all()
    found = {}
    for row in rows:
        found.setdefault(row.company_id, row)
    return found


async def _snapshots(session: AsyncSession, company_ids: list[int]) -> dict[int, CompanyMarketSnapshot]:
    if not company_ids:
        return {}
    rows = (
        await session.scalars(select(CompanyMarketSnapshot).where(CompanyMarketSnapshot.company_id.in_(company_ids)))
    ).all()
    return {row.company_id: row for row in rows}


def _candidate_depth(parent: Company | None) -> int | None:
    if parent is None:
        return None
    return int(parent.discovery_depth or 0) + 1


def _range_start(labels: list[str], chart_range: str) -> int:
    days = _RANGES.get(chart_range)
    if days is None or not labels:
        return 0
    last = date.fromisoformat(labels[-1])
    cutoff = last - timedelta(days=days)
    for index, label in enumerate(labels):
        if date.fromisoformat(label) >= cutoff:
            return index
    return 0


def _sma(values: list[float | None], window: int) -> list[float | None]:
    series: list[float | None] = []
    for index in range(len(values)):
        chunk = values[index + 1 - window : index + 1]
        if len(chunk) < window or any(item is None for item in chunk):
            series.append(None)
            continue
        series.append(round(sum(chunk) / window, 4))
    return series


def _safe_label(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in " .,&/-'’" else " " for character in value)
    return " ".join(cleaned.split())[:80] or "Company"
