"""
database.py — Configuração da conexão assíncrona com o PostgreSQL via SQLAlchemy.

Utiliza o driver asyncpg para operações não-bloqueantes no event loop do asyncio,
garantindo que queries ao banco não travem o processamento de outras requisições.
"""

import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

# Lê a DATABASE_URL do ambiente e converte o scheme para o driver asyncpg.
# Ex: postgresql://user:pass@host/db → postgresql+asyncpg://user:pass@host/db
_raw_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/promoscraper")
DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,          # True em dev para logar SQL; False em prod
    pool_size=10,        # Conexões mantidas abertas no pool
    max_overflow=20,     # Conexões extras permitidas sob carga
    pool_pre_ping=True,  # Verifica conexão antes de usar (evita stale connections)
)

# Fábrica de sessões assíncronas — usada via dependency injection no FastAPI
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Objetos permanecem acessíveis após commit
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Classe base declarativa para todos os modelos ORM do projeto."""
    pass


async def get_db() -> AsyncSession:  # type: ignore[return]
    """
    Dependency do FastAPI que fornece uma sessão de banco de dados por request.
    Garante que a sessão seja fechada corretamente ao final de cada operação,
    mesmo em caso de exceção.
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
    """
    Cria todas as tabelas definidas nos modelos ORM caso ainda não existam.
    Chamada uma única vez no evento de startup da aplicação FastAPI.
    """
    async with engine.begin() as conn:
        # Import aqui para garantir que os modelos já foram registrados no Base
        from app.models import Promotion  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
