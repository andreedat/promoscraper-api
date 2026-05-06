"""
main.py — Ponto de entrada da aplicação FastAPI.

Define o ciclo de vida da app (lifespan), registra as rotas e configura
middlewares. Mantém as rotas finas (thin controllers): a lógica de negócio
vive em scraper.py e a lógica de persistência em funções dedicadas aqui.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, init_db
from app.models import Promotion
from app.schemas import (
    HealthResponse,
    PromotionCreate,
    PromotionRead,
    ScrapeRequest,
    ScrapeResponse,
    ScrapeResult,
)
from app.scraper import ScrapeItemResult, scrape_all_items

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan (substitui deprecated on_event)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Gerencia o ciclo de vida da aplicação:
    - Startup: inicializa o banco de dados (cria tabelas se necessário)
    - Shutdown: recursos são liberados automaticamente pelo engine
    """
    logger.info("🚀 PromoScraper API iniciando...")
    await init_db()
    logger.info("✅ Banco de dados inicializado com sucesso.")
    yield
    logger.info("🛑 PromoScraper API encerrando.")


# ---------------------------------------------------------------------------
# Instância da aplicação
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PromoScraper API",
    description=(
        "API de scraping assíncrono de promoções em e-commerces. "
        "Busca múltiplos produtos de forma concorrente via asyncio + aiohttp "
        "e persiste os resultados em PostgreSQL."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produção, restrinja para domínios específicos
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Funções auxiliares de persistência
# ---------------------------------------------------------------------------


async def _save_promotions(
    db: AsyncSession,
    item_result: ScrapeItemResult,
) -> list[Promotion]:
    """
    Persiste os resultados de um único item de scraping no banco.
    Retorna os objetos ORM criados com seus IDs preenchidos.
    """
    saved: list[Promotion] = []
    for promo in item_result.promotions:
        db_promo = Promotion(
            title=promo.title,
            price=promo.price,
            link=promo.link,
            source=promo.source,
            search_term=promo.search_term,
        )
        db.add(db_promo)
        saved.append(db_promo)

    # Flush envia os INSERTs ao banco e popula os IDs sem commitar a transação
    await db.flush()
    return saved


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health Check",
    tags=["Infra"],
)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """
    Verifica se a API e a conexão com o banco de dados estão operacionais.
    Ideal para liveness/readiness probes em ambientes Kubernetes.
    """
    try:
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as exc:
        logger.error("Database health check falhou: %s", exc)
        db_status = "unhealthy"

    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        database=db_status,
        version=app.version,
    )


@app.post(
    "/scrape/",
    response_model=ScrapeResponse,
    status_code=status.HTTP_200_OK,
    summary="Scraping Assíncrono de Promoções",
    tags=["Scraping"],
)
async def scrape_promotions(
    payload: ScrapeRequest,
    db: AsyncSession = Depends(get_db),
) -> ScrapeResponse:
    """
    Recebe uma lista de produtos, busca promoções de forma **concorrente**
    no Mercado Livre e persiste os resultados no banco de dados.

    ### Fluxo interno:
    1. `scrape_all_items` dispara todas as buscas em paralelo via `asyncio.gather`
    2. Para cada item com sucesso, os resultados são salvos no PostgreSQL
    3. A resposta agrega todos os resultados, incluindo erros parciais

    ### Comportamento em caso de falha:
    - Falhas em itens individuais **não** cancelam os demais
    - O campo `results[].status` indica `'success'` ou `'error'` por item
    - Um erro total de infraestrutura retorna HTTP 503
    """
    logger.info("POST /scrape/ recebido com %d item(s): %s", len(payload.items), payload.items)

    # 1. Executa scraping concorrente
    try:
        scrape_results = await scrape_all_items(payload.items)
    except Exception as exc:
        logger.exception("Falha crítica no motor de scraping: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serviço de scraping temporariamente indisponível: {exc}",
        ) from exc

    # 2. Persiste resultados e monta resposta
    response_results: list[ScrapeResult] = []
    total_saved = 0

    for item_result in scrape_results:
        if not item_result.success:
            response_results.append(
                ScrapeResult(
                    search_term=item_result.search_term,
                    status="error",
                    error=item_result.error,
                )
            )
            continue

        if not item_result.promotions:
            response_results.append(
                ScrapeResult(
                    search_term=item_result.search_term,
                    status="success",
                    promotions_found=0,
                )
            )
            continue

        try:
            saved_promos = await _save_promotions(db, item_result)
            total_saved += len(saved_promos)

            response_results.append(
                ScrapeResult(
                    search_term=item_result.search_term,
                    status="success",
                    promotions_found=len(saved_promos),
                    promotions=[PromotionRead.model_validate(p) for p in saved_promos],
                )
            )
        except Exception as exc:
            logger.error(
                "Falha ao persistir resultados de '%s': %s",
                item_result.search_term,
                exc,
            )
            response_results.append(
                ScrapeResult(
                    search_term=item_result.search_term,
                    status="error",
                    error=f"Erro ao salvar no banco: {exc}",
                )
            )

    logger.info(
        "POST /scrape/ concluído: %d promoção(ões) salva(s) de %d item(s).",
        total_saved,
        len(payload.items),
    )

    return ScrapeResponse(
        total_items_requested=len(payload.items),
        total_promotions_saved=total_saved,
        results=response_results,
    )


@app.get(
    "/promotions/",
    response_model=list[PromotionRead],
    summary="Listar Promoções Salvas",
    tags=["Promoções"],
)
async def list_promotions(
    search_term: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[PromotionRead]:
    """
    Lista as promoções já salvas no banco com filtros opcionais.

    - **search_term**: filtra por termo de busca (correspondência exata)
    - **source**: filtra por e-commerce de origem
    - **limit**: máximo de registros retornados (padrão: 50)
    - **offset**: deslocamento para paginação
    """
    from sqlalchemy import select

    query = select(Promotion).order_by(Promotion.scraped_at.desc())

    if search_term:
        query = query.where(Promotion.search_term == search_term)
    if source:
        query = query.where(Promotion.source == source)

    query = query.limit(min(limit, 200)).offset(offset)

    result = await db.execute(query)
    promotions = result.scalars().all()
    return [PromotionRead.model_validate(p) for p in promotions]
