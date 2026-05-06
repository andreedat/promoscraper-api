import asyncio
import logging
import re
import urllib.parse
from dataclasses import dataclass, field

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MERCADO_LIVRE_BASE_URL = "https://lista.mercadolivre.com.br"
MAX_RESULTS_PER_ITEM = 5
REQUEST_TIMEOUT_SECONDS = 15
MAX_CONCURRENT_REQUESTS = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

@dataclass
class ScrapedPromotion:
    title: str
    price: float | None
    link: str
    source: str
    search_term: str

@dataclass
class ScrapeItemResult:
    search_term: str
    promotions: list[ScrapedPromotion] = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

def _parse_price(price_text: str) -> float | None:
    if not price_text:
        return None
    
    cleaned = re.sub(r"[R$\s\xa0]", "", price_text)
    
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
        
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    
    try:
        return float(cleaned)
    except ValueError:
        logger.debug("Não foi possível converter preço: %r", price_text)
        return None

def _parse_mercado_livre_html(html: str, search_term: str) -> list[ScrapedPromotion]:
    soup = BeautifulSoup(html, "html.parser")
    promotions: list[ScrapedPromotion] = []

    items = soup.select("li.ui-search-layout__item")
    if not items:
        items = soup.select("div.ui-search-result__wrapper")

    for item in items[:MAX_RESULTS_PER_ITEM]:
        title_tag = item.select_one(
            "h2.poly-box.poly-component__title, "
            "h2.ui-search-item__title, "
            "a.poly-component__title"
        )
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        if not title:
            continue

        link_tag = item.select_one(
            "a.poly-component__title, "
            "a.ui-search-item__group__element"
        )
        link = link_tag.get("href", "") if link_tag else ""
        if not link:
            continue
            
        link = link.split("#")[0].split("?")[0]

        fraction_tag = item.select_one("span.andes-money-amount__fraction")
        cents_tag = item.select_one("span.andes-money-amount__cents")

        price: float | None = None
        if fraction_tag:
            fraction_text = fraction_tag.get_text(strip=True)
            cents_text = cents_tag.get_text(strip=True) if cents_tag else "00"
            price_str = f"{fraction_text},{cents_text}"
            price = _parse_price(price_str)

        promotions.append(
            ScrapedPromotion(
                title=title,
                price=price,
                link=link,
                source="Mercado Livre",
                search_term=search_term,
            )
        )

    return promotions

async def _fetch_mercado_livre(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    search_term: str,
) -> ScrapeItemResult:
    encoded_term = urllib.parse.quote(search_term)
    url = f"{MERCADO_LIVRE_BASE_URL}/{encoded_term}"

    async with semaphore:
        try:
            logger.info("Iniciando scraping para: %r → %s", search_term, url)
            async with session.get(url, headers=HEADERS, allow_redirects=True) as response:
                if response.status != 200:
                    error_msg = f"HTTP {response.status} ao buscar '{search_term}'"
                    logger.warning(error_msg)
                    return ScrapeItemResult(search_term=search_term, error=error_msg)

                html = await response.text(encoding="utf-8", errors="replace")

        except aiohttp.ClientConnectorError as exc:
            error_msg = f"Falha de conexão ao Mercado Livre: {exc}"
            logger.error(error_msg)
            return ScrapeItemResult(search_term=search_term, error=error_msg)

        except asyncio.TimeoutError:
            error_msg = f"Timeout ({REQUEST_TIMEOUT_SECONDS}s) ao buscar '{search_term}'"
            logger.error(error_msg)
            return ScrapeItemResult(search_term=search_term, error=error_msg)

        except aiohttp.ClientError as exc:
            error_msg = f"Erro HTTP inesperado: {exc}"
            logger.exception(error_msg)
            return ScrapeItemResult(search_term=search_term, error=error_msg)

    try:
        promotions = _parse_mercado_livre_html(html, search_term)
        logger.info(
            "Scraping de %r concluído: %d resultado(s) encontrado(s).",
            search_term,
            len(promotions),
        )
        return ScrapeItemResult(search_term=search_term, promotions=promotions)
    except Exception as exc:
        error_msg = f"Erro no parsing HTML para '{search_term}': {exc}"
        logger.exception(error_msg)
        return ScrapeItemResult(search_term=search_term, error=error_msg)

async def scrape_all_items(search_terms: list[str]) -> list[ScrapeItemResult]:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            _fetch_mercado_livre(session, semaphore, term)
            for term in search_terms
        ]

        results: list[ScrapeItemResult | BaseException] = await asyncio.gather(
            *tasks, return_exceptions=True
        )

    normalized: list[ScrapeItemResult] = []
    for term, result in zip(search_terms, results):
        if isinstance(result, BaseException):
            logger.error("Exceção não tratada para '%s': %s", term, result)
            normalized.append(
                ScrapeItemResult(
                    search_term=term,
                    error=f"Erro interno inesperado: {result}",
                )
            )
        else:
            normalized.append(result)

    return normalized
