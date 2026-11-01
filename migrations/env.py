"""Ambiente do Alembic: URL a partir das variáveis de ambiente (nunca de arquivo versionado)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from principal.catalogo.infrastructure.tabelas import metadata as metadata_catalogo
from principal.mensageria.tabelas import metadata as metadata_mensageria
from principal.pagamentos.infrastructure.tabelas import metadata as metadata_pagamentos
from principal.shared.config import DatabaseSettings

config = context.config

# Logs do alembic.ini (ex.: "Running upgrade -> 0001") na saída do Job de migração. Quando
# os testes entregam a conexão pronta, a configuração de log do pytest é preservada.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = [metadata_catalogo, metadata_pagamentos, metadata_mensageria]
SCHEMAS = {"catalogo", "pagamentos", "mensageria"}


def _incluir_nome(nome: str | None, tipo: str, _pais: object) -> bool:
    # Autogenerate só olha os schemas da aplicação (e public, onde fica alembic_version).
    if tipo == "schema":
        return nome is None or nome in SCHEMAS
    return True


def _configurar(conexao: Connection) -> None:
    context.configure(
        connection=conexao,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=_incluir_nome,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def executar_online() -> None:
    # Os testes de integração entregam uma conexão pronta (config.attributes["connection"]).
    conexao_externa = config.attributes.get("connection")
    if conexao_externa is not None:
        _configurar(conexao_externa)
        return
    engine = create_engine(DatabaseSettings().url_banco(), poolclass=pool.NullPool)
    with engine.connect() as conexao:
        _configurar(conexao)


if context.is_offline_mode():
    context.configure(
        url=DatabaseSettings().url_banco().render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    executar_online()
