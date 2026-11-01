"""Porta da outbox transacional (ADR-003), usada pelos casos de uso dos dois contextos.

O caso de uso registra a mensagem na mesma transação da mudança de negócio; o relay
(`principal.mensageria`) a entrega depois, por HTTP, ao serviço de venda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MensagemSaida:
    tipo: str  # ex.: "ANUNCIO", "RESULTADO_PAGAMENTO"
    chave: str  # agregado de origem: mensagens com a mesma chave são entregues em ordem
    metodo: str  # verbo HTTP no serviço de destino
    caminho: str  # caminho no serviço de destino (rotas /internal/v1)
    corpo: dict[str, object] = field(default_factory=dict)


class Outbox(Protocol):
    def registrar(self, mensagem: MensagemSaida) -> None:
        """Grava a mensagem na transação corrente (sem commit)."""
        ...
