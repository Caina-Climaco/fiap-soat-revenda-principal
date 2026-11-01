"""Integracao entre os dois servicos pelo ambiente implantado (exige o servico de venda).

Cobre a comunicacao HTTP nos dois sentidos: a replica do anuncio (outbox do principal para
o servico de venda), a saga da compra (venda chama o principal: bloqueio e cobranca) e o
repasse do resultado do pagamento (webhook no principal, outbox para o servico de venda).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from apoio_e2e import CPF_DE_TESTE, Ambiente, esperar, tipo

pytestmark = [pytest.mark.e2e, pytest.mark.vendas]


def _anuncio(http: httpx.Client, veiculo_id: str, versao: int) -> dict[str, Any] | None:
    resposta = http.get(f"/api/v1/anuncios/{veiculo_id}")
    if resposta.status_code != 200:
        return None
    corpo: dict[str, Any] = resposta.json()
    return corpo if corpo["versao"] >= versao else None


def _webhook(http: httpx.Client, ambiente: Ambiente, codigo: str, status: str) -> httpx.Response:
    return http.post(
        "/api/v1/pagamentos/webhook",
        json={"codigo_pagamento": codigo, "status": status},
        headers={"apikey": ambiente.chave_pagamento, "X-Webhook-Secret": ambiente.segredo_webhook},
    )


def _compra(http: httpx.Client, compra_id: str) -> dict[str, Any]:
    corpo: dict[str, Any] = http.get(f"/api/v1/compras/{compra_id}").json()
    return corpo


def test_cadastro_e_edicao_chegam_a_vitrine(
    http: httpx.Client, gestor: dict[str, str], novo_veiculo: Callable[..., dict[str, Any]]
) -> None:
    veiculo = novo_veiculo()
    esperar(lambda: _anuncio(http, veiculo["id"], 1), "anuncio na vitrine")
    http.patch(f"/api/v1/veiculos/{veiculo['id']}", json={"cor": "Grafite"}, headers=gestor)
    anuncio = esperar(lambda: _anuncio(http, veiculo["id"], 2), "versao 2 na vitrine")
    assert anuncio["cor"] == "Grafite"
    assert anuncio["status"] == "A_VENDA"


def test_compra_paga_vende_o_veiculo_nos_dois_servicos(
    http: httpx.Client,
    ambiente: Ambiente,
    gestor: dict[str, str],
    novo_veiculo: Callable[..., dict[str, Any]],
) -> None:
    veiculo = novo_veiculo()
    anuncio = esperar(lambda: _anuncio(http, veiculo["id"], 1), "anuncio na vitrine")

    compra = http.post(
        "/api/v1/compras",
        json={"veiculo_id": veiculo["id"], "versao": anuncio["versao"], "cpf": CPF_DE_TESTE},
    )
    assert compra.status_code == 201, compra.text
    corpo = compra.json()
    assert corpo["status"] == "AGUARDANDO_PAGAMENTO"
    assert corpo["cpf"] == "***.982.247-**"
    codigo = corpo["codigo_pagamento"]

    # Durante a compra o gestor nao edita o veiculo (passo T2 da saga)
    bloqueado = http.patch(
        f"/api/v1/veiculos/{veiculo['id']}", json={"preco": "1000.00"}, headers=gestor
    )
    assert bloqueado.status_code == 409
    assert tipo(bloqueado) == "veiculo-nao-editavel"

    pago = _webhook(http, ambiente, codigo, "PAGO")
    assert pago.status_code == 200, pago.text

    efetivada = esperar(
        lambda: (c := _compra(http, corpo["id"]))["status"] == "EFETIVADA" and c,
        "compra efetivada",
    )
    assert efetivada["data_venda"]
    esperar(
        lambda: (
            http.get(f"/api/v1/veiculos/{veiculo['id']}", headers=gestor).json()["status"]
            == "VENDIDO"
        ),
        "veiculo vendido no catalogo (T6)",
    )
    vendidos = http.get("/api/v1/veiculos/vendidos", params={"limite": 100}).json()["itens"]
    assert veiculo["id"] in {v["veiculo_id"] for v in vendidos}
    # Notificacao repetida: 200 sem efeito
    repetida = _webhook(http, ambiente, codigo, "PAGO")
    assert repetida.json()["efeito"] == "SEM_EFEITO"


def test_pagamento_cancelado_devolve_o_veiculo_a_vitrine(
    http: httpx.Client,
    ambiente: Ambiente,
    gestor: dict[str, str],
    novo_veiculo: Callable[..., dict[str, Any]],
) -> None:
    veiculo = novo_veiculo()
    anuncio = esperar(lambda: _anuncio(http, veiculo["id"], 1), "anuncio na vitrine")
    compra = http.post(
        "/api/v1/compras",
        json={"veiculo_id": veiculo["id"], "versao": anuncio["versao"], "cpf": CPF_DE_TESTE},
    ).json()

    assert _webhook(http, ambiente, compra["codigo_pagamento"], "CANCELADO").status_code == 200

    cancelada = esperar(
        lambda: (c := _compra(http, compra["id"]))["status"] == "CANCELADA" and c,
        "compra cancelada",
    )
    assert cancelada["motivo_cancelamento"] == "PAGAMENTO_CANCELADO"
    assert cancelada["codigo_pagamento"] is None
    esperar(
        lambda: (a := _anuncio(http, veiculo["id"], 1)) and a["status"] == "A_VENDA",
        "anuncio de volta a vitrine",
    )
    editado = http.patch(
        f"/api/v1/veiculos/{veiculo['id']}", json={"preco": "39900.00"}, headers=gestor
    )
    assert editado.status_code == 200, "o bloqueio deveria ter sido desfeito (C2)"


def test_compra_com_versao_antiga_e_recusada(
    http: httpx.Client, gestor: dict[str, str], novo_veiculo: Callable[..., dict[str, Any]]
) -> None:
    veiculo = novo_veiculo()
    esperar(lambda: _anuncio(http, veiculo["id"], 1), "anuncio na vitrine")
    http.patch(f"/api/v1/veiculos/{veiculo['id']}", json={"cor": "Preto"}, headers=gestor)
    esperar(lambda: _anuncio(http, veiculo["id"], 2), "versao 2 na vitrine")
    resposta = http.post(
        "/api/v1/compras", json={"veiculo_id": veiculo["id"], "versao": 1, "cpf": CPF_DE_TESTE}
    )
    assert resposta.status_code == 409
    assert tipo(resposta) == "anuncio-alterado"


def test_documentacao_do_servico_de_venda_pelo_kong(http: httpx.Client) -> None:
    assert http.get("/vendas/docs").status_code == 200
    assert "/api/v1/compras" in http.get("/vendas/openapi.json").json()["paths"]
