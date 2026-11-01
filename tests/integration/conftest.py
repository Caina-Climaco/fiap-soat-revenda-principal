"""Infraestrutura dos testes de integração: PostgreSQL real, migrações Alembic e a API.

- O banco vem de `TEST_DATABASE_URL`; sem ela, todos os testes desta pasta são pulados
  (com aviso no cabeçalho e no resumo `-ra`), para que `pytest` continue rodando sem banco.
- No início da sessão os schemas são recriados com `alembic upgrade head` (valida as
  migrações a cada execução); antes de cada teste as tabelas são esvaziadas.
- A API é montada pelo caminho de produção (`criar_app`), com relógio fixo, o relay
  desligado (os testes o acionam) e um entregador falso no lugar do serviço de venda.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from apoio.api import SEGREDO_WEBHOOK, TOKEN_ENTRADA, Api
from apoio.banco import esvaziar, recriar_schemas
from apoio.cenario import AS_10H
from apoio.fakes import EntregadorFalso
from principal.main import criar_app
from principal.mensageria.relay import RelayOutbox
from principal.shared.clock import RelogioFixo
from principal.shared.config import Settings
from principal.shared.db import BancoDeDados, criar_engine

VARIAVEL_URL = "TEST_DATABASE_URL"
_PASTA = Path(__file__).parent
_AVISO = (
    f"{VARIAVEL_URL} ausente: testes de integração PULADOS "
    "(ex.: postgresql+psycopg://revenda:revenda@localhost:5432/revenda_principal_test)"
)


def _url_banco() -> str | None:
    return os.environ.get(VARIAVEL_URL) or None


def pytest_report_header() -> str | None:
    return None if _url_banco() else f"AVISO: {_AVISO}"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if _url_banco():
        return
    pular = pytest.mark.skip(reason=_AVISO)
    for item in items:
        if _PASTA in item.path.parents:
            item.add_marker(pular)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _url_banco()
    assert url, _AVISO  # a coleta já pulou os testes; aqui é só garantia
    # Pool maior que o padrão: os testes de concorrência abrem uma conexão por thread.
    engine = criar_engine(url, pool_size=12, max_overflow=4)
    recriar_schemas(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _tabelas_vazias(engine: Engine) -> None:
    esvaziar(engine)


@pytest.fixture
def banco(engine: Engine) -> BancoDeDados:
    return BancoDeDados(engine)


@pytest.fixture
def relogio() -> RelogioFixo:
    return RelogioFixo(AS_10H)


@pytest.fixture
def settings() -> Settings:
    url = _url_banco()
    assert url
    return Settings(
        database_url=url,
        webhook_secret=SEGREDO_WEBHOOK,
        token_servico_entrada=TOKEN_ENTRADA,
        token_servico_saida="token-de-saida-0123456789",
        outbox_relay_ativo=False,
        log_level="INFO",
    )


@pytest.fixture
def vendas() -> EntregadorFalso:
    """O serviço de venda, do ponto de vista da outbox."""
    return EntregadorFalso()


@pytest.fixture
def app(
    settings: Settings, banco: BancoDeDados, relogio: RelogioFixo, vendas: EntregadorFalso
) -> FastAPI:
    return criar_app(settings, banco=banco, relogio=relogio, entregador=vendas)


@pytest.fixture
def relay(app: FastAPI) -> RelayOutbox:
    relay: RelayOutbox = app.state.relay
    return relay


@pytest.fixture
def cliente_http(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as cliente:
        yield cliente


@pytest.fixture
def api(cliente_http: TestClient) -> Api:
    return Api(cliente_http)
