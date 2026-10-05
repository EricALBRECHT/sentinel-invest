from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.urls import database_url

engine = create_async_engine(database_url("postgresql+asyncpg"), pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
