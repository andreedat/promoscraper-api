import asyncio
import logging
import urllib.parse
from dataclasses import dataclass, field

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_RESULTS_PER_STORE = 5
REQUEST_TIMEOUT_SECONDS = 15
MAX_CONCURRENT_REQUESTS = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
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
        return self.error is None or len(self.promotions) > 0

def _parse_books_toscrape(html: str, search_term: str) -> list[ScrapedPromotion]:
    soup = BeautifulSoup(html, "html.parser")
    promotions = []
    
    items = soup.select("article.product_pod")
    
    term_lower = search_term.lower()
    
    for item in items:
        if len(promotions) >= MAX_RESULTS_PER_STORE:
            break
            
        title_tag = item.select_one("h3 a")
        if not title_tag:
            continue
            
        title = title_tag.get("title", "")
        if term_lower not in title.lower():
            continue
            
        link = title_tag.get("href", "")
        if link and not link.startswith("http"):
            link = f"https://books.toscrape.com/{link}"
            
        price_tag = item.select_one("p.price_color")
        price = None
        if price_tag:
            price_text = price_tag.get_text(strip=True).replace("£", "").replace("Â", "")
            try:
                price = float(price_text)
            except ValueError:
                pass
                
        promotions.append(ScrapedPromotion(title, price, link, "Books To Scrape", search_term))
        
    return promotions

STORES = [
    {
        "name": "BooksToScrape",
        "url_template": "https://books.toscrape.com/index.html", 
        "parser": _parse_books_toscrape
    }
]

async def _fetch_store(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    search_term: str,
    store_config: dict
) -> list[ScrapedPromotion] | dict:
    
    url = store_config["url_template"]
    store_name = store_config["name"]

    async with semaphore:
        try:
            logger.info("Scraping [%s] para: %r", store_name, search_term)
            async with session.get(url, headers=HEADERS, allow_redirects=True) as response:
                if response.status != 200:
                    return {"term": search_term, "error": f"{store_name}: HTTP {response.status}"}
                
                html = await response.text(encoding="utf-8", errors="replace")
                
        except Exception as exc:
            return {"term": search_term, "error": f"{store_name}: {exc}"}

    try:
        promotions = store_config["parser"](html, search_term)
        return promotions
    except Exception as exc:
        return {"term": search_term, "error": f"{store_name} Parser Error: {exc}"}

async def scrape_all_items(search_terms: list[str]) -> list[ScrapeItemResult]:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    tasks = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for term in search_terms:
            for store in STORES:
                tasks.append(_fetch_store(session, semaphore, term, store))

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    grouped_results = {term: ScrapeItemResult(search_term=term) for term in search_terms}

    for result in raw_results:
        if isinstance(result, BaseException):
            continue
            
        if isinstance(result, list):
            for promo in result:
                grouped_results[promo.search_term].promotions.append(promo)
                
        elif isinstance(result, dict) and "error" in result:
            term = result["term"]
            error_msg = result["error"]
            if grouped_results[term].error:
                grouped_results[term].error += f" | {error_msg}"
            else:
                grouped_results[term].error = error_msg

    return list(grouped_results.values())
