"""Exceções do contexto Pagamentos."""

from __future__ import annotations

from uuid import UUID


class PagamentosError(Exception):
    """Base das exceções de negócio de Pagamentos."""


class CobrancaNaoEncontradaError(PagamentosError):
    def __init__(self, referencia: str) -> None:
        self.referencia = referencia
        super().__init__(f"Nenhuma cobrança encontrada para {referencia}.")


class CobrancaJaPagaError(PagamentosError):
    """Cancelamento pedido para cobrança já paga: o pivô da saga já aconteceu."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(f"A cobrança {codigo} já foi paga e não pode ser cancelada.")


class PagamentoAposCancelamentoError(PagamentosError):
    """Pagamento informado para cobrança já cancelada (RN-10: registrar para estorno)."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(
            f"A cobrança {codigo} já estava cancelada quando o pagamento foi informado; "
            "a ocorrência foi registrada para estorno."
        )


class CompraCompensadaError(PagamentosError):
    """Geração de cobrança pedida depois da compensação da mesma compra."""

    def __init__(self, compra_id: UUID) -> None:
        self.compra_id = compra_id
        super().__init__(
            f"A compra {compra_id} já foi compensada; o pedido chegou atrasado e foi recusado."
        )


class ValorInvalidoError(PagamentosError):
    def __init__(self) -> None:
        self.erros = [("valor", "deve ser maior que zero, com até 2 casas decimais")]
        super().__init__("O valor da cobrança é inválido.")
