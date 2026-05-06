import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models import Promotion
from app.scraper import (
    ScrapeItemResult,
    ScrapedPromotion,
    _parse_price,
    _parse_mercado_livre_html,
    scrape_all_items,
)

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()


class TestParsePrice:
    def test_parse_integer_price(self):
        assert _parse_price("1299") == 1299.0

    def test_parse_br_format_with_comma(self):
        assert _parse_price("49,90") == 49.90

    def test_parse_br_format_with_thousands(self):
        assert _parse_price("1.299,90") == 1299.90

    def test_parse_with_currency_symbol(self):
        result = _parse_price("R$\xa01.299")
        assert result == 1299.0

    def test_parse_empty_string_returns_none(self):
        assert _parse_price("") is None

    def test_parse_non_numeric_returns_none(self):
        assert _parse_price("Sob consulta") is None

    def test_parse_zero(self):
        assert _parse_price("0") == 0.0


MOCK_ML_HTML = """
<html><body>
<ul>
  <li class="ui-search-layout__item">
    <a class="poly-component__title" href="https://www.mercadolivre.com.br/produto/1">
      <h2 class="poly-box poly-component__title">Memória RAM 16GB DDR4</h2>
    </a>
    <span class="andes-money-amount__fraction">299</span>
    <span class="andes-money-amount__cents">90</span>
  </li>
  <li class="ui-search-layout__item">
    <a class="poly-component__title" href="https://www.mercadolivre.com.br/produto/2">
      <h2 class="poly-box poly-component__title">Memória RAM 32GB DDR5</h2>
    </a>
    <span class="andes-money-amount__fraction">799</span>
    <span class="andes-money-amount__cents">00</span>
  </li>
  <li class="ui-search-layout__item">
    <a class="poly-component__title" href="https://www.mercadolivre.com.br/produto/3">
      <h2 class="poly-box poly-component__title">Memória RAM 8GB DDR4</h2>
    </a>
    <span class="andes-money-amount__fraction">149</span>
    <span class="andes-money-amount__cents">99</span>
  </li>
</ul>
</body></html>
"""

MOCK_ML_HTML_EMPTY = "<html><body><div>Nenhum resultado encontrado</div></body></html>"


class TestParseMercadoLivreHtml:
    def test_parses_multiple_items(self):
        results = _parse_mercado_livre_html(MOCK_ML_HTML, "memória ram")
        assert len(results) == 3

    def test_parses_title_correctly(self):
        results = _parse_mercado_livre_html(MOCK_ML_HTML, "memória ram")
        assert results[0].title == "Memória RAM 16GB DDR4"

    def test_parses_price_correctly(self):
        results = _parse_mercado_livre_html(MOCK_ML_HTML, "memória ram")
        assert results[0].price == pytest.approx(299.90)

    def test_parses_link_correctly(self):
        results = _parse_mercado_livre_html(MOCK_ML_HTML, "memória ram")
        assert "mercadolivre.com.br/produto/1" in results[0].link

    def test_sets_source_as_mercado_livre(self):
        results = _parse_mercado_livre_html(MOCK_ML_HTML, "memória ram")
        assert all(r.source == "Mercado Livre" for r in results)

    def test_sets_search_term(self):
        results = _parse_mercado_livre_html(MOCK_ML_HTML, "memória ram")
        assert all(r.search_term == "memória ram" for r in results)

    def test_empty_html_returns_empty_list(self):
        results = _parse_mercado_livre_html(MOCK_ML_HTML_EMPTY, "memória ram")
        assert results == []

    def test_respects_max_results_limit(self):

        items_html = ""
        for i in range(10):
            items_html += f"""
            <li class="ui-search-layout__item">
              <a class="poly-component__title" href="https://ml.com/{i}">
                <h2 class="poly-box poly-component__title">Produto {i}</h2>
              </a>
              <span class="andes-money-amount__fraction">{100 + i}</span>
            </li>"""
        html = f"<html><body><ul>{items_html}</ul></body></html>"
        results = _parse_mercado_livre_html(html, "teste")
        assert len(results) == 5  # MAX_RESULTS_PER_ITEM


class TestScrapeAllItems:
    @pytest.mark.asyncio
    async def test_returns_result_for_each_term(self):
        """Verifica que o gather retorna um resultado por termo."""
        mock_result = ScrapeItemResult(
            search_term="memória ram",
            promotions=[
                ScrapedPromotion(
                    title="RAM 16GB",
                    price=299.90,
                    link="https://ml.com/1",
                    source="Mercado Livre",
                    search_term="memória ram",
                )
            ],
        )

        with patch("app.scraper._fetch_mercado_livre", return_value=mock_result):
            results = await scrape_all_items(["memória ram", "monitor gamer"])
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_handles_partial_failure_gracefully(self):
        success_result = ScrapeItemResult(
            search_term="monitor gamer",
            promotions=[
                ScrapedPromotion(
                    title="Monitor 144Hz",
                    price=1299.0,
                    link="https://ml.com/2",
                    source="Mercado Livre",
                    search_term="monitor gamer",
                )
            ],
        )
        error_result = ScrapeItemResult(
            search_term="produto inexistente",
            error="HTTP 404",
        )

        async def mock_fetch(session, semaphore, term):
            if term == "monitor gamer":
                return success_result
            return error_result

        with patch("app.scraper._fetch_mercado_livre", side_effect=mock_fetch):
            results = await scrape_all_items(["monitor gamer", "produto inexistente"])

        assert results[0].success is True
        assert results[1].success is False
        assert results[1].error == "HTTP 404"

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, async_client: AsyncClient):
        response = await async_client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_response_structure(self, async_client: AsyncClient):
        response = await async_client.get("/health")
        data = response.json()
        assert "status" in data
        assert "database" in data
        assert "version" in data

    @pytest.mark.asyncio
    async def test_health_database_is_healthy(self, async_client: AsyncClient):
        response = await async_client.get("/health")
        assert response.json()["database"] == "healthy"


