# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — Imagem de produção da PromoScraper API
#
# Estratégia multi-stage:
#   Stage 1 (builder): instala dependências em ambiente isolado
#   Stage 2 (runtime): copia apenas o necessário, sem ferramentas de build
#
# Isso reduz a imagem final em ~60% comparado a uma build single-stage,
# removendo compiladores, headers e caches de pip da imagem de produção.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Evita que Python crie arquivos .pyc e bufferize stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copia apenas requirements para aproveitar cache de layers do Docker.
# Se o requirements.txt não mudar, esta layer é reutilizada nas próximas builds.
COPY requirements.txt .

# Instala dependências no diretório /install para copiar depois
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Adiciona os pacotes instalados no builder ao PYTHONPATH do runtime
    PYTHONPATH=/usr/local/lib/python3.11/site-packages

# Usuário não-root para segurança em produção
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /api

# Copia dependências compiladas do builder
COPY --from=builder /install /usr/local

# Copia o código da aplicação
COPY --chown=appuser:appgroup app/ ./app/

USER appuser

EXPOSE 8000

# Usa exec form para que o processo receba sinais do Docker (SIGTERM) corretamente
# --workers 1: Single worker pois usamos asyncio; múltiplos workers = múltiplos event loops
# --timeout-keep-alive 30: Mantém conexões HTTP keep-alive abertas por 30s
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "30", \
     "--access-log"]
