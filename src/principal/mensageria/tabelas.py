"""Tabela `mensageria.outbox` (ADR-003), compartilhada pelos dois contextos do principal."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from principal.shared.db import CONVENCAO_NOMES

SCHEMA = "mensageria"

metadata = MetaData(schema=SCHEMA, naming_convention=CONVENCAO_NOMES)

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("tipo", String(30), nullable=False),
    Column("chave", String(80), nullable=False),
    Column("metodo", String(8), nullable=False),
    Column("caminho", String(300), nullable=False),
    Column("corpo", JSONB, nullable=False),
    Column("contexto_trace", JSONB, nullable=True),
    Column("status", String(10), nullable=False, server_default=text("'PENDENTE'")),
    Column("tentativas", Integer, nullable=False, server_default=text("0")),
    Column("proxima_tentativa", DateTime(timezone=True), nullable=False),
    Column("ultimo_erro", String(500), nullable=True),
    Column("criado_em", DateTime(timezone=True), nullable=False),
    Column("entregue_em", DateTime(timezone=True), nullable=True),
    CheckConstraint("status IN ('PENDENTE', 'ENTREGUE', 'FALHOU')", name="status"),
    # Busca do relay: só as pendentes, na ordem de gravação.
    Index(
        "ix_outbox_pendentes",
        "proxima_tentativa",
        "id",
        postgresql_where=text("status = 'PENDENTE'"),
    ),
    # Ordem por agregado: a mensagem só sai se não houver outra pendente mais antiga da chave.
    Index("ix_outbox_chave_pendentes", "chave", "id", postgresql_where=text("status = 'PENDENTE'")),
)
