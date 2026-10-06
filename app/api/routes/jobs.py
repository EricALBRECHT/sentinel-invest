from datetime import date
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.jobs.maintenance import enqueue_selected
from app.jobs.market_sync import enqueue_selected_market
from app.jobs.queues import (
    enqueue_ai_document_analysis,
    enqueue_discovery_expansion,
    enqueue_discovery_verification_batch,
    enqueue_gpu_probe,
    enqueue_market_sync,
    enqueue_news_sync,
    enqueue_sec_sync,
    enqueue_supply_chain,
    enqueue_technical_backfill,
)
from app.models.intelligence import ExternalDocument
from app.models.company import Company
from app.schemas.jobs import DueSyncRead, JobDetailRead, JobEnqueueRead
from app.services.jobs.status import collect_job_counts, describe_job

router = APIRouter(
    prefix="/admin/jobs",
    tags=["jobs"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/sec-sync/{company_id}", response_model=JobEnqueueRead)
async def enqueue_company_sec_sync(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not company.sec_cik:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Company has no SEC CIK")
    queued = await asyncio.to_thread(enqueue_sec_sync, company_id)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/sec-sync-due", response_model=DueSyncRead)
async def enqueue_due_sec_sync(db: AsyncSession = Depends(get_db)) -> DueSyncRead:
    result = await enqueue_selected(db)
    return DueSyncRead(**result)


@router.post("/market-sync/{company_id}", response_model=JobEnqueueRead)
async def enqueue_company_market_sync(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not company.market_symbol:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Company has no market symbol")
    queued = await asyncio.to_thread(enqueue_market_sync, company_id)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/market-sync-due", response_model=DueSyncRead)
async def enqueue_due_market_sync(db: AsyncSession = Depends(get_db)) -> DueSyncRead:
    result = await enqueue_selected_market(db)
    return DueSyncRead(**result)


@router.post("/technical-backfill/{company_id}", response_model=JobEnqueueRead)
async def enqueue_company_technical_backfill(
    company_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    queued = await asyncio.to_thread(
        enqueue_technical_backfill,
        company_id,
        None if start_date is None else start_date.isoformat(),
        None if end_date is None else end_date.isoformat(),
    )
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/news-sync/{company_id}", response_model=JobEnqueueRead)
async def enqueue_company_news_sync(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    queued = await asyncio.to_thread(enqueue_news_sync, company_id, True)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/supply-chain-process/{company_id}", response_model=JobEnqueueRead)
async def enqueue_company_supply_chain(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    queued = await asyncio.to_thread(enqueue_supply_chain, company_id)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/discovery-expand/{company_id}", response_model=JobEnqueueRead)
async def enqueue_company_discovery_expansion(
    company_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not company.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company is inactive")
    queued = await asyncio.to_thread(enqueue_discovery_expansion, company_id)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/verify-discovered-candidates", response_model=JobEnqueueRead)
async def enqueue_discovery_verification() -> JSONResponse:
    queued = await asyncio.to_thread(enqueue_discovery_verification_batch)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/ai-document/{document_id}", response_model=JobEnqueueRead)
async def enqueue_document_ai_analysis(
    document_id: int,
    force: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    document = await db.get(ExternalDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if not (document.content_text or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Document has no analyzable text")
    if force:
        from app.services.ai.service import ensure_analysis_row

        await ensure_analysis_row(db, document_id, force=True)
        await db.commit()
    queued = await asyncio.to_thread(enqueue_ai_document_analysis, document_id, force)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.post("/gpu-probe", response_model=JobEnqueueRead)
async def enqueue_gpu_system_probe() -> JSONResponse:
    queued = await asyncio.to_thread(enqueue_gpu_probe)
    code = status.HTTP_202_ACCEPTED if queued["enqueued"] else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=JobEnqueueRead(**queued).model_dump())


@router.get("/status")
async def job_queue_status() -> dict:
    counted = await asyncio.to_thread(collect_job_counts)
    if counted is None:
        return {"queued": None, "started": None, "finished_recent": None, "failed": None}
    return counted


@router.get("/{job_id}", response_model=JobDetailRead)
async def job_detail(job_id: str) -> JobDetailRead:
    described = await asyncio.to_thread(describe_job, job_id)
    if described is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobDetailRead(**described)
