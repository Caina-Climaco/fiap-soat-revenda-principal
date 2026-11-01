"""Eventos de domínio do Catálogo (log estruturado e métricas, após o commit)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EventoCatalogo:
    NOME: ClassVar[str] = "EventoCatalogo"

    veiculo_id: UUID
    ocorrido_em: datetime

    @property
    def nome(self) -> str:
        return self.NOME

    def como_dict(self) -> dict[str, str]:
        return {"veiculo_id": str(self.veiculo_id), "ocorrido_em": self.ocorrido_em.isoformat()}


@dataclass(frozen=True, slots=True)
class VeiculoCadastrado(EventoCatalogo):
    NOME: ClassVar[str] = "VeiculoCadastrado"

    preco: Decimal = Decimal("0")

    def como_dict(self) -> dict[str, str]:
        return {**EventoCatalogo.como_dict(self), "preco": str(self.preco)}


@dataclass(frozen=True, slots=True)
class VeiculoEditado(EventoCatalogo):
    NOME: ClassVar[str] = "VeiculoEditado"

    versao: int = 0
    campos: str = ""

    def como_dict(self) -> dict[str, str]:
        return {
            **EventoCatalogo.como_dict(self),
            "versao": str(self.versao),
            "campos": self.campos,
        }


@dataclass(frozen=True, slots=True)
class EventoDeCompra(EventoCatalogo):
    """Evento disparado por um passo da saga: carrega a compra envolvida."""

    NOME: ClassVar[str] = "EventoDeCompra"

    compra_id: UUID | None = None

    def como_dict(self) -> dict[str, str]:
        return {**EventoCatalogo.como_dict(self), "compra_id": str(self.compra_id)}


@dataclass(frozen=True, slots=True)
class VeiculoBloqueado(EventoDeCompra):
    NOME: ClassVar[str] = "VeiculoBloqueado"


@dataclass(frozen=True, slots=True)
class VeiculoDesbloqueado(EventoDeCompra):
    NOME: ClassVar[str] = "VeiculoDesbloqueado"


@dataclass(frozen=True, slots=True)
class VeiculoVendido(EventoDeCompra):
    NOME: ClassVar[str] = "VeiculoVendido"
