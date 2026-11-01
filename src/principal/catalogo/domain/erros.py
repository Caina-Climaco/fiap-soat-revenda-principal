"""Exceções do contexto Catálogo. Mensagens em português, sem dados pessoais."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from principal.catalogo.domain.veiculo import StatusVeiculo


class CatalogoError(Exception):
    """Base das exceções de negócio do Catálogo."""


class DadosVeiculoInvalidosError(CatalogoError):
    """Campos fora das regras (RN-15). `erros` lista pares (campo, mensagem)."""

    def __init__(self, erros: Sequence[tuple[str, str]]) -> None:
        self.erros = list(erros)
        super().__init__("Um ou mais campos são inválidos.")


class VeiculoNaoEncontradoError(CatalogoError):
    def __init__(self, veiculo_id: UUID) -> None:
        self.veiculo_id = veiculo_id
        super().__init__(f"O veículo {veiculo_id} não existe.")


class VeiculoNaoEditavelError(CatalogoError):
    """Edição de veículo bloqueado por uma compra ou vendido (RN-02)."""

    def __init__(self, veiculo_id: UUID, status: StatusVeiculo) -> None:
        self.veiculo_id = veiculo_id
        self.status = status
        super().__init__(
            f"O veículo {veiculo_id} não pode ser editado (status atual: {status.value}); "
            "só veículos à venda podem ser alterados."
        )


class VeiculoIndisponivelError(CatalogoError):
    """Bloqueio pedido para veículo já bloqueado por outra compra ou vendido."""

    def __init__(self, veiculo_id: UUID, status: StatusVeiculo) -> None:
        self.veiculo_id = veiculo_id
        self.status = status
        super().__init__(
            f"O veículo {veiculo_id} não está disponível (status atual: {status.value})."
        )


class VersaoDivergenteError(CatalogoError):
    """A versão vista pelo comprador não é a atual (RN-12, releitura de valor)."""

    def __init__(self, veiculo_id: UUID, informada: int, atual: int) -> None:
        self.veiculo_id = veiculo_id
        self.informada = informada
        self.atual = atual
        super().__init__(
            f"O veículo {veiculo_id} foi alterado (versão informada {informada}, "
            f"atual {atual}); a compra precisa ser refeita com os dados atuais."
        )


class VeiculoJaVendidoError(CatalogoError):
    """Desbloqueio pedido para um veículo que essa mesma compra já vendeu (após o pivô)."""

    def __init__(self, veiculo_id: UUID) -> None:
        self.veiculo_id = veiculo_id
        super().__init__(f"O veículo {veiculo_id} já foi vendido e não pode ser desbloqueado.")


class BloqueioDeOutraCompraError(CatalogoError):
    """Venda pedida por uma compra que não detém o bloqueio do veículo."""

    def __init__(self, veiculo_id: UUID, compra_id: UUID) -> None:
        self.veiculo_id = veiculo_id
        self.compra_id = compra_id
        super().__init__(
            f"O veículo {veiculo_id} não está bloqueado pela compra {compra_id}; "
            "a venda não pode ser registrada."
        )


class CompraCompensadaError(CatalogoError):
    """Passo da saga recebido depois da compensação da mesma compra (regra contra ressurreição)."""

    def __init__(self, compra_id: UUID) -> None:
        self.compra_id = compra_id
        super().__init__(
            f"A compra {compra_id} já foi compensada; o pedido chegou atrasado e foi recusado."
        )
