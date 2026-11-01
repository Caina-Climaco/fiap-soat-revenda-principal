"""Cria os schemas catalogo, pagamentos e mensageria, com tabelas, constraints e índices.

Revision ID: 0001
Revises:
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMAS = ("catalogo", "pagamentos", "mensageria")


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    op.create_table(
        "compensacoes",
        sa.Column("compra_id", sa.Uuid(), nullable=False),
        sa.Column("veiculo_id", sa.Uuid(), nullable=False),
        sa.Column("registrada_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("compra_id", name=op.f("pk_compensacoes")),
        schema="catalogo",
    )
    op.create_table(
        "veiculos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("marca", sa.String(length=60), nullable=False),
        sa.Column("modelo", sa.String(length=60), nullable=False),
        sa.Column("ano", sa.SmallInteger(), nullable=False),
        sa.Column("cor", sa.String(length=30), nullable=False),
        sa.Column("preco", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "status", sa.String(length=12), server_default=sa.text("'A_VENDA'"), nullable=False
        ),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("compra_id", sa.Uuid(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'A_VENDA') = (compra_id IS NULL)",
            name=op.f("ck_veiculos_compra_conforme_status"),
        ),
        sa.CheckConstraint(
            "status IN ('A_VENDA', 'BLOQUEADO', 'VENDIDO')", name=op.f("ck_veiculos_status")
        ),
        sa.CheckConstraint("ano BETWEEN 1950 AND 2100", name=op.f("ck_veiculos_ano_faixa")),
        sa.CheckConstraint(
            "length(trim(marca)) > 0 AND length(trim(modelo)) > 0 AND length(trim(cor)) > 0",
            name=op.f("ck_veiculos_textos_nao_vazios"),
        ),
        sa.CheckConstraint("preco > 0", name=op.f("ck_veiculos_preco_positivo")),
        sa.CheckConstraint("versao >= 1", name=op.f("ck_veiculos_versao")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_veiculos")),
        schema="catalogo",
    )
    op.create_index(
        "ix_veiculos_status_criado_em",
        "veiculos",
        ["status", "criado_em"],
        unique=False,
        schema="catalogo",
    )
    op.create_table(
        "cobrancas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("codigo", sa.String(length=16), nullable=False),
        sa.Column("compra_id", sa.Uuid(), nullable=False),
        sa.Column("valor", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("origem_cancelamento", sa.String(length=12), nullable=True),
        sa.Column(
            "pagamento_apos_cancelamento",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizada_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'CANCELADA') = (origem_cancelamento IS NOT NULL)",
            name=op.f("ck_cobrancas_origem_status"),
        ),
        sa.CheckConstraint(
            "origem_cancelamento IS NULL OR origem_cancelamento IN ('PROCESSADOR', 'SAGA')",
            name=op.f("ck_cobrancas_origem"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDENTE', 'PAGA', 'CANCELADA')", name=op.f("ck_cobrancas_status")
        ),
        sa.CheckConstraint(
            "valor > 0 OR (valor IS NULL AND status = 'CANCELADA')", name=op.f("ck_cobrancas_valor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cobrancas")),
        sa.UniqueConstraint("codigo", name=op.f("uq_cobrancas_codigo")),
        sa.UniqueConstraint("compra_id", name=op.f("uq_cobrancas_compra_id")),
        schema="pagamentos",
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("chave", sa.String(length=80), nullable=False),
        sa.Column("metodo", sa.String(length=8), nullable=False),
        sa.Column("caminho", sa.String(length=300), nullable=False),
        sa.Column("corpo", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contexto_trace", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'PENDENTE'"), nullable=False
        ),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("proxima_tentativa", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_erro", sa.String(length=500), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDENTE', 'ENTREGUE', 'FALHOU')", name=op.f("ck_outbox_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
        schema="mensageria",
    )
    op.create_index(
        "ix_outbox_chave_pendentes",
        "outbox",
        ["chave", "id"],
        unique=False,
        schema="mensageria",
        postgresql_where=sa.text("status = 'PENDENTE'"),
    )
    op.create_index(
        "ix_outbox_pendentes",
        "outbox",
        ["proxima_tentativa", "id"],
        unique=False,
        schema="mensageria",
        postgresql_where=sa.text("status = 'PENDENTE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_pendentes",
        table_name="outbox",
        schema="mensageria",
        postgresql_where=sa.text("status = 'PENDENTE'"),
    )
    op.drop_index(
        "ix_outbox_chave_pendentes",
        table_name="outbox",
        schema="mensageria",
        postgresql_where=sa.text("status = 'PENDENTE'"),
    )
    op.drop_table("outbox", schema="mensageria")
    op.drop_table("cobrancas", schema="pagamentos")
    op.drop_index("ix_veiculos_status_criado_em", table_name="veiculos", schema="catalogo")
    op.drop_table("veiculos", schema="catalogo")
    op.drop_table("compensacoes", schema="catalogo")
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")
