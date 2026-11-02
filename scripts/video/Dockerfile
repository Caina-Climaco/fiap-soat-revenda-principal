# Imagem de gravacao do video (scripts/video/README.md). Construida pelo gravar.ps1 uma vez.
# Base oficial do Playwright para Python (Ubuntu 24.04 "noble", navegadores ja instalados em
# /ms-playwright). A imagem NAO traz o pacote pip "playwright": ele e instalado aqui, com a
# MESMA versao da tag (driver e navegadores precisam casar).
# Fonte da tag: https://playwright.dev/python/docs/docker (v1.63.0-noble) e PyPI playwright 1.63.0.
# ffmpeg (6.1 do Ubuntu 24.04) e Pillow permitem rodar o montar.sh no PC (gravar.ps1 -Montar),
# dentro do container, sem instalar binarios no Windows (Smart App Control).
ARG PLAYWRIGHT_VERSION=1.63.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble
ARG PLAYWRIGHT_VERSION
ARG PILLOW_VERSION=12.3.0
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" "pillow==${PILLOW_VERSION}" \
    && python3 -c "from importlib.metadata import version as v; print('playwright', v('playwright'), 'pillow', v('pillow'))"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /saida
