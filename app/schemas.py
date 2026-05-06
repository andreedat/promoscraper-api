"""
schemas.py — Contratos de dados da API definidos via Pydantic v2.

Separa completamente a camada de serialização/validação dos modelos ORM,
seguindo o princípio de responsabilidade única e evitando acoplamento
entre a API e o banco de dados.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------


class ScrapeRequest(BaseModel):
    """
    Payload de entrada para o endpoint POST /scrape/.

    Valida que a lista de itens não está vazia e que cada termo
    tem comprimento razoável para evitar buscas abusivas.
    """

    items: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=20,
            description="Lista de produtos a buscar. Mínimo 1, máximo 20 termos.",
            examples=[["memória ram", "monitor gamer", "teclado mecânico"]],
        ),
    ]

    @field_validator("items", mode="before")
    @classmethod
    def validate_items(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in v:
            item = item.strip()
            if not item:
                raise ValueError("Os termos de busca não podem ser strings vazias.")
            if len(item) > 100:
                raise ValueError(f"Termo '{item[:20]}...' excede 100 caracteres.")
            cleaned.append(item)
        return cleaned


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------


class PromotionBase(BaseModel):
    """Campos compartilhados entre criação e leitura de uma promoção."""

    title: str = Field(..., description="Título do produto no e-commerce.")
    price: float | None = Field(None, description="Preço em BRL. None se indisponível.")
    link: str = Field(..., description="URL do produto.")
    source: str = Field(..., description="Nome do e-commerce (ex: 'Mercado Livre').")
    search_term: str = Field(..., description="Termo de busca que gerou este resultado.")


class PromotionCreate(PromotionBase):
    """Schema interno usado ao persistir um novo registro no banco."""
    pass


class PromotionRead(PromotionBase):
    """Schema de saída que expõe os campos do banco de dados ao cliente."""

    id: int
    scraped_at: datetime

    model_config = {"from_attributes": True}  # Habilita ORM mode (Pydantic v2)


# ---------------------------------------------------------------------------
# Aggregate Response Schemas
# ---------------------------------------------------------------------------


class ScrapeResult(BaseModel):
    """
    Resultado parcial para um único termo de busca.
    Permite ao cliente distinguir sucesso de falha por item.
    """

    search_term: str
    status: str = Field(..., description="'success' ou 'error'")
    promotions_found: int = Field(default=0)
    promotions: list[PromotionRead] = Field(default_factory=list)
    error: str | None = Field(None, description="Mensagem de erro se status='error'")


class ScrapeResponse(BaseModel):
    """
    Envelope da resposta do endpoint POST /scrape/.
    Agrega resultados de todos os termos solicitados.
    """

    total_items_requested: int
    total_promotions_saved: int
    results: list[ScrapeResult]


class HealthResponse(BaseModel):
    """Resposta do endpoint de health check."""

    status: str
    database: str
    version: str
