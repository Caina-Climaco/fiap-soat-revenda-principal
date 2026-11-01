"""Esquemas HTTP de Pagamentos (docs/05-api.md, seções 5.4.4 e 5.6)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from principal.pagamentos.domain.cobranca import StatusCobranca
from principal.shared.http import Dinheiro, ModeloRequisicao, ModeloResposta

CodigoPagamentoTexto = Annotated[
    str,
    Field(
        pattern=r"^PAG-[0-9a-f]{12}$",
        description="Código do pagamento: PAG- seguido de 12 hexadecimais minúsculos.",
        examples=["PAG-3f9a1c2b7d4e"],
    ),
]


class NotificacaoPagamento(ModeloRequisicao):
    """Payload do processador de pagamentos (traduzido pela ACL do webhook)."""

    codigo_pagamento: CodigoPagamentoTexto
    status: Literal["PAGO", "CANCELADO"] = Field(
        description="PAGO: pagamento efetuado. CANCELADO: pagamento cancelado."
    )


class RespostaWebhook(ModeloResposta):
    codigo_pagamento: str
    status_cobranca: StatusCobranca
    efeito: Literal["APLICADO", "SEM_EFEITO"]


class CobrancaRequisicao(ModeloRequisicao):
    compra_id: UUID = Field(description="Compra cobrada (chave de idempotência do passo T3).")
    valor: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="Preço congelado da compra, como string decimal.",
        examples=["119900.00"],
    )


class CobrancaResposta(ModeloResposta):
    codigo_pagamento: str
    compra_id: UUID
    valor: Dinheiro | None
    status: StatusCobranca
    efeito: bool = Field(description="False quando o pedido repetiu um passo já aplicado.")
