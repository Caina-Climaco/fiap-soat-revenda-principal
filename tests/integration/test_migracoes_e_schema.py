"""Migrações Alembic e o modelo físico: schemas, constraints, índices e LGPD."""

from __future__ import annotations

from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from apoio.banco import SCHEMAS, config_alembic, recriar_schemas
from principal.catalogo.infrastructure.tabelas import metadata as metadata_catalogo
from principal.mensageria.tabelas import metadata as metadata_mensageria
from principal.pagamentos.infrastructure.tabelas import metadata as metadata_pagamentos

METADADOS = [metadata_catalogo, metadata_pagamentos, metadata_mensageria]


def _consulta(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conexao:
        return list(conexao.execute(text(sql), params))


def test_migracao_cria_schemas_tabelas_e_versao(engine: Engine) -> None:
    inspetor = inspect(engine)
    assert set(SCHEMAS) <= set(inspetor.get_schema_names())
    assert sorted(inspetor.get_table_names(schema="catalogo")) == ["compensacoes", "veiculos"]
    assert inspetor.get_table_names(schema="pagamentos") == ["cobrancas"]
    assert inspetor.get_table_names(schema="mensageria") == ["outbox"]
    assert _consulta(engine, "SELECT version_num FROM public.alembic_version") == [("0001",)]


def test_unicidades_que_sustentam_a_idempotencia(engine: Engine) -> None:
    unicas = {
        tuple(u["column_names"])
        for u in inspect(engine).get_unique_constraints("cobrancas", schema="pagamentos")
    }
    assert unicas == {("codigo",), ("compra_id",)}
    pk = inspect(engine).get_pk_constraint("compensacoes", schema="catalogo")
    assert pk["constrained_columns"] == ["compra_id"]


def test_constraints_de_estado(engine: Engine) -> None:
    nomes = {
        r[0]
        for r in _consulta(
            engine,
            "SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname IN ('catalogo', 'pagamentos', 'mensageria') AND contype = 'c'",
        )
    }
    assert {
        "ck_veiculos_status",
        "ck_veiculos_compra_conforme_status",
        "ck_cobrancas_status",
        "ck_cobrancas_origem_status",
        "ck_cobrancas_valor",
        "ck_outbox_status",
    } <= nomes


def test_indices_parciais_da_outbox(engine: Engine) -> None:
    indices = {
        r[0]: r[1]
        for r in _consulta(
            engine,
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'mensageria'",
        )
    }
    for nome in ("ix_outbox_pendentes", "ix_outbox_chave_pendentes"):
        assert "WHERE ((status)::text = 'PENDENTE'::text)" in indices[nome]


def test_sem_chave_estrangeira_entre_schemas(engine: Engine) -> None:
    fks = _consulta(engine, "SELECT conname FROM pg_constraint WHERE contype = 'f'")
    assert fks == []


def test_schema_sem_dados_pessoais(engine: Engine) -> None:
    """O principal nunca guarda CPF, nome, e-mail ou telefone do comprador (ADR-005 do venda)."""
    colunas = _consulta(
        engine,
        "SELECT table_schema, table_name, column_name FROM information_schema.columns "
        "WHERE table_schema IN ('catalogo', 'pagamentos', 'mensageria')",
    )
    suspeitas = [
        c
        for c in colunas
        if any(p in c[2] for p in ("cpf", "nome", "email", "telefone", "documento"))
    ]
    assert suspeitas == []


def test_modelos_orm_coincidem_com_a_migracao(engine: Engine) -> None:
    """Equivalente a `alembic check`: o autogenerate não encontraria nada a migrar."""
    with engine.connect() as conexao:
        contexto = MigrationContext.configure(
            conexao,
            opts={
                "include_schemas": True,
                "include_name": lambda nome, tipo, _p: tipo != "schema" or nome in SCHEMAS,
                "compare_type": True,
            },
        )
        diferencas = compare_metadata(contexto, METADADOS)
    assert diferencas == []


def test_downgrade_e_upgrade_do_zero(engine: Engine) -> None:
    with engine.begin() as conexao:
        command.downgrade(config_alembic(conexao), "base")
    assert inspect(engine).get_table_names(schema="public") == ["alembic_version"]
    assert not set(SCHEMAS) & set(inspect(engine).get_schema_names())
    recriar_schemas(engine)
    assert inspect(engine).get_table_names(schema="mensageria") == ["outbox"]
