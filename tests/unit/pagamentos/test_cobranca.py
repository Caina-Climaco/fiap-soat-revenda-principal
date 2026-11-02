"""Agregado Cobranca: árbitro do pivô da saga (ADR-009)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from principal.pagamentos.domain.cobranca import (
    Cobranca,
    CodigoPagamento,
    OrigemCancelamento,
    StatusCobranca,
)
from principal.pagamentos.domain.erros import (
    CobrancaJaPagaError,
    CodigoPagamentoEmUsoError,
    PagamentoAposCancelamentoError,
    PagamentosError,
    ValorInvalidoError,
)

AGORA = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def nova() -> Cobranca:
    return Cobranca.gerar(uuid4(), Decimal("119900.00"), AGORA)


def test_gerar_cria_pendente_com_codigo_no_formato() -> None:
    cobranca = nova()
    assert cobranca.status is StatusCobranca.PENDENTE
    assert CodigoPagamento.FORMATO.match(str(cobranca.codigo))
    assert [e.nome for e in cobranca.coletar_eventos()] == ["CobrancaGerada"]


def test_sortear_novo_codigo_troca_o_codigo_no_mesmo_formato() -> None:
    cobranca = nova()
    anterior = cobranca.codigo
    cobranca.sortear_novo_codigo()
    assert cobranca.codigo != anterior
    assert CodigoPagamento.FORMATO.match(str(cobranca.codigo))


def test_codigo_em_uso_nao_e_erro_de_negocio() -> None:
    """Colisão repetida é defeito (500), e não uma regra de Pagamentos."""
    compra_id = uuid4()
    erro = CodigoPagamentoEmUsoError(compra_id)
    assert not isinstance(erro, PagamentosError)
    assert erro.compra_id == compra_id
    assert str(compra_id) in str(erro)


def test_codigos_sao_aleatorios_e_formato_e_validado() -> None:
    assert len({str(CodigoPagamento.gerar()) for _ in range(200)}) == 200
    with pytest.raises(ValueError, match="formato"):
        CodigoPagamento("PAG-XYZ")


@pytest.mark.parametrize("valor", ["0", "-1", "10.001", "NaN"])
def test_valor_invalido(valor: str) -> None:
    with pytest.raises(ValorInvalidoError):
        Cobranca.gerar(uuid4(), Decimal(valor), AGORA)


def test_pagamento_e_pivo_e_repeticao_nao_tem_efeito() -> None:
    cobranca = nova()
    cobranca.coletar_eventos()
    assert cobranca.registrar_pagamento(AGORA) is True
    assert cobranca.status is StatusCobranca.PAGA
    assert cobranca.registrar_pagamento(AGORA) is False
    with pytest.raises(CobrancaJaPagaError):
        cobranca.registrar_cancelamento(OrigemCancelamento.SAGA, AGORA)
    assert [e.nome for e in cobranca.coletar_eventos()] == ["CobrancaPaga"]


@pytest.mark.parametrize("origem", list(OrigemCancelamento))
def test_cancelamento_e_idempotente_e_guarda_a_origem(origem: OrigemCancelamento) -> None:
    cobranca = nova()
    cobranca.coletar_eventos()
    assert cobranca.registrar_cancelamento(origem, AGORA) is True
    assert cobranca.origem_cancelamento is origem
    assert cobranca.registrar_cancelamento(OrigemCancelamento.PROCESSADOR, AGORA) is False
    evento = cobranca.coletar_eventos()[0]
    assert evento.como_dict()["origem"] == origem.value


def test_pagamento_apos_cancelamento_e_registrado_uma_vez() -> None:
    cobranca = nova()
    cobranca.registrar_cancelamento(OrigemCancelamento.SAGA, AGORA)
    cobranca.coletar_eventos()
    for _ in range(2):
        with pytest.raises(PagamentoAposCancelamentoError):
            cobranca.registrar_pagamento(AGORA)
    assert cobranca.pagamento_apos_cancelamento is True
    assert cobranca.status is StatusCobranca.CANCELADA
    assert [e.nome for e in cobranca.coletar_eventos()] == ["PagamentoAposCancelamento"]


def test_marca_de_compensacao() -> None:
    marca = Cobranca.marca_de_compensacao(uuid4(), AGORA)
    assert marca.compensada_pela_saga
    assert marca.valor is None
    assert not nova().compensada_pela_saga
