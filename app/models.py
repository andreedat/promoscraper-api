"""
models.py — Modelos ORM que mapeiam as entidades do domínio para tabelas no PostgreSQL.

Cada classe herda de Base (DeclarativeBase) e define colunas com tipos nativos
do SQLAlchemy, garantindo portabilidade e geração correta do schema.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Promotion(Base):
    """
    Representa uma promoção encontrada durante o scraping.

    Rastreabilidade completa: sabemos qual termo foi buscado (search_term),
    em qual fonte foi encontrada (source), e quando o registro foi criado (scraped_at).
    """

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="Título do produto conforme exibido no e-commerce")

    price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="Preço em BRL; None se não foi possível extrair")

    link: Mapped[str] = mapped_column(Text, nullable=False, comment="URL canônica do produto no e-commerce")

    source: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="Nome do e-commerce de origem (ex: 'Mercado Livre')",
    )

    search_term: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        index=True,
        comment="Palavra-chave que originou este resultado de scraping",
    )

    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="Timestamp UTC de quando o dado foi coletado",
    )

    def __repr__(self) -> str:
        return (
            f"<Promotion id={self.id} title={self.title!r:.40} "
            f"price={self.price} source={self.source!r}>"
        )