class TestScrapeEndpoint:
    @pytest.mark.asyncio
    async def test_scrape_returns_200_on_success(self, async_client: AsyncClient):
        mock_results = [
            ScrapeItemResult(
                search_term="memória ram",
                promotions=[
                    ScrapedPromotion(
                        title="RAM 16GB",
                        price=299.90,
                        link="https://ml.com/1",
                        source="Mercado Livre",
                        search_term="memória ram",
                    )
                ],
            )
        ]
        with patch("app.main.scrape_all_items", return_value=mock_results):
            response = await async_client.post(
                "/scrape/", json={"items": ["memória ram"]}
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_scrape_response_contains_saved_promotions(self, async_client: AsyncClient):
        mock_results = [
            ScrapeItemResult(
                search_term="teclado mecânico",
                promotions=[
                    ScrapedPromotion(
                        title="Teclado HyperX",
                        price=450.0,
                        link="https://ml.com/3",
                        source="Mercado Livre",
                        search_term="teclado mecânico",
                    )
                ],
            )
        ]
        with patch("app.main.scrape_all_items", return_value=mock_results):
            response = await async_client.post(
                "/scrape/", json={"items": ["teclado mecânico"]}
            )
        data = response.json()
        assert data["total_items_requested"] == 1
        assert data["total_promotions_saved"] == 1
        assert data["results"][0]["status"] == "success"
        assert data["results"][0]["promotions"][0]["title"] == "Teclado HyperX"

    @pytest.mark.asyncio
    async def test_scrape_with_empty_items_returns_422(self, async_client: AsyncClient):
        response = await async_client.post("/scrape/", json={"items": []})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_scrape_with_missing_items_returns_422(self, async_client: AsyncClient):
        response = await async_client.post("/scrape/", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_scrape_handles_partial_error_in_results(self, async_client: AsyncClient):
            ScrapeItemResult(
                search_term="produto ok",
                promotions=[
                    ScrapedPromotion(
                        title="Produto OK",
                        price=100.0,
                        link="https://ml.com/ok",
                        source="Mercado Livre",
                        search_term="produto ok",
                    )
                ],
            ),
            ScrapeItemResult(
                search_term="produto com erro",
                error="Timeout na requisição",
            ),
        ]
        with patch("app.main.scrape_all_items", return_value=mock_results):
            response = await async_client.post(
                "/scrape/", json={"items": ["produto ok", "produto com erro"]}
            )
        assert response.status_code == 200
        data = response.json()
        statuses = {r["search_term"]: r["status"] for r in data["results"]}
        assert statuses["produto ok"] == "success"
        assert statuses["produto com erro"] == "error"

    @pytest.mark.asyncio
    async def test_scrape_returns_503_on_critical_failure(self, async_client: AsyncClient):
        """Simula falha catastrófica no motor de scraping → deve retornar 503."""
        with patch(
            "app.main.scrape_all_items",
            side_effect=RuntimeError("Conexão recusada"),
        ):
            response = await async_client.post(
                "/scrape/", json={"items": ["qualquer coisa"]}
            )
        assert response.status_code == 503


class TestListPromotionsEndpoint:
    @pytest.mark.asyncio
    async def test_list_promotions_returns_200(self, async_client: AsyncClient):
        response = await async_client.get("/promotions/")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_promotions_returns_list(self, async_client: AsyncClient):
        response = await async_client.get("/promotions/")
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_list_promotions_data_persisted_after_scrape(
        self, async_client: AsyncClient
    ):
        mock_results = [
            ScrapeItemResult(
                search_term="headset gamer",
                promotions=[
                    ScrapedPromotion(
                        title="Headset JBL",
                        price=350.0,
                        link="https://ml.com/headset",
                        source="Mercado Livre",
                        search_term="headset gamer",
                    )
                ],
            )
        ]
        with patch("app.main.scrape_all_items", return_value=mock_results):
            await async_client.post("/scrape/", json={"items": ["headset gamer"]})

        response = await async_client.get("/promotions/?search_term=headset+gamer")
        data = response.json()
        assert len(data) >= 1
        assert data[0]["title"] == "Headset JBL"
        assert data[0]["price"] == pytest.approx(350.0)
