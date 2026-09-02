"""SQLAlchemy async engine, session, and Base setup."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.environment.value == "development",
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injector for FastAPI – yields an async DB session.

    Do NOT wrap this in tenacity's @retry: it replaces the async-generator
    function with a plain sync wrapper, so FastAPI stops recognising it as a
    yield-dependency and injects the raw async_generator object instead of a
    session. Every DB-backed endpoint then fails with
    "'async_generator' object has no attribute 'execute'".

    Connection-level resilience belongs on the engine (pool_pre_ping above),
    not on the dependency.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables (use Alembic in production instead)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
