import logging

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
import app.runtime  # noqa: F401
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.companies import router as companies_router
from app.api.routes.financials import router as financials_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.market import router as market_router
from app.api.routes.opportunity import router as opportunity_router
from app.api.routes.scores import router as scores_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.supply_chain import router as supply_chain_router
from app.api.routes.investment_view import router as investment_view_router
from app.api.routes.technical import router as technical_router
from app.api.routes.universe import company_router as company_universe_router
from app.api.routes.universe import import_router as universe_import_router
from app.api.routes.universe import router as universe_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal


def _configure_logging() -> None:
    logger = logging.getLogger("sentinel")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


_configure_logging()

app = FastAPI(title="Sentinel API")
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(jobs_router)
app.include_router(universe_import_router)
app.include_router(universe_router)
app.include_router(companies_router)
app.include_router(company_universe_router)
app.include_router(market_router)
app.include_router(financials_router)
app.include_router(scores_router)
app.include_router(opportunity_router)
app.include_router(technical_router)
app.include_router(investment_view_router)
app.include_router(intelligence_router)
app.include_router(supply_chain_router)

@app.get("/health")
async def health():
    postgres_ok = False
    redis_ok = False

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        postgres_ok = True
    except Exception:
        postgres_ok = False

    redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )

    try:
        redis_ok = bool(await redis.ping())
    except Exception:
        redis_ok = False
    finally:
        await redis.aclose()

    status = "ok" if postgres_ok and redis_ok else "degraded"

    return {
        "status": status,
        "postgres": postgres_ok,
        "redis": redis_ok,
    }
