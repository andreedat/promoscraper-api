# 🛒 PromoScraper API

> **Motor de scraping assíncrono de alta performance** para busca concorrente de promoções em e-commerces, construído com FastAPI, asyncio e PostgreSQL.

```
POST /scrape/ {"items": ["memória ram", "monitor gamer", "teclado mecânico"]}
         │
         ▼
┌─────────────────────────────────────────────────────┐
│              asyncio.gather (concorrente)            │
│  ┌──────────────┐ ┌────────────────┐ ┌───────────┐  │
│  │ memória ram  │ │ monitor gamer  │ │ teclado.. │  │
│  │ aiohttp req  │ │ aiohttp req    │ │ aiohttp.. │  │
│  └──────┬───────┘ └───────┬────────┘ └─────┬─────┘  │
│         └─────────────────┴────────────────┘         │
│                    ▼ ~2s (vs ~6s síncronos)          │
└─────────────────────────────────────────────────────┘
         │
         ▼
  PostgreSQL (SQLAlchemy async) → Response JSON
```

---

## 📋 Sumário

- [Stack Tecnológico](#-stack-tecnológico)
- [Arquitetura do Projeto](#-arquitetura-do-projeto)
- [Decisões Arquiteturais](#-decisões-arquiteturais)
- [Setup com Docker (Recomendado)](#-setup-com-docker-recomendado)
- [Setup Local (Desenvolvimento)](#-setup-local-desenvolvimento)
- [Endpoints da API](#-endpoints-da-api)
- [Exemplos de Uso](#-exemplos-de-uso)
- [Rodando os Testes](#-rodando-os-testes)
- [Estrutura do Projeto](#-estrutura-do-projeto)

---

## 🛠️ Stack Tecnológico

| Camada | Tecnologia | Versão | Função |
|--------|-----------|--------|--------|
| **Web Framework** | FastAPI | 0.111 | Rotas, DI, validação automática, OpenAPI |
| **Validação** | Pydantic v2 | 2.7 | Schemas de request/response com type hints |
| **HTTP Assíncrono** | aiohttp | 3.9 | Requisições HTTP não-bloqueantes |
| **HTML Parsing** | BeautifulSoup4 + lxml | 4.12 | Extração de dados do HTML |
| **ORM** | SQLAlchemy (async) | 2.0 | Mapeamento objeto-relacional com asyncpg |
| **Banco de Dados** | PostgreSQL | 15 | Persistência dos dados de promoções |
| **Containerização** | Docker + Compose | - | Orquestração de serviços |
| **Testes** | Pytest + pytest-asyncio | 8.2 | Suite de testes unitários e de integração |

---

## 🏗️ Arquitetura do Projeto

```
promoscraper/
├── app/
│   ├── __init__.py
│   ├── main.py         # 🚪 Rotas FastAPI + lógica de persistência
│   ├── scraper.py      # 🕷️ Motor de scraping async (aiohttp + BS4)
│   ├── models.py       # 🗄️ Modelos ORM (SQLAlchemy)
│   ├── schemas.py      # 📐 Contratos Pydantic (request/response)
│   └── database.py     # 🔌 Engine async + sessões + init_db
├── tests/
│   └── test_promoscraper.py  # ✅ Unit + Integration tests
├── Dockerfile          # 🐳 Build multi-stage (builder + runtime)
├── docker-compose.yml  # 🎻 Orquestração PostgreSQL + API
├── requirements.txt
├── pytest.ini
├── .env.example
└── README.md
```

### Separação de Responsabilidades

```
Request HTTP
    │
    ▼
main.py (Controller)
 ├── Valida payload via Pydantic (schemas.py)
 ├── Chama scraper.py → retorna DTOs
 ├── Persiste via SQLAlchemy (models.py)
 └── Serializa resposta via Pydantic
         │
         ▼
    scraper.py (Engine)
     ├── asyncio.gather → N tarefas paralelas
     ├── aiohttp.ClientSession → requisições HTTP
     └── BeautifulSoup4 → parsing HTML

    database.py (Infraestrutura)
     ├── create_async_engine (asyncpg)
     ├── async_sessionmaker
     └── init_db() → cria tabelas no startup
```

---

## 🧠 Decisões Arquiteturais

### 1. Por que `aiohttp` + `asyncio` em vez de `requests`?

O scraping é um problema **I/O Bound**: 95% do tempo de execução é gasto esperando respostas HTTP de servidores externos. O código da CPU (parsing, validação) representa uma fração mínima.

**Com `requests` (bloqueante/síncrono):**
```
Thread Principal:
  buscar "memória ram"  → [====ESPERA 2s====] → parse → salvar
  buscar "monitor gamer" → [====ESPERA 2s====] → parse → salvar
  buscar "teclado"       → [====ESPERA 2s====] → parse → salvar
                                                         Total: ~6s
```

**Com `aiohttp` + `asyncio.gather` (não-bloqueante):**
```
Event Loop:
  buscar "memória ram"   → [dispara req] ───────────────┐
  buscar "monitor gamer" → [dispara req] ─────────────┐ │
  buscar "teclado"       → [dispara req] ───────────┐ │ │
                                                     ▼ ▼ ▼
                           [======ESPERA CONCORRENTE ~2s======]
                                                     │ │ │
                                              parse ◄┘ │ │
                                              parse ◄──┘ │
                                              parse ◄────┘
                                                         Total: ~2s
```

Para **N itens**, o ganho de performance é proporcional a **N×** (com um único thread). Quando `aiohttp` aguarda a resposta de um servidor, o event loop do asyncio aproveita esse tempo para disparar ou processar outras requisições.

### 2. Por que `asyncio.Semaphore`?

O `asyncio.gather` sem limitação poderia disparar 50 requisições simultâneas, causando:
- Rate-limiting ou banimento de IP pelo Mercado Livre
- Sobrecarga no pool de conexões do banco de dados
- Consumo excessivo de memória por corrotinas pendentes

O `Semaphore(MAX_CONCURRENT_REQUESTS=5)` atua como um **limitador de fluxo**: no máximo 5 requisições HTTP acontecem ao mesmo tempo, as demais aguardam na fila sem bloquear o event loop.

### 3. Por que `SQLAlchemy` com `asyncpg` (e não `psycopg2`)?

- `psycopg2` é **síncrono**: uma query ao banco bloquearia o event loop do FastAPI, anulando todo o ganho do asyncio.
- `asyncpg` é o driver **nativo assíncrono** para PostgreSQL, perfeitamente integrado ao asyncio.
- `SQLAlchemy 2.0` tem suporte nativo a async engines, preservando os benefícios do ORM (migrações, type safety) sem sacrificar a performance assíncrona.

### 4. Por que multi-stage Docker build?

```
Stage Builder:  python:3.11-slim + gcc + headers = ~800MB
Stage Runtime:  python:3.11-slim + only *.so libs = ~280MB
```

A imagem final não carrega compiladores, headers C ou caches de pip — apenas os binários necessários para executar a aplicação. Resultado: imagem ~65% menor, superfície de ataque reduzida, deploys mais rápidos.

### 5. Por que `return_exceptions=True` no `asyncio.gather`?

```python
results = await asyncio.gather(*tasks, return_exceptions=True)
```

Sem este flag, uma única falha em qualquer tarefa **cancela todas as outras** e propaga a exceção. Com `return_exceptions=True`, exceções viram valores no array de resultados — permitindo que o endpoint retorne resultados parciais (os itens que funcionaram) mesmo quando alguns falham.

---

## 🐳 Setup com Docker (Recomendado)

### Pré-requisitos

- Docker Engine 24+
- Docker Compose v2 (incluído no Docker Desktop)

### Subindo o ambiente

```bash
# 1. Clone o repositório
git clone https://github.com/seu-usuario/promoscraper.git
cd promoscraper

# 2. (Opcional) Configure variáveis de ambiente
cp .env.example .env
# Edite .env com suas credenciais se necessário

# 3. Suba os serviços (build + start)
docker-compose up --build

# Ou em background (detached mode)
docker-compose up --build -d
```

A API estará disponível em `http://localhost:8000` após o healthcheck do PostgreSQL passar (geralmente 15-20 segundos).

### Verificando os logs

```bash
# Logs de todos os serviços
docker-compose logs -f

# Apenas da API
docker-compose logs -f api

# Apenas do banco
docker-compose logs -f db
```

### Encerrando o ambiente

```bash
# Apenas para os containers (preserva dados)
docker-compose down

# Para containers E remove volumes (dados do banco)
docker-compose down -v
```

---

## 💻 Setup Local (Desenvolvimento)

### Pré-requisitos

- Python 3.11+
- PostgreSQL 14+ rodando localmente (ou via `docker run`)

```bash
# 1. Crie e ative um ambiente virtual
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\activate        # Windows

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Configure a variável de ambiente
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/promoscraper"

# 4. Inicie a aplicação
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> As tabelas são criadas automaticamente no startup via `init_db()`.

---

## 📡 Endpoints da API

### `GET /health`
Verifica o status da API e da conexão com o banco.

```json
{
  "status": "healthy",
  "database": "healthy",
  "version": "1.0.0"
}
```

---

### `POST /scrape/`
Scraping concorrente de promoções para múltiplos produtos.

**Request:**
```json
{
  "items": ["memória ram", "monitor gamer", "teclado mecânico"]
}
```

**Response:**
```json
{
  "total_items_requested": 3,
  "total_promotions_saved": 14,
  "results": [
    {
      "search_term": "memória ram",
      "status": "success",
      "promotions_found": 5,
      "promotions": [
        {
          "id": 1,
          "title": "Memória RAM 16GB DDR4 Kingston",
          "price": 299.90,
          "link": "https://www.mercadolivre.com.br/...",
          "source": "Mercado Livre",
          "search_term": "memória ram",
          "scraped_at": "2024-05-15T14:32:00Z"
        }
      ],
      "error": null
    }
  ]
}
```

**Códigos de status:**
| Código | Situação |
|--------|----------|
| `200` | Sucesso (mesmo com falhas parciais por item) |
| `422` | Payload inválido (lista vazia, termos muito longos) |
| `503` | Falha catastrófica no motor de scraping |

---

### `GET /promotions/`
Lista as promoções salvas com filtros opcionais.

**Query parameters:**
| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `search_term` | `string` | Filtra por termo de busca |
| `source` | `string` | Filtra por e-commerce |
| `limit` | `int` | Máx. de resultados (padrão: 50) |
| `offset` | `int` | Paginação |

**Exemplo:**
```
GET /promotions/?search_term=memória+ram&limit=10
```

---

## 🧪 Exemplos de Uso

### Com cURL

```bash
# Health check
curl http://localhost:8000/health

# Scraping de múltiplos produtos
curl -X POST http://localhost:8000/scrape/ \
  -H "Content-Type: application/json" \
  -d '{"items": ["placa de vídeo rtx", "processador amd ryzen", "ssd nvme 1tb"]}'

# Listar promoções salvas
curl "http://localhost:8000/promotions/?limit=20"

# Filtrar por termo
curl "http://localhost:8000/promotions/?search_term=ssd+nvme+1tb"
```

### Com Python (httpx)

```python
import httpx

BASE_URL = "http://localhost:8000"

with httpx.Client() as client:
    # Scraping
    response = client.post(
        f"{BASE_URL}/scrape/",
        json={"items": ["memória ram 16gb", "monitor 144hz"]},
        timeout=60.0,
    )
    data = response.json()
    print(f"✅ {data['total_promotions_saved']} promoções salvas")
    
    for result in data["results"]:
        print(f"\n📦 {result['search_term']}: {result['promotions_found']} resultado(s)")
        for promo in result["promotions"]:
            print(f"  • {promo['title'][:50]} — R$ {promo['price']:.2f}")
```

### Documentação Interativa (Swagger UI)

Acesse `http://localhost:8000/docs` para explorar e testar todos os endpoints via interface gráfica.

---

## ✅ Rodando os Testes

```bash
# Ativa o ambiente virtual (se usar setup local)
source .venv/bin/activate

# Instala dependências (incluindo as de teste)
pip install -r requirements.txt

# Roda a suite completa
pytest

# Com relatório de cobertura
pytest --cov=app --cov-report=term-missing

# Roda apenas testes unitários (rápido, sem I/O)
pytest -k "TestParsePrice or TestParseMercadoLivreHtml"

# Roda apenas testes de integração
pytest -k "TestScrapeEndpoint or TestListPromotions or TestHealth"

# Modo verbose
pytest -v
```

> Os testes usam **SQLite em memória** — não requerem PostgreSQL nem Docker.

### Estratégia de Testes

| Tipo | Quantidade | Descrição |
|------|-----------|-----------|
| **Unit** | ~10 | Funções puras de parsing (sem I/O) |
| **Integration** | ~10 | Rotas da API com banco SQLite e mocks de HTTP |
| **E2E parcial** | ~2 | Fluxo scrape → persist → list |

---

## 🗄️ Schema do Banco de Dados

```sql
CREATE TABLE promotions (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(512)  NOT NULL,
    price       FLOAT,                    -- NULL se preço não disponível
    link        TEXT          NOT NULL,
    source      VARCHAR(128)  NOT NULL,   -- ex: "Mercado Livre"
    search_term VARCHAR(256)  NOT NULL,   -- termo que gerou o registro
    scraped_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Índices para as queries mais comuns
CREATE INDEX ix_promotions_source      ON promotions(source);
CREATE INDEX ix_promotions_search_term ON promotions(search_term);
```

---

## 🔧 Variáveis de Ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/promoscraper` | URL de conexão com o PostgreSQL |
| `POSTGRES_USER` | `postgres` | Usuário do PostgreSQL (docker-compose) |
| `POSTGRES_PASSWORD` | `postgres` | Senha do PostgreSQL (docker-compose) |
| `POSTGRES_DB` | `promoscraper` | Nome do banco (docker-compose) |
| `LOG_LEVEL` | `info` | Nível de log da aplicação |

---

## 📈 Performance

Em testes com lista de 10 itens e latência média de ~2s por requisição:

| Abordagem | Tempo Total | Ganho |
|-----------|-------------|-------|
| Síncrono (`requests`) | ~20s | 1x |
| Async sem semáforo (`aiohttp`) | ~2s | **10x** |
| Async com semáforo=5 (`aiohttp`) | ~4s | **5x** |

O semáforo reduz ligeiramente o ganho, mas é essencial para evitar banimento de IP em produção.

---

## 🚀 Possíveis Evoluções

- [ ] **Múltiplas fontes**: Adicionar Amazon, Shopee, OLX como scrapers independentes
- [ ] **Cache de resultados**: Redis para evitar scraping repetido do mesmo termo em curto intervalo
- [ ] **Rate limiting na API**: Limitar requisições por IP com `slowapi`
- [ ] **Filas assíncronas**: Celery + Redis para scraping em background com notificação
- [ ] **Alertas de preço**: WebSocket ou webhooks para notificar quando preço cai abaixo de threshold
- [ ] **Migrações**: Alembic para gerenciar evoluções de schema em produção
- [ ] **Monitoramento**: Prometheus metrics + Grafana dashboard

---

*Desenvolvido como solução técnica para alta volumetria de dados e web scraping com Python assíncrono.*
