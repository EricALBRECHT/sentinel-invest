"""One async database session per job, disposed before the worker runs the next job."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.urls import database_url


def run_async(factory):
    return asyncio.run(factory())


@asynccontextmanager
async def job_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url("postgresql+asyncpg"), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
