"""Rotas internas chamadas pela saga do serviço de venda (docs/05-api.md, seção 5.6)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from apoio.api import Api, tipo_problema


@pytest.mark.parametrize("token", [None, "errado"])
def test_rotas_internas_exigem_token_de_servico(
    cliente_http: TestClient, token: str | None
) -> None:
    cabecalhos = {} if token is None else {"X-Service-Token": token}
    veiculo, compra = uuid.uuid4(), uuid.uuid4()
    for metodo, caminho in [
        ("POST", f"/internal/v1/veiculos/{veiculo}/bloqueio"),
        ("DELETE", f"/internal/v1/veiculos/{veiculo}/bloqueio/{compra}"),
        ("POST", f"/internal/v1/veiculos/{veiculo}/venda"),
        ("POST", "/internal/v1/cobrancas"),
        ("POST", f"/internal/v1/cobrancas/{compra}/cancelamento"),
    ]:
        resposta = cliente_http.request(metodo, caminho, json={}, headers=cabecalhos)
        assert resposta.status_code == 401, (metodo, caminho)
        assert tipo_problema(resposta) == "servico-nao-autorizado"


def test_t2_bloqueio_idempotente_e_disputa(api: Api) -> None:
    veiculo = api.cadastrar()
    compra, outra = str(uuid.uuid4()), str(uuid.uuid4())
    primeira = api.bloquear(veiculo["id"], compra)
    assert primeira.status_code == 200
    assert primeira.json() == {
        "veiculo_id": veiculo["id"],
        "status": "BLOQUEADO",
        "versao": 1,
        "compra_id": compra,
        "efeito": True,
    }
    assert api.bloquear(veiculo["id"], compra).json()["efeito"] is False
    disputa = api.bloquear(veiculo["id"], outra)
    assert disputa.status_code == 409
    assert tipo_problema(disputa) == "veiculo-indisponivel"


def test_t2_com_versao_desatualizada(api: Api) -> None:
    """RN-12: o comprador viu a versão 1; o gestor editou antes do bloqueio."""
    veiculo = api.cadastrar()
    api.editar(veiculo["id"], preco="99000.00")
    resposta = api.bloquear(veiculo["id"], str(uuid.uuid4()), versao=1)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "versao-divergente"
    assert api.veiculo(veiculo["id"])["status"] == "A_VENDA"


def test_t2_validacao_e_veiculo_inexistente(api: Api) -> None:
    veiculo = api.cadastrar()
    invalido = api.http.post(
        f"/internal/v1/veiculos/{veiculo['id']}/bloqueio",
        json={"compra_id": str(uuid.uuid4()), "versao": "1"},
        headers={"X-Service-Token": "token-de-entrada-0123456789"},
    )
    assert invalido.status_code == 422  # versão precisa ser inteiro JSON
    assert api.bloquear(str(uuid.uuid4()), str(uuid.uuid4())).status_code == 404


def test_c2_desbloqueia_e_impede_t2_atrasado(api: Api) -> None:
    veiculo = api.cadastrar()
    compra = str(uuid.uuid4())
    api.bloquear(veiculo["id"], compra)
    resposta = api.desbloquear(veiculo["id"], compra)
    assert resposta.status_code == 200
    assert (resposta.json()["status"], resposta.json()["efeito"]) == ("A_VENDA", True)
    assert api.desbloquear(veiculo["id"], compra).json()["efeito"] is False
    atrasado = api.bloquear(veiculo["id"], compra)
    assert atrasado.status_code == 409
    assert tipo_problema(atrasado) == "compra-compensada"
    # Outra compra continua podendo bloquear.
    assert api.bloquear(veiculo["id"], str(uuid.uuid4())).status_code == 200


def test_c2_antes_de_t2_tambem_marca_a_compra(api: Api) -> None:
    veiculo = api.cadastrar()
    compra = str(uuid.uuid4())
    assert api.desbloquear(veiculo["id"], compra).json()["efeito"] is False
    assert tipo_problema(api.bloquear(veiculo["id"], compra)) == "compra-compensada"


def test_t6_vende_e_c2_depois_do_pivo_e_recusado(api: Api) -> None:
    veiculo = api.cadastrar()
    compra = str(uuid.uuid4())
    api.bloquear(veiculo["id"], compra)
    assert api.vender(veiculo["id"], compra).json()["status"] == "VENDIDO"
    assert api.vender(veiculo["id"], compra).json()["efeito"] is False
    resposta = api.desbloquear(veiculo["id"], compra)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "veiculo-ja-vendido"


def test_t6_sem_o_bloqueio_da_compra(api: Api) -> None:
    veiculo = api.cadastrar()
    resposta = api.vender(veiculo["id"], str(uuid.uuid4()))
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "bloqueio-de-outra-compra"


def test_t3_cobranca_idempotente(api: Api) -> None:
    compra = str(uuid.uuid4())
    primeira = api.gerar_cobranca(compra)
    assert primeira.status_code == 201
    corpo = primeira.json()
    assert corpo["status"] == "PENDENTE"
    assert corpo["valor"] == "119900.00"
    assert corpo["codigo_pagamento"].startswith("PAG-")
    repetida = api.gerar_cobranca(compra)
    assert repetida.status_code == 200
    assert repetida.json()["codigo_pagamento"] == corpo["codigo_pagamento"]
    assert repetida.json()["efeito"] is False


@pytest.mark.parametrize("valor", ["0", "-5", "1.001", "abc"])
def test_t3_valor_invalido(api: Api, valor: str) -> None:
    assert api.gerar_cobranca(str(uuid.uuid4()), valor).status_code == 422


def test_c3_cancela_e_impede_t3_atrasado(api: Api) -> None:
    compra = str(uuid.uuid4())
    cancelada = api.cancelar_cobranca(compra)  # C3 antes de T3: marca de compensação
    assert cancelada.status_code == 200
    assert (cancelada.json()["status"], cancelada.json()["valor"]) == ("CANCELADA", None)
    atrasado = api.gerar_cobranca(compra)
    assert atrasado.status_code == 409
    assert tipo_problema(atrasado) == "compra-compensada"


def test_c3_depois_do_pagamento_avisa_que_o_pivo_aconteceu(api: Api) -> None:
    _, compra, codigo = api.compra_aguardando_pagamento()
    assert api.webhook(codigo, "PAGO").status_code == 200
    resposta = api.cancelar_cobranca(compra)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "cobranca-ja-paga"
