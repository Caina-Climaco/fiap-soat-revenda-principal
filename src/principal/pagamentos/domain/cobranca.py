"""Agregado Cobranca (contexto Pagamentos): docs/02-modelagem-ddd.md, 2.6.2, e ADR-009.

A cobrança é o árbitro do pivô da saga: só uma transição sai de PENDENTE. Pagamento
(webhook) e cancelamento (processador ou compensação da saga) disputam essa saída sob a
trava da linha, e o perdedor recebe um erro que diz o que aconteceu.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from principal.pagamentos.domain.erros import (
    CobrancaJaPagaError,
    PagamentoAposCancelamentoError,
    ValorInvalidoError,
)
from principal.pagamentos.domain.eventos import (
    CobrancaCancelada,
    CobrancaGerada,
    CobrancaPaga,
    EventoPagamentos,
    PagamentoAposCancelamento,
)

_CENTAVOS = Decimal("0.01")


class StatusCobranca(StrEnum):
    PENDENTE = "PENDENTE"
    PAGA = "PAGA"
    CANCELADA = "CANCELADA"


class OrigemCancelamento(StrEnum):
    PROCESSADOR = "PROCESSADOR"  # o processador informou o cancelamento (webhook)
    SAGA = "SAGA"  # compensação da saga (expiração ou falha da compra)


@dataclass(frozen=True, slots=True)
class CodigoPagamento:
    """`PAG-` + 12 hexadecimais aleatórios (RN-14)."""

    FORMATO: ClassVar[re.Pattern[str]] = re.compile(r"^PAG-[0-9a-f]{12}$")

    valor: str

    def __post_init__(self) -> None:
        if not self.FORMATO.match(self.valor):
            raise ValueError("código de pagamento fora do formato PAG- + 12 hexadecimais")

    @classmethod
    def gerar(cls) -> CodigoPagamento:
        return cls(f"PAG-{secrets.token_hex(6)}")

    def __str__(self) -> str:
        return self.valor


@dataclass(eq=False)
class Cobranca:
    id: UUID
    codigo: CodigoPagamento
    compra_id: UUID
    valor: Decimal | None  # None só na marca de compensação (cobrança nunca gerada)
    status: StatusCobranca
    origem_cancelamento: OrigemCancelamento | None
    criada_em: datetime
    atualizada_em: datetime
    pagamento_apos_cancelamento: bool = False
    _eventos: list[EventoPagamentos] = field(default_factory=list, repr=False)

    @classmethod
    def gerar(cls, compra_id: UUID, valor: Decimal, agora: datetime) -> Cobranca:
        """T3 da saga: cobrança PENDENTE do valor congelado da compra."""
        if not valor.is_finite() or valor <= 0 or valor != valor.quantize(_CENTAVOS):
            raise ValorInvalidoError
        cobranca = cls(
            id=uuid.uuid4(),
            codigo=CodigoPagamento.gerar(),
            compra_id=compra_id,
            valor=valor.quantize(_CENTAVOS),
            status=StatusCobranca.PENDENTE,
            origem_cancelamento=None,
            criada_em=agora,
            atualizada_em=agora,
        )
        cobranca._registrar(CobrancaGerada, agora)
        return cobranca

    @classmethod
    def marca_de_compensacao(cls, compra_id: UUID, agora: datetime) -> Cobranca:
        """C3 antes de T3: cobrança já CANCELADA, para recusar um T3 atrasado dessa compra."""
        cobranca = cls(
            id=uuid.uuid4(),
            codigo=CodigoPagamento.gerar(),
            compra_id=compra_id,
            valor=None,
            status=StatusCobranca.CANCELADA,
            origem_cancelamento=OrigemCancelamento.SAGA,
            criada_em=agora,
            atualizada_em=agora,
        )
        cobranca._registrar(CobrancaCancelada, agora)
        return cobranca

    def sortear_novo_codigo(self) -> None:
        """Troca o código por outro sorteado (o anterior colidiu com o de outra cobrança).

        Só vale antes da primeira gravação: o código é a chave que o processador de
        pagamentos recebe, e uma cobrança já registrada não muda de código.
        """
        self.codigo = CodigoPagamento.gerar()

    @property
    def compensada_pela_saga(self) -> bool:
        return (
            self.status is StatusCobranca.CANCELADA
            and self.origem_cancelamento is OrigemCancelamento.SAGA
        )

    def registrar_pagamento(self, agora: datetime) -> bool:
        """Webhook PAGO (T4, pivô). False se já estava paga (notificação repetida)."""
        if self.status is StatusCobranca.PAGA:
            return False
        if self.status is StatusCobranca.CANCELADA:
            if not self.pagamento_apos_cancelamento:
                self.pagamento_apos_cancelamento = True
                self.atualizada_em = agora
                self._registrar(PagamentoAposCancelamento, agora)
            raise PagamentoAposCancelamentoError(str(self.codigo))
        self._mudar(StatusCobranca.PAGA, None, agora)
        self._registrar(CobrancaPaga, agora)
        return True

    def registrar_cancelamento(self, origem: OrigemCancelamento, agora: datetime) -> bool:
        """Webhook CANCELADO (processador) ou C3 (saga). False se já estava cancelada."""
        if self.status is StatusCobranca.CANCELADA:
            return False
        if self.status is StatusCobranca.PAGA:
            raise CobrancaJaPagaError(str(self.codigo))
        self._mudar(StatusCobranca.CANCELADA, origem, agora)
        self._registrar(CobrancaCancelada, agora)
        return True

    def _mudar(
        self, status: StatusCobranca, origem: OrigemCancelamento | None, agora: datetime
    ) -> None:
        self.status = status
        self.origem_cancelamento = origem
        self.atualizada_em = agora

    def _registrar(self, tipo: type[EventoPagamentos], agora: datetime) -> None:
        self._eventos.append(
            tipo(
                cobranca_id=self.id,
                compra_id=self.compra_id,
                origem=self.origem_cancelamento.value if self.origem_cancelamento else "",
                ocorrido_em=agora,
            )
        )

    def coletar_eventos(self) -> list[EventoPagamentos]:
        eventos, self._eventos = self._eventos, []
        return eventos
