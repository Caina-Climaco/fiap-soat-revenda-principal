"""Abstração da unidade de trabalho usada pelos casos de uso (sem depender de SQLAlchemy)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from principal.shared.eventos import EventoDominio


class UnidadeDeTrabalho(Protocol):
    def registrar_eventos(self, eventos: Iterable[EventoDominio]) -> None:
        """Enfileira eventos de domínio para publicação somente após o commit."""
        ...

    def confirmar(self) -> None:
        """Commit da transação e, em seguida, publicação dos eventos enfileirados."""
        ...
