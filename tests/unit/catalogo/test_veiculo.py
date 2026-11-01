"""Agregado Veiculo: cadastro, validação (RN-15), edição (RN-02) e passos da saga."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from principal.catalogo.domain.erros import (
    BloqueioDeOutraCompraError,
    DadosVeiculoInvalidosError,
    VeiculoIndisponivelError,
    VeiculoJaVendidoError,
    VeiculoNaoEditavelError,
    VersaoDivergenteError,
)
from principal.catalogo.domain.veiculo import StatusVeiculo, Veiculo, validar_dados

AGORA = datetime(2026, 10, 3, 13, 0, tzinfo=UTC)


def novo(**kwargs: object) -> Veiculo:
    dados: dict[str, object] = {
        "marca": "Toyota",
        "modelo": "Corolla XEi 2.0",
        "ano": 2022,
        "cor": "Prata",
        "preco": "124900.00",
        "agora": AGORA,
    }
    dados.update(kwargs)
    return Veiculo.cadastrar(**dados)  # type: ignore[arg-type]


def test_cadastro_nasce_a_venda_com_versao_1_e_evento() -> None:
    veiculo = novo(marca="  Toyota ")
    assert veiculo.status is StatusVeiculo.A_VENDA
    assert veiculo.versao == 1
    assert veiculo.marca == "Toyota"
    assert veiculo.preco == Decimal("124900.00")
    assert veiculo.criado_em == veiculo.atualizado_em == AGORA
    eventos = veiculo.coletar_eventos()
    assert [e.nome for e in eventos] == ["VeiculoCadastrado"]
    assert eventos[0].como_dict()["preco"] == "124900.00"
    assert veiculo.coletar_eventos() == []


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("ano", 1949, "deve estar entre 1950 e 2027"),
        ("ano", 2028, "deve estar entre 1950 e 2027"),
        ("ano", True, "deve ser um número inteiro"),
        ("ano", "2020", "deve ser um número inteiro"),
        ("preco", "0.00", "deve ser maior que zero"),
        ("preco", "-1", "deve ser maior que zero"),
        ("preco", "10.123", "deve ter no máximo 2 casas decimais"),
        ("preco", "abc", "deve ser um valor decimal"),
        ("preco", "NaN", "deve ser um valor decimal"),
        ("preco", 1.5, "deve ser um valor decimal"),
        ("preco", "10000000000.00", "deve ser no máximo 9999999999.99"),
        ("marca", "   ", "não pode ser vazio"),
        ("marca", "x" * 61, "deve ter no máximo 60 caracteres"),
        ("cor", "y" * 31, "deve ter no máximo 30 caracteres"),
        ("modelo", 123, "deve ser texto"),
    ],
)
def test_validacao_rejeita_dados_fora_das_regras(campo: str, valor: object, mensagem: str) -> None:
    with pytest.raises(DadosVeiculoInvalidosError) as erro:
        novo(**{campo: valor})
    assert (campo, mensagem) in erro.value.erros


def test_validacao_acumula_todos_os_erros() -> None:
    with pytest.raises(DadosVeiculoInvalidosError) as erro:
        novo(ano=1900, preco="0", cor="")
    assert {c for c, _ in erro.value.erros} == {"ano", "preco", "cor"}


def test_ano_seguinte_e_aceito_e_preco_inteiro_normalizado() -> None:
    veiculo = novo(ano=2027, preco=50000)
    assert veiculo.ano == 2027
    assert str(veiculo.preco) == "50000.00"


def test_cadastro_sem_campo_obrigatorio() -> None:
    with pytest.raises(DadosVeiculoInvalidosError) as erro:
        novo(cor=None)
    assert erro.value.erros == [("cor", "campo obrigatório")]


def test_edicao_parcial_incrementa_versao() -> None:
    veiculo = novo()
    depois = AGORA + timedelta(minutes=5)
    veiculo.editar(agora=depois, preco="119900.00", cor=" Preto ")
    assert veiculo.preco == Decimal("119900.00")
    assert veiculo.cor == "Preto"
    assert veiculo.marca == "Toyota"
    assert veiculo.versao == 2
    assert veiculo.atualizado_em == depois
    evento = veiculo.coletar_eventos()[-1]
    assert evento.nome == "VeiculoEditado"
    assert evento.como_dict()["campos"] == "cor,preco"


def test_edicao_sem_campos_e_invalida() -> None:
    with pytest.raises(DadosVeiculoInvalidosError):
        novo().editar(agora=AGORA)


COMPRA = uuid4()
OUTRA = uuid4()


def test_bloqueio_impede_edicao_e_nao_muda_a_versao() -> None:
    veiculo = novo(preco="95000.00")
    veiculo.coletar_eventos()
    assert veiculo.bloquear(COMPRA, 1, AGORA) is True
    assert (veiculo.status, veiculo.compra_id, veiculo.versao) == (
        StatusVeiculo.BLOQUEADO,
        COMPRA,
        1,
    )
    with pytest.raises(VeiculoNaoEditavelError) as erro:
        veiculo.editar(agora=AGORA, preco="90000.00")
    assert veiculo.preco == Decimal("95000.00")
    assert "BLOQUEADO" in str(erro.value)
    evento = veiculo.coletar_eventos()[0]
    assert evento.nome == "VeiculoBloqueado"
    assert evento.como_dict()["compra_id"] == str(COMPRA)


def test_bloqueio_repetido_pela_mesma_compra_e_idempotente() -> None:
    veiculo = novo()
    veiculo.bloquear(COMPRA, 1, AGORA)
    veiculo.coletar_eventos()
    assert veiculo.bloquear(COMPRA, 1, AGORA) is False
    assert veiculo.coletar_eventos() == []


def test_bloqueio_por_outra_compra_e_recusado() -> None:
    veiculo = novo()
    veiculo.bloquear(COMPRA, 1, AGORA)
    with pytest.raises(VeiculoIndisponivelError) as erro:
        veiculo.bloquear(OUTRA, 1, AGORA)
    assert erro.value.status is StatusVeiculo.BLOQUEADO


def test_bloqueio_com_versao_antiga_e_recusado() -> None:
    """RN-12: o comprador viu a versão 1, mas o gestor já editou (versão 2)."""
    veiculo = novo()
    veiculo.editar(agora=AGORA, preco="1.00")
    with pytest.raises(VersaoDivergenteError) as erro:
        veiculo.bloquear(COMPRA, 1, AGORA)
    assert (erro.value.informada, erro.value.atual) == (1, 2)
    assert veiculo.status is StatusVeiculo.A_VENDA


def test_desbloqueio_devolve_a_venda_e_e_idempotente() -> None:
    veiculo = novo()
    veiculo.bloquear(COMPRA, 1, AGORA)
    assert veiculo.desbloquear(COMPRA, AGORA) is True
    assert (veiculo.status, veiculo.compra_id) == (StatusVeiculo.A_VENDA, None)
    assert veiculo.desbloquear(COMPRA, AGORA) is False
    veiculo.editar(agora=AGORA, preco="1.00")  # editável de novo
    assert veiculo.versao == 2


def test_desbloqueio_de_outra_compra_nao_tem_efeito() -> None:
    veiculo = novo()
    veiculo.bloquear(COMPRA, 1, AGORA)
    assert veiculo.desbloquear(OUTRA, AGORA) is False
    assert veiculo.status is StatusVeiculo.BLOQUEADO


def test_venda_e_estado_final() -> None:
    veiculo = novo()
    veiculo.bloquear(COMPRA, 1, AGORA)
    assert veiculo.marcar_vendido(COMPRA, AGORA) is True
    assert veiculo.status is StatusVeiculo.VENDIDO
    assert veiculo.marcar_vendido(COMPRA, AGORA) is False  # T6 repetido
    assert veiculo.bloquear(COMPRA, 1, AGORA) is False  # T2 atrasado da mesma compra
    with pytest.raises(VeiculoJaVendidoError):
        veiculo.desbloquear(COMPRA, AGORA)
    with pytest.raises(VeiculoIndisponivelError):
        veiculo.bloquear(OUTRA, 1, AGORA)
    with pytest.raises(VeiculoNaoEditavelError):
        veiculo.editar(agora=AGORA, preco="1.00")
    nomes = [e.nome for e in veiculo.coletar_eventos()]
    assert nomes == ["VeiculoCadastrado", "VeiculoBloqueado", "VeiculoVendido"]


@pytest.mark.parametrize("bloquear_antes", [False, True])
def test_venda_exige_o_bloqueio_da_mesma_compra(bloquear_antes: bool) -> None:
    veiculo = novo()
    if bloquear_antes:
        veiculo.bloquear(OUTRA, 1, AGORA)
    with pytest.raises(BloqueioDeOutraCompraError):
        veiculo.marcar_vendido(COMPRA, AGORA)


def test_validar_dados_ignora_campos_ausentes() -> None:
    assert validar_dados(ano_corrente=2026).informados() == {}


@pytest.mark.parametrize("campo", ["marca", "modelo", "cor"])
@pytest.mark.parametrize(
    "valor",
    ["a\x00b", "a\x1fb", "a\tb", "a\nb", "Fiat\n", "\x1cFiat", "a\x7fb"],
    ids=["nul", "us", "tab", "lf-meio", "lf-fim", "fs-inicio", "del"],
)
def test_caracteres_de_controle_sao_recusados(campo: str, valor: str) -> None:
    # Antes, um NUL chegava ao PostgreSQL e virava 500; strip() escondia \x1c-\x1f nas pontas.
    with pytest.raises(DadosVeiculoInvalidosError) as erro:
        novo(**{campo: valor})
    assert erro.value.erros == [(campo, "não pode conter caracteres de controle")]
    with pytest.raises(DadosVeiculoInvalidosError):
        novo().editar(agora=AGORA, **{campo: valor})


def test_acentos_e_espacos_internos_continuam_validos() -> None:
    veiculo = novo(marca="Citroën", modelo="C4 Cactus Feel 1.6", cor="Azul Côte d'Azur")
    assert veiculo.marca == "Citroën"


def test_edicao_sem_mudanca_real_nao_gera_versao_nem_evento() -> None:
    veiculo = novo()
    veiculo.coletar_eventos()
    depois = AGORA + timedelta(minutes=5)
    alterou = veiculo.editar(agora=depois, preco="124900", cor=" Prata ", ano=2022)
    assert alterou is False
    assert veiculo.versao == 1
    assert veiculo.atualizado_em == AGORA
    assert veiculo.coletar_eventos() == []
    # Com ao menos um valor diferente, só os campos realmente alterados entram no evento.
    assert veiculo.editar(agora=depois, preco="124900.00", cor="Preto") is True
    assert veiculo.versao == 2
    assert veiculo.coletar_eventos()[-1].como_dict()["campos"] == "cor"
