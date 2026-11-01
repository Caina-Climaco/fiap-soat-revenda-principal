"""Casos de uso do Catálogo com repositório em memória: outbox, trava e passos da saga."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from apoio.cenario import AS_10H
from apoio.fakes import OutboxFalsa, UowFalsa, VeiculoRepoMemoria
from principal.catalogo.application.casos_uso import (
    BloquearVeiculo,
    CadastrarVeiculo,
    DadosCadastro,
    DadosEdicao,
    DesbloquearVeiculo,
    EditarVeiculo,
    ListarVeiculos,
    ObterVeiculo,
    VenderVeiculo,
)
from principal.catalogo.domain.erros import CompraCompensadaError, VeiculoNaoEncontradoError
from principal.catalogo.domain.veiculo import StatusVeiculo, Veiculo
from principal.shared.clock import RelogioFixo


class Cenario:
    def __init__(self) -> None:
        self.repo = VeiculoRepoMemoria()
        self.uow = UowFalsa()
        self.outbox = OutboxFalsa()
        self.relogio = RelogioFixo(AS_10H)

    def caso(self, classe: type) -> object:
        return classe(self.repo, self.uow, self.relogio, self.outbox)

    def veiculo(self) -> Veiculo:
        caso = self.caso(CadastrarVeiculo)
        assert isinstance(caso, CadastrarVeiculo)
        return caso.executar(
            DadosCadastro(
                marca="Fiat", modelo="Argo", ano=2022, cor="Prata", preco=Decimal("72000")
            )
        )


@pytest.fixture
def cenario() -> Cenario:
    return Cenario()


def test_cadastro_grava_veiculo_e_anuncio_na_outbox(cenario: Cenario) -> None:
    veiculo = cenario.veiculo()
    assert cenario.repo.dados[veiculo.id].status is StatusVeiculo.A_VENDA
    [mensagem] = cenario.outbox.mensagens
    assert mensagem.tipo == "ANUNCIO"
    assert (mensagem.metodo, mensagem.caminho) == ("PUT", f"/internal/v1/anuncios/{veiculo.id}")
    assert mensagem.chave == f"veiculo:{veiculo.id}"
    assert mensagem.corpo["versao"] == 1
    assert mensagem.corpo["preco"] == "72000.00"
    assert cenario.uow.nomes == ["VeiculoCadastrado"]


def test_edicao_publica_nova_versao_e_sem_mudanca_nao_publica(cenario: Cenario) -> None:
    veiculo = cenario.veiculo()
    editar = EditarVeiculo(cenario.repo, cenario.uow, cenario.relogio, cenario.outbox)
    editado = editar.executar(veiculo.id, DadosEdicao(preco=Decimal("70000.00")))
    assert editado.versao == 2
    assert cenario.outbox.mensagens[-1].corpo["versao"] == 2
    assert cenario.repo.travados == [veiculo.id]
    editar.executar(veiculo.id, DadosEdicao(preco=Decimal("70000.00")))
    assert len(cenario.outbox.mensagens) == 2  # cadastro + uma edição real


def test_operacoes_em_veiculo_inexistente(cenario: Cenario) -> None:
    inexistente = uuid4()
    with pytest.raises(VeiculoNaoEncontradoError):
        ObterVeiculo(cenario.repo).executar(inexistente)
    with pytest.raises(VeiculoNaoEncontradoError):
        EditarVeiculo(cenario.repo, cenario.uow, cenario.relogio, cenario.outbox).executar(
            inexistente, DadosEdicao(cor="Azul")
        )


def test_listagem_filtra_por_status(cenario: Cenario) -> None:
    a = cenario.veiculo()
    cenario.veiculo()
    BloquearVeiculo(cenario.repo, cenario.uow, cenario.relogio, cenario.outbox).executar(
        a.id, uuid4(), 1
    )
    listar = ListarVeiculos(cenario.repo)
    assert listar.executar(status=None, limite=10, deslocamento=0).total == 2
    bloqueados = listar.executar(status=StatusVeiculo.BLOQUEADO, limite=10, deslocamento=0)
    assert [v.id for v in bloqueados.itens] == [a.id]


def test_saga_feliz_bloqueio_e_venda_com_repeticoes(cenario: Cenario) -> None:
    veiculo = cenario.veiculo()
    compra = uuid4()
    argumentos = (cenario.repo, cenario.uow, cenario.relogio, cenario.outbox)
    bloquear = BloquearVeiculo(*argumentos)
    vender = VenderVeiculo(*argumentos)
    assert bloquear.executar(veiculo.id, compra, 1).efeito is True
    assert bloquear.executar(veiculo.id, compra, 1).efeito is False
    assert vender.executar(veiculo.id, compra).efeito is True
    assert vender.executar(veiculo.id, compra).efeito is False
    assert cenario.repo.dados[veiculo.id].status is StatusVeiculo.VENDIDO
    # Passos da saga não publicam anúncio (a disponibilidade é do serviço de venda).
    assert len(cenario.outbox.mensagens) == 1
    assert cenario.uow.nomes == ["VeiculoCadastrado", "VeiculoBloqueado", "VeiculoVendido"]


def test_compensacao_impede_bloqueio_atrasado(cenario: Cenario) -> None:
    """Regra contra ressurreição: C2 antes de T2 marca a compra; o T2 atrasado é recusado."""
    veiculo = cenario.veiculo()
    compra = uuid4()
    argumentos = (cenario.repo, cenario.uow, cenario.relogio, cenario.outbox)
    resultado = DesbloquearVeiculo(*argumentos).executar(veiculo.id, compra)
    assert resultado.efeito is False
    assert compra in cenario.repo.compensacoes
    with pytest.raises(CompraCompensadaError):
        BloquearVeiculo(*argumentos).executar(veiculo.id, compra, 1)
    assert cenario.repo.dados[veiculo.id].status is StatusVeiculo.A_VENDA


def test_compensacao_desfaz_bloqueio(cenario: Cenario) -> None:
    veiculo = cenario.veiculo()
    compra = uuid4()
    argumentos = (cenario.repo, cenario.uow, cenario.relogio, cenario.outbox)
    BloquearVeiculo(*argumentos).executar(veiculo.id, compra, 1)
    resultado = DesbloquearVeiculo(*argumentos).executar(veiculo.id, compra)
    assert resultado.efeito is True
    assert resultado.veiculo.status is StatusVeiculo.A_VENDA
    assert cenario.uow.nomes[-1] == "VeiculoDesbloqueado"
