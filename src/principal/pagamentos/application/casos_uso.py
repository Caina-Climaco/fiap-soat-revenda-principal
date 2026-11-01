"""Casos de uso de Pagamentos: passos da saga (T3, C3) e o webhook do processador (T4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from principal.pagamentos.domain.cobranca import Cobranca, OrigemCancelamento, StatusCobranca
from principal.pagamentos.domain.erros import (
    CobrancaNaoEncontradaError,
    CompraCompensadaError,
    PagamentoAposCancelamentoError,
)
from principal.pagamentos.domain.repositorio import CobrancaRepository
from principal.shared.clock import Clock
from principal.shared.outbox import MensagemSaida, Outbox
from principal.shared.uow import UnidadeDeTrabalho

TIPO_RESULTADO_PAGAMENTO = "RESULTADO_PAGAMENTO"


@dataclass(frozen=True, slots=True)
class ResultadoCobranca:
    """Cobrança após a operação e se a operação teve efeito (False = repetição)."""

    cobranca: Cobranca
    efeito: bool


def mensagem_resultado(cobranca: Cobranca) -> MensagemSaida:
    """Repasse do resultado ao serviço de venda (docs/05-api.md, seção 5.7)."""
    status = "PAGO" if cobranca.status is StatusCobranca.PAGA else "CANCELADO"
    return MensagemSaida(
        tipo=TIPO_RESULTADO_PAGAMENTO,
        chave=f"compra:{cobranca.compra_id}",
        metodo="POST",
        caminho=f"/internal/v1/compras/{cobranca.compra_id}/resultado-pagamento",
        corpo={"codigo_pagamento": str(cobranca.codigo), "status": status},
    )


class _CasoDeUso:
    def __init__(
        self, repo: CobrancaRepository, uow: UnidadeDeTrabalho, relogio: Clock, outbox: Outbox
    ) -> None:
        self._repo = repo
        self._uow = uow
        self._relogio = relogio
        self._outbox = outbox

    def _confirmar(self, cobranca: Cobranca) -> None:
        self._uow.registrar_eventos(cobranca.coletar_eventos())
        self._uow.confirmar()


class GerarCobranca(_CasoDeUso):
    """T3 da saga: uma cobrança por compra; repetir devolve a mesma cobrança."""

    def executar(self, compra_id: UUID, valor: Decimal) -> ResultadoCobranca:
        existente = self._existente(compra_id)
        if existente is not None:
            return ResultadoCobranca(existente, efeito=False)
        nova = Cobranca.gerar(compra_id, valor, self._relogio.agora())
        if self._repo.adicionar_se_ausente(nova):
            self._confirmar(nova)
            return ResultadoCobranca(nova, efeito=True)
        # Outra tentativa do mesmo passo gravou primeiro: devolve a dela.
        existente = self._existente(compra_id)
        if existente is None:  # pragma: no cover - a unicidade garante que ela existe
            raise CobrancaNaoEncontradaError(f"a compra {compra_id}")
        return ResultadoCobranca(existente, efeito=False)

    def _existente(self, compra_id: UUID) -> Cobranca | None:
        existente = self._repo.por_compra(compra_id)
        if existente is not None and existente.compensada_pela_saga:
            raise CompraCompensadaError(compra_id)
        return existente


class CancelarCobrancaDaCompra(_CasoDeUso):
    """C3 da saga. Se a cobrança já foi paga, o pivô aconteceu: CobrancaJaPagaError."""

    def executar(self, compra_id: UUID) -> ResultadoCobranca:
        agora = self._relogio.agora()
        cobranca = self._repo.por_compra(compra_id, travar=True)
        if cobranca is None:
            # T3 nunca chegou (ou ainda está a caminho): a marca impede que ele gere depois.
            marca = Cobranca.marca_de_compensacao(compra_id, agora)
            if self._repo.adicionar_se_ausente(marca):
                self._confirmar(marca)
                return ResultadoCobranca(marca, efeito=True)
            cobranca = self._repo.por_compra(compra_id, travar=True)
            if cobranca is None:  # pragma: no cover - a unicidade garante que ela existe
                raise CobrancaNaoEncontradaError(f"a compra {compra_id}")
        efeito = cobranca.registrar_cancelamento(OrigemCancelamento.SAGA, agora)
        if efeito:
            self._repo.salvar(cobranca)
            self._confirmar(cobranca)
        return ResultadoCobranca(cobranca, efeito)


class RegistrarResultadoPagamento(_CasoDeUso):
    """Webhook do processador (ACL): PAGO é o pivô da saga; CANCELADO a encerra."""

    def executar(self, codigo: str, *, pago: bool) -> ResultadoCobranca:
        cobranca = self._repo.por_codigo(codigo, travar=True)
        if cobranca is None:
            raise CobrancaNaoEncontradaError(f"o código {codigo}")
        agora = self._relogio.agora()
        try:
            if pago:
                efeito = cobranca.registrar_pagamento(agora)
            else:
                efeito = cobranca.registrar_cancelamento(OrigemCancelamento.PROCESSADOR, agora)
        except PagamentoAposCancelamentoError:
            # A ocorrência fica gravada para o estorno antes de responder 409 (RN-10).
            self._repo.salvar(cobranca)
            self._confirmar(cobranca)
            raise
        if efeito:
            self._repo.salvar(cobranca)
            # Mesma transação: o resultado e o repasse ao serviço de venda (ADR-003).
            self._outbox.registrar(mensagem_resultado(cobranca))
            self._confirmar(cobranca)
        return ResultadoCobranca(cobranca, efeito)


@dataclass(frozen=True, slots=True)
class CasosUsoPagamentos:
    gerar: GerarCobranca
    cancelar: CancelarCobrancaDaCompra
    registrar_resultado: RegistrarResultadoPagamento


FabricaCasosUsoPagamentos = Callable[..., CasosUsoPagamentos]
