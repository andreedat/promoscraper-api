from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, field_validator

class ScrapeRequest(BaseModel):
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

class PromotionBase(BaseModel):
    title: str = Field(..., description="Título do produto no e-commerce.")
    price: float | None = Field(None, description="Preço em BRL. None se indisponível.")
    link: str = Field(..., description="URL do produto.")
    source: str = Field(..., description="Nome do e-commerce (ex: 'Mercado Livre').")
    search_term: str = Field(..., description="Termo de busca que gerou este resultado.")

class PromotionCreate(PromotionBase):
    pass

class PromotionRead(PromotionBase):
    id: int
    scraped_at: datetime
    model_config = {"from_attributes": True}

class ScrapeResult(BaseModel):
    search_term: str
    status: str = Field(..., description="'success' ou 'error'")
    promotions_found: int = Field(default=0)
    promotions: list[PromotionRead] = Field(default_factory=list)
    error: str | None = Field(None, description="Mensagem de erro se status='error'")

class ScrapeResponse(BaseModel):
    total_items_requested: int
    total_promotions_saved: int
    results: list[ScrapeResult]

class HealthResponse(BaseModel):
    status: str
    database: str
    version: str
