# Painel de acompanhamento do treino, publicado no site pelo tunel do Cloudflare + hub.
# So le os arquivos do treino (montados como somente leitura); o treino continua rodando no Windows.
# Contexto do build: a pasta v19 (ver docker-compose.yml, servico painel-treino).
FROM python:3.12-slim

ARG TARGETARCH

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ARGOS_PAINEL_CONTAINER=1 \
    ARGOS_TREINO_DIR=/treino \
    ARGOS_PAINEL_DATASET=/dataset/argos_epi_v1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ADD --chmod=755 https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH} /usr/local/bin/cloudflared

WORKDIR /app

COPY treinamento/comum.py treinamento/comum.py
COPY treinamento/acompanhar_treinamento/servidor.py treinamento/acompanhar_treinamento/ponte.py treinamento/acompanhar_treinamento/index.html treinamento/acompanhar_treinamento/
# logo.png e favicon.ico chegam montados em /coisas (a pasta coisas fica fora do contexto do build)

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=5)" || exit 1

CMD ["python", "treinamento/acompanhar_treinamento/servidor.py", "--publico", "--porta", "8765"]
