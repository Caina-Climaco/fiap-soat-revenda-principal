"""Repositório de cobranças contra PostgreSQL real: colisão do código de pagamento."""

from __future__ import annotations

from uuid import uuid4

import pytest

from apoio.cenario import AS_10H, PRECO
from principal.pagamentos.domain.cobranca import Cobranca, CodigoPagamento
from principal.pagamentos.infrastructure.repositorio_sql import (
    CodigoPagamentoEmUsoError,
    SqlCobrancaRepository,
)
from principal.shared.db import BancoDeDados

REPETIDO = CodigoPagamento("PAG-000000000001")


def _com_codigo(codigo: CodigoPagamento) -> Cobranca:
    cobranca = Cobranca.gerar(uuid4(), PRECO, AS_10H)
    cobranca.codigo = codigo
    return cobranca


def test_codigo_repetido_e_sorteado_de_novo(banco: BancoDeDados) -> None:
    """Item 4.5 da revisão: colisão em uq_cobrancas_codigo não vira erro 500."""
    with banco.nova_sessao() as sessao:
        repo = SqlCobrancaRepository(sessao)
        assert repo.adicionar_se_ausente(_com_codigo(REPETIDO)) is True
        nova = _com_codigo(REPETIDO)
        assert repo.adicionar_se_ausente(nova) is True
        assert nova.codigo != REPETIDO
        gravada = repo.por_compra(nova.compra_id)
        assert gravada is not None
        assert gravada.codigo == nova.codigo
        # A mesma compra de novo continua idempotente (sem trocar o código).
        assert repo.adicionar_se_ausente(Cobranca.gerar(nova.compra_id, PRECO, AS_10H)) is False


def test_colisao_em_todas_as_tentativas_e_erro(
    banco: BancoDeDados, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(CodigoPagamento, "gerar", classmethod(lambda _cls: REPETIDO))
    with banco.nova_sessao() as sessao:
        repo = SqlCobrancaRepository(sessao)
        assert repo.adicionar_se_ausente(_com_codigo(REPETIDO)) is True
        with pytest.raises(CodigoPagamentoEmUsoError):
            repo.adicionar_se_ausente(_com_codigo(REPETIDO))
