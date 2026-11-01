"""Tabelas do schema `catalogo`. Só este contexto mapeia o schema."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Uuid,
    text,
)

from principal.shared.db import CONVENCAO_NOMES

SCHEMA = "catalogo"

metadata = MetaData(schema=SCHEMA, naming_convention=CONVENCAO_NOMES)

veiculos = Table(
    "veiculos",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("marca", String(60), nullable=False),
    Column("modelo", String(60), nullable=False),
    Column("ano", SmallInteger, nullable=False),
    Column("cor", String(30), nullable=False),
    Column("preco", Numeric(12, 2, asdecimal=True), nullable=False),
    Column("status", String(12), nullable=False, server_default=text("'A_VENDA'")),
    Column("versao", Integer, nullable=False, server_default=text("1")),
    Column("compra_id", Uuid(as_uuid=True), nullable=True),
    Column("criado_em", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("atualizado_em", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("status IN ('A_VENDA', 'BLOQUEADO', 'VENDIDO')", name="status"),
    CheckConstraint("preco > 0", name="preco_positivo"),
    CheckConstraint("ano BETWEEN 1950 AND 2100", name="ano_faixa"),
    CheckConstraint(
        "length(trim(marca)) > 0 AND length(trim(modelo)) > 0 AND length(trim(cor)) > 0",
        name="textos_nao_vazios",
    ),
    CheckConstraint("versao >= 1", name="versao"),
    # Bloqueado ou vendido sempre tem a compra; à venda, nunca.
    CheckConstraint(
        "(status = 'A_VENDA') = (compra_id IS NULL)",
        name="compra_conforme_status",
    ),
    Index("ix_veiculos_status_criado_em", "status", "criado_em"),
)

# Compras cujo bloqueio foi compensado (regra contra ressurreição, docs/05-api.md, 5.6).
compensacoes = Table(
    "compensacoes",
    metadata,
    Column("compra_id", Uuid(as_uuid=True), primary_key=True),
    Column("veiculo_id", Uuid(as_uuid=True), nullable=False),
    Column("registrada_em", DateTime(timezone=True), nullable=False),
)
