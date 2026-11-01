"""Migração tolerante a rollback (python -m principal.migracao) contra PostgreSQL real.

Cada cenário roda numa transação desfeita no final (DDL do PostgreSQL é transacional),
então o banco compartilhado pelos demais testes não muda.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text

from apoio.banco import RAIZ_PROJETO, apagar_schemas, config_alembic
from principal import migracao


@pytest.fixture
def conexao(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as conexao:
        transacao = conexao.begin()
        try:
            yield conexao
        finally:
            transacao.rollback()


@pytest.fixture
def logging_preservado() -> Iterator[None]:
    """main() aplica o fileConfig do alembic.ini; devolve o logging como estava."""
    raiz = logging.getLogger()
    handlers, nivel = list(raiz.handlers), raiz.level
    yield
    raiz.handlers[:] = handlers
    raiz.setLevel(nivel)


def _head() -> str:
    head = ScriptDirectory.from_config(Config(str(RAIZ_PROJETO / "alembic.ini"))).get_current_head()
    assert head
    return head


def _revisao(conexao: Connection) -> tuple[str, ...]:
    return tuple(MigrationContext.configure(conexao).get_current_heads())


def test_banco_no_head_roda_upgrade_sem_efeito(conexao: Connection) -> None:
    resultado = migracao.migrar(config_alembic(conexao), conexao)
    assert resultado.aplicada
    assert resultado.revisoes_banco == (_head(),)
    assert _revisao(conexao) == (_head(),)


def test_banco_vazio_recebe_todas_as_migracoes(conexao: Connection) -> None:
    apagar_schemas(conexao)
    resultado = migracao.migrar(config_alembic(conexao), conexao)
    assert resultado.aplicada
    assert resultado.revisoes_banco == ()
    assert _revisao(conexao) == (_head(),)
    assert inspect(conexao).has_table("outbox", schema="mensageria")


def test_banco_a_frente_da_imagem_e_mantido_intacto(
    conexao: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    # Simula o rollback: o banco já recebeu a migração "0002" de uma versão mais nova,
    # que os scripts desta imagem (anterior) não conhecem.
    conexao.execute(text("UPDATE public.alembic_version SET version_num = '0002_futura'"))
    conexao.execute(text("CREATE TABLE catalogo.coluna_da_versao_nova (x int)"))
    with caplog.at_level("WARNING", logger="principal.migracao"):
        resultado = migracao.migrar(config_alembic(conexao), conexao)
    assert not resultado.aplicada
    assert resultado.desconhecidas == ("0002_futura",)
    assert _revisao(conexao) == ("0002_futura",)
    assert inspect(conexao).has_table("coluna_da_versao_nova", schema="catalogo")
    assert "migração ignorada, banco intacto" in caplog.text


@pytest.mark.usefixtures("logging_preservado")
def test_main_usa_as_variaveis_de_ambiente_e_termina_com_zero(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    for nome in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        monkeypatch.delenv(nome, raising=False)
    assert migracao.main([str(RAIZ_PROJETO / "alembic.ini")]) == 0
    with engine.connect() as conexao:
        assert _revisao(conexao) == (_head(),)


@pytest.mark.usefixtures("logging_preservado")
def test_main_com_banco_a_frente_termina_com_zero_sem_alterar(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    with engine.begin() as conexao:
        conexao.execute(text("UPDATE public.alembic_version SET version_num = '0002_futura'"))
    try:
        assert migracao.main([str(RAIZ_PROJETO / "alembic.ini")]) == 0
        with engine.connect() as conexao:
            assert _revisao(conexao) == ("0002_futura",)
    finally:
        with engine.begin() as conexao:
            conexao.execute(
                text("UPDATE public.alembic_version SET version_num = :v"), {"v": _head()}
            )
