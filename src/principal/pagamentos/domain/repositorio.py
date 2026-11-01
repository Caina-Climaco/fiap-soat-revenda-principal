"""Porta de persistência do agregado Cobranca."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from principal.pagamentos.domain.cobranca import Cobranca


class CobrancaRepository(Protocol):
    def adicionar_se_ausente(self, cobranca: Cobranca) -> bool:
        """Insere se ainda não houver cobrança para a mesma `compra_id`.

        Devolve False quando outra transação já gravou a cobrança dessa compra (a unicidade
        de `compra_id` no banco decide a corrida entre duas tentativas do mesmo passo).
        """
        ...

    def por_compra(self, compra_id: UUID, *, travar: bool = False) -> Cobranca | None: ...

    def por_codigo(self, codigo: str, *, travar: bool = False) -> Cobranca | None: ...

    def salvar(self, cobranca: Cobranca) -> None:
        """Grava o estado atual da cobrança (lida antes com `travar=True`)."""
        ...
