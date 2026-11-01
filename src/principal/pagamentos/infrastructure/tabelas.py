"""Tabela `pagamentos.cobrancas`. Só este contexto mapeia o schema."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

from principal.shared.db import CONVENCAO_NOMES

SCHEMA = "pagamentos"

metadata = MetaData(schema=SCHEMA, naming_convention=CONVENCAO_NOMES)

cobrancas = Table(
    "cobrancas",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("codigo", String(16), nullable=False),
    Column("compra_id", Uuid(as_uuid=True), nullable=False),
    Column("valor", Numeric(12, 2, asdecimal=True), nullable=True),
    Column("status", String(10), nullable=False),
    Column("origem_cancelamento", String(12), nullable=True),
    Column("pagamento_apos_cancelamento", Boolean, nullable=False, server_default=text("false")),
    Column("criada_em", DateTime(timezone=True), nullable=False),
    Column("atualizada_em", DateTime(timezone=True), nullable=False),
    UniqueConstraint("codigo"),
    UniqueConstraint("compra_id"),  # uma cobrança por compra: idempotência do passo T3
    CheckConstraint("status IN ('PENDENTE', 'PAGA', 'CANCELADA')", name="status"),
    CheckConstraint(
        "origem_cancelamento IS NULL OR origem_cancelamento IN ('PROCESSADOR', 'SAGA')",
        name="origem",
    ),
    CheckConstraint(
        "(status = 'CANCELADA') = (origem_cancelamento IS NOT NULL)", name="origem_status"
    ),
    # Valor ausente só na marca de compensação (cobrança nunca gerada, já cancelada).
    CheckConstraint("valor > 0 OR (valor IS NULL AND status = 'CANCELADA')", name="valor"),
)
