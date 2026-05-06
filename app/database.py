import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

_raw_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/promoscraper")
DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,         
    pool_size=10,       
    max_overflow=20,   
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Classe base declarativa para todos os modelos ORM do projeto."""
    pass


async def get_db() -> AsyncSession:
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
    async with engine.begin() as conn:
    
        from app.models import Promotion
        await conn.run_sync(Base.metadata.create_all)
