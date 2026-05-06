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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("🚀 PromoScraper API iniciando...")
    await init_db()
    logger.info("✅ Banco de dados inicializado com sucesso.")
    yield
    logger.info("🛑 PromoScraper API encerrando.")

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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async def _save_promotions(
    db: AsyncSession,
    item_result: ScrapeItemResult,
) -> list[Promotion]:
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

    await db.flush()
    return saved

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health Check",
    tags=["Infra"],
)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
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
    logger.info("POST /scrape/ recebido com %d item(s): %s", len(payload.items), payload.items)

    try:
        scrape_results = await scrape_all_items(payload.items)
    except Exception as exc:
        logger.exception("Falha crítica no motor de scraping: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serviço de scraping temporariamente indisponível: {exc}",
        ) from exc

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
