"""Preparação do banco dos testes de integração com as migrações Alembic do projeto."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text

RAIZ_PROJETO = Path(__file__).resolve().parents[2]
SCHEMAS = ("mensageria", "pagamentos", "catalogo")
TABELAS = "catalogo.veiculos, catalogo.compensacoes, pagamentos.cobrancas, mensageria.outbox"


def config_alembic(conexao: Connection) -> Config:
    """Config do alembic.ini da raiz, reaproveitando uma conexão aberta (ver migrations/env.py)."""
    config = Config(str(RAIZ_PROJETO / "alembic.ini"))
    config.attributes["connection"] = conexao
    return config


def apagar_schemas(conexao: Connection) -> None:
    for schema in SCHEMAS:
        conexao.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    conexao.execute(text("DROP TABLE IF EXISTS public.alembic_version"))


def recriar_schemas(engine: Engine) -> None:
    """Apaga os schemas da aplicação e aplica `alembic upgrade head` do zero."""
    with engine.begin() as conexao:
        apagar_schemas(conexao)
        command.upgrade(config_alembic(conexao), "head")


def esvaziar(engine: Engine) -> None:
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {TABELAS} RESTART IDENTITY"))
