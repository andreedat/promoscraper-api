"""
scraper.py — Motor de scraping assíncrono baseado em aiohttp + BeautifulSoup4.

DECISÃO ARQUITETURAL — Por que asyncio + aiohttp?
─────────────────────────────────────────────────
Scraping é uma operação I/O Bound: a maior parte do tempo é gasta
ESPERANDO respostas HTTP, não processando CPU. Com `requests` (síncrono),
as buscas seriam executadas em série: item1 → aguarda → item2 → aguarda...

Com `aiohttp` + `asyncio.gather`, todas as requisições HTTP são disparadas
de forma concorrente dentro do mesmo thread, aproveitando os momentos de
espera de I/O para processar outras tarefas. Para N=10 itens com latência
média de 2s, o ganho é de ~10x: 20s síncronos → ~2s assíncronos.

Limites e boas práticas implementados:
- Timeout global por requisição (evita travar em servidores lentos)
- Semáforo de concorrência (evita sobrecarga no alvo e rate-limiting)
- User-Agent realista (reduz chance de bloqueio por bot detection)
- Tratamento granular de exceções (falha em 1 item não derruba os demais)
"""

import asyncio
import logging
import re
import urllib.parse
from dataclasses import dataclass, field

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de configuração
# ---------------------------------------------------------------------------

MERCADO_LIVRE_BASE_URL = "https://lista.mercadolivre.com.br"
MAX_RESULTS_PER_ITEM = 5
REQUEST_TIMEOUT_SECONDS = 15
MAX_CONCURRENT_REQUESTS = 5  # Semáforo: máx. de requisições HTTP simultâneas

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Data Transfer Objects internos
# ---------------------------------------------------------------------------


@dataclass
class ScrapedPromotion:
    """DTO que carrega um resultado bruto antes de ser persistido."""

    title: str
    price: float | None
    link: str
    source: str
    search_term: str


@dataclass
class ScrapeItemResult:
    """Resultado (sucesso ou falha) do scraping de um único termo."""

    search_term: str
    promotions: list[ScrapedPromotion] = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Funções auxiliares de parsing
# ---------------------------------------------------------------------------


def _parse_price(price_text: str) -> float | None:
    """
    Converte string de preço do Mercado Livre para float.
    Exemplos de entrada: "R$\xa01.299", "1.299", "R$ 49,90"
    """
    if not price_text:
        return None
    # Remove símbolos de moeda, espaços e quebras não-separáveis
    cleaned = re.sub(r"[R$\s\xa0]", "", price_text)
    # Normaliza separador: "1.299,90" → "1299.90"
    # Detecta formato BR (vírgula decimal) vs formato com ponto como milhar
    if "," in cleaned and "." in cleaned:
        # Ex: "1.299,90" → remove ponto de milhar, troca vírgula por ponto
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        # Ex: "49,90" → troca vírgula por ponto
        cleaned = cleaned.replace(",", ".")
    # Remove qualquer caractere restante que não seja dígito ou ponto
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        logger.debug("Não foi possível converter preço: %r", price_text)
        return None


def _parse_mercado_livre_html(html: str, search_term: str) -> list[ScrapedPromotion]:
    """
    Extrai os primeiros MAX_RESULTS_PER_ITEM resultados da página de busca
    do Mercado Livre.

    Estrutura HTML alvo (maio/2024):
    - Container: <li class="ui-search-layout__item">
    - Título:     <h2 class="poly-box poly-component__title">
    - Preço int:  <span class="andes-money-amount__fraction">
    - Preço dec:  <span class="andes-money-amount__cents">
    - Link:       <a class="poly-component__title"> href
    """
    soup = BeautifulSoup(html, "html.parser")
    promotions: list[ScrapedPromotion] = []

    # Seleciona os itens de resultado
    items = soup.select("li.ui-search-layout__item")
    if not items:
        # Fallback: tenta seletor alternativo para variações do layout
        items = soup.select("div.ui-search-result__wrapper")

    for item in items[:MAX_RESULTS_PER_ITEM]:
        # --- Extrai título ---
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

        # --- Extrai link ---
        link_tag = item.select_one(
            "a.poly-component__title, "
            "a.ui-search-item__group__element"
        )
        link = link_tag.get("href", "") if link_tag else ""
        if not link:
            continue
        # Remove parâmetros de tracking para obter URL limpa
        link = link.split("#")[0].split("?")[0]

        # --- Extrai preço ---
        fraction_tag = item.select_one("span.andes-money-amount__fraction")
        cents_tag = item.select_one("span.andes-money-amount__cents")

        price: float | None = None
        if fraction_tag:
            fraction_text = fraction_tag.get_text(strip=True)
            cents_text = cents_tag.get_text(strip=True) if cents_tag else "00"
            # Monta a string de preço no formato "1299,90"
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


# ---------------------------------------------------------------------------
# Funções de scraping assíncronas
# ---------------------------------------------------------------------------


async def _fetch_mercado_livre(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    search_term: str,
) -> ScrapeItemResult:
    """
    Realiza a requisição HTTP ao Mercado Livre para um único termo
    e faz o parse do HTML retornado.

    O semáforo limita o número de corrotinas que podem executar
    simultaneamente esta função, evitando flood de requisições.
    """
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

    # Parse fora do `async with session.get` — libera a conexão antes de processar
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
    """
    Ponto de entrada principal do motor de scraping.

    Cria UMA única sessão aiohttp (reutiliza conexões TCP via keep-alive)
    e dispara TODAS as buscas em paralelo com asyncio.gather.

    asyncio.gather:
    - Recebe N corrotinas e as agenda no event loop
    - Retorna quando TODAS completam (ou falham)
    - return_exceptions=True: falhas individuais viram valores no resultado,
      não propagam exceção e não cancelam as demais tarefas
    """
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            _fetch_mercado_livre(session, semaphore, term)
            for term in search_terms
        ]

        # O coração da concorrência: todas as buscas rodam "ao mesmo tempo"
        results: list[ScrapeItemResult | BaseException] = await asyncio.gather(
            *tasks, return_exceptions=True
        )

    # Normaliza: converte exceções inesperadas em ScrapeItemResult com erro
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
