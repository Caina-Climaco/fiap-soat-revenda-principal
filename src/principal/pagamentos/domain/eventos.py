"""Eventos de domínio de Pagamentos (log estruturado e métricas, após o commit)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EventoPagamentos:
    NOME: ClassVar[str] = "EventoPagamentos"

    cobranca_id: UUID
    compra_id: UUID
    origem: str
    ocorrido_em: datetime

    @property
    def nome(self) -> str:
        return self.NOME

    def como_dict(self) -> dict[str, str]:
        dados = {
            "cobranca_id": str(self.cobranca_id),
            "compra_id": str(self.compra_id),
            "ocorrido_em": self.ocorrido_em.isoformat(),
        }
        if self.origem:
            dados["origem"] = self.origem
        return dados


@dataclass(frozen=True, slots=True)
class CobrancaGerada(EventoPagamentos):
    NOME: ClassVar[str] = "CobrancaGerada"


@dataclass(frozen=True, slots=True)
class CobrancaPaga(EventoPagamentos):
    NOME: ClassVar[str] = "CobrancaPaga"


@dataclass(frozen=True, slots=True)
class CobrancaCancelada(EventoPagamentos):
    NOME: ClassVar[str] = "CobrancaCancelada"


@dataclass(frozen=True, slots=True)
class PagamentoAposCancelamento(EventoPagamentos):
    """Pagamento informado para cobrança já cancelada: a loja precisa estornar (RN-10)."""

    NOME: ClassVar[str] = "PagamentoAposCancelamento"
