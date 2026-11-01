"""Porta de persistência do agregado Veiculo (implementada em catalogo/infrastructure)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from principal.catalogo.domain.veiculo import StatusVeiculo, Veiculo


class VeiculoRepository(Protocol):
    def adicionar(self, veiculo: Veiculo) -> None: ...

    def obter(self, veiculo_id: UUID, *, travar: bool = False) -> Veiculo | None:
        """Lê o veículo; com `travar=True`, trava a linha até o fim da transação.

        Toda alteração passa por uma leitura travada: duas operações concorrentes sobre o
        mesmo veículo (edição e bloqueio, dois bloqueios) ficam em fila no banco.
        """
        ...

    def salvar(self, veiculo: Veiculo) -> None:
        """Grava o estado atual do veículo (lido antes com `travar=True`)."""
        ...

    def listar(
        self, status: StatusVeiculo | None, *, limite: int, deslocamento: int
    ) -> tuple[Sequence[Veiculo], int]:
        """Página do mais recente ao mais antigo (visão da loja) e o total do filtro."""
        ...

    def compensacao_registrada(self, compra_id: UUID) -> bool:
        """True se a compra já teve o bloqueio compensado (regra contra ressurreição)."""
        ...

    def registrar_compensacao(self, compra_id: UUID, veiculo_id: UUID, agora: datetime) -> None:
        """Marca a compra como compensada; repetir não tem efeito."""
        ...
