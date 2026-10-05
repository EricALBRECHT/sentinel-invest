from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.schemas.admin import AdminStatusRead
from app.services.admin.status import build_admin_status

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/status", response_model=AdminStatusRead)
async def get_admin_status(db: AsyncSession = Depends(get_db)) -> AdminStatusRead:
    return await build_admin_status(db)
