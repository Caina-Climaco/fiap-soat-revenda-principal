"""Casos de uso de Pagamentos com repositório em memória."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from apoio.cenario import AS_10H, PRECO
from apoio.fakes import CobrancaRepoMemoria, OutboxFalsa, UowFalsa
from principal.pagamentos.application.casos_uso import (
    CancelarCobrancaDaCompra,
    GerarCobranca,
    RegistrarResultadoPagamento,
)
from principal.pagamentos.domain.cobranca import Cobranca, OrigemCancelamento, StatusCobranca
from principal.pagamentos.domain.erros import (
    CobrancaJaPagaError,
    CobrancaNaoEncontradaError,
    CompraCompensadaError,
    PagamentoAposCancelamentoError,
)
from principal.shared.clock import RelogioFixo


class Cenario:
    def __init__(self) -> None:
        self.repo = CobrancaRepoMemoria()
        self.uow = UowFalsa()
        self.outbox = OutboxFalsa()
        argumentos = (self.repo, self.uow, RelogioFixo(AS_10H), self.outbox)
        self.gerar = GerarCobranca(*argumentos)
        self.cancelar = CancelarCobrancaDaCompra(*argumentos)
        self.resultado = RegistrarResultadoPagamento(*argumentos)

    def cobranca(self, compra_id: UUID | None = None) -> Cobranca:
        return self.gerar.executar(compra_id or uuid4(), PRECO).cobranca


@pytest.fixture
def cenario() -> Cenario:
    return Cenario()


def test_gerar_e_idempotente_pela_compra(cenario: Cenario) -> None:
    compra = uuid4()
    primeira = cenario.gerar.executar(compra, PRECO)
    segunda = cenario.gerar.executar(compra, Decimal("1.00"))
    assert primeira.efeito is True
    assert segunda.efeito is False
    assert segunda.cobranca.codigo == primeira.cobranca.codigo
    assert cenario.uow.nomes == ["CobrancaGerada"]


def test_gerar_perde_a_corrida_e_devolve_a_cobranca_da_outra_tentativa(cenario: Cenario) -> None:
    compra = uuid4()
    outra = Cobranca.gerar(compra, PRECO, AS_10H)
    cenario.repo.concorrente = outra
    resultado = cenario.gerar.executar(compra, PRECO)
    assert resultado.efeito is False
    assert resultado.cobranca.codigo == outra.codigo


def test_gerar_depois_da_compensacao_e_recusado(cenario: Cenario) -> None:
    compra = uuid4()
    marca = cenario.cancelar.executar(compra)
    assert marca.efeito is True
    assert marca.cobranca.compensada_pela_saga
    with pytest.raises(CompraCompensadaError):
        cenario.gerar.executar(compra, PRECO)


def test_cancelar_pela_saga_e_idempotente(cenario: Cenario) -> None:
    cobranca = cenario.cobranca()
    assert cenario.cancelar.executar(cobranca.compra_id).efeito is True
    assert cenario.cancelar.executar(cobranca.compra_id).efeito is False
    assert cenario.repo.dados[cobranca.compra_id].origem_cancelamento is OrigemCancelamento.SAGA
    assert cenario.outbox.mensagens == []  # a saga já sabe; não há repasse


def test_cancelar_pela_saga_depois_do_pivo(cenario: Cenario) -> None:
    cobranca = cenario.cobranca()
    cenario.resultado.executar(str(cobranca.codigo), pago=True)
    with pytest.raises(CobrancaJaPagaError):
        cenario.cancelar.executar(cobranca.compra_id)


def test_cancelar_perde_a_corrida_para_t3(cenario: Cenario) -> None:
    compra = uuid4()
    cenario.repo.concorrente = Cobranca.gerar(compra, PRECO, AS_10H)
    resultado = cenario.cancelar.executar(compra)
    assert resultado.efeito is True
    assert resultado.cobranca.status is StatusCobranca.CANCELADA
    assert resultado.cobranca.valor == PRECO


@pytest.mark.parametrize(("pago", "status"), [(True, "PAGO"), (False, "CANCELADO")])
def test_webhook_aplica_e_repassa_pela_outbox(cenario: Cenario, pago: bool, status: str) -> None:
    cobranca = cenario.cobranca()
    resultado = cenario.resultado.executar(str(cobranca.codigo), pago=pago)
    assert resultado.efeito is True
    [mensagem] = cenario.outbox.mensagens
    assert mensagem.tipo == "RESULTADO_PAGAMENTO"
    assert mensagem.caminho == f"/internal/v1/compras/{cobranca.compra_id}/resultado-pagamento"
    assert mensagem.corpo == {"codigo_pagamento": str(cobranca.codigo), "status": status}
    repetido = cenario.resultado.executar(str(cobranca.codigo), pago=pago)
    assert repetido.efeito is False
    assert len(cenario.outbox.mensagens) == 1


def test_webhook_pago_depois_do_cancelamento_registra_ocorrencia(cenario: Cenario) -> None:
    cobranca = cenario.cobranca()
    cenario.cancelar.executar(cobranca.compra_id)
    with pytest.raises(PagamentoAposCancelamentoError):
        cenario.resultado.executar(str(cobranca.codigo), pago=True)
    assert cenario.repo.dados[cobranca.compra_id].pagamento_apos_cancelamento is True
    assert cenario.uow.nomes[-1] == "PagamentoAposCancelamento"
    assert cenario.outbox.mensagens == []


def test_webhook_com_codigo_desconhecido(cenario: Cenario) -> None:
    with pytest.raises(CobrancaNaoEncontradaError):
        cenario.resultado.executar("PAG-000000000000", pago=True)
