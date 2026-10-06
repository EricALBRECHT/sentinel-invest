from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.schemas.admin import AdminStatusRead
from app.schemas.gpu import GpuWorkerRead
from app.services.admin.status import build_admin_status
from app.services.ai.schemas import AiAdminStatusRead
from app.services.ai.service import admin_ai_status
from app.services.gpu.workers import get_worker_record, list_worker_records

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/status", response_model=AdminStatusRead)
async def get_admin_status(db: AsyncSession = Depends(get_db)) -> AdminStatusRead:
    return await build_admin_status(db)


@router.get("/ai/status", response_model=AiAdminStatusRead)
async def get_ai_status(db: AsyncSession = Depends(get_db)) -> AiAdminStatusRead:
    return AiAdminStatusRead(**await admin_ai_status(db))


@router.get("/gpu-workers", response_model=list[GpuWorkerRead])
def get_gpu_workers() -> list[GpuWorkerRead]:
    return [GpuWorkerRead(**row) for row in list_worker_records()]


@router.get("/gpu-workers/{name}", response_model=GpuWorkerRead)
def get_gpu_worker(name: str) -> GpuWorkerRead:
    try:
        row = get_worker_record(name)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GPU worker not found") from None
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GPU worker not found")
    return GpuWorkerRead(**row)
