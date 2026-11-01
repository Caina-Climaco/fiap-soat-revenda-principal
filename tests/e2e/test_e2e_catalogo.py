"""Cadastro, edicao e consulta de veiculos pelo gestor; webhook de pagamento protegido."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from apoio_e2e import Ambiente, tipo

pytestmark = pytest.mark.e2e


def test_cadastro_e_consulta(
    http: httpx.Client, gestor: dict[str, str], novo_veiculo: Callable[..., dict[str, Any]]
) -> None:
    veiculo = novo_veiculo(cor="Vermelho")
    assert veiculo["status"] == "A_VENDA"
    assert veiculo["versao"] == 1
    lido = http.get(f"/api/v1/veiculos/{veiculo['id']}", headers=gestor)
    assert lido.status_code == 200
    assert lido.json()["cor"] == "Vermelho"


def test_edicao_parcial_gera_nova_versao(
    http: httpx.Client, gestor: dict[str, str], novo_veiculo: Callable[..., dict[str, Any]]
) -> None:
    veiculo = novo_veiculo()
    editado = http.patch(
        f"/api/v1/veiculos/{veiculo['id']}", json={"preco": "45500.00"}, headers=gestor
    )
    assert editado.status_code == 200, editado.text
    corpo = editado.json()
    assert corpo["preco"] == "45500.00"
    assert corpo["versao"] == 2
    assert corpo["modelo"] == veiculo["modelo"]


def test_dados_invalidos_em_problem_json(http: httpx.Client, gestor: dict[str, str]) -> None:
    resposta = http.post(
        "/api/v1/veiculos",
        json={"marca": "E2E", "modelo": "X", "ano": "2020", "cor": "Azul", "preco": "-1"},
        headers=gestor,
    )
    assert resposta.status_code == 422
    assert resposta.headers["content-type"].startswith("application/problem+json")


def test_listagem_do_gestor_paginada(http: httpx.Client, gestor: dict[str, str]) -> None:
    pagina = http.get("/api/v1/veiculos", params={"limite": 5}, headers=gestor).json()
    assert pagina["limite"] == 5
    assert len(pagina["itens"]) <= 5


def test_webhook_exige_o_segredo_alem_da_chave(http: httpx.Client, ambiente: Ambiente) -> None:
    corpo = {"codigo_pagamento": "PAG-000000000000", "status": "PAGO"}
    so_chave = http.post(
        "/api/v1/pagamentos/webhook", json=corpo, headers={"apikey": ambiente.chave_pagamento}
    )
    assert so_chave.status_code == 401
    assert tipo(so_chave) == "webhook-nao-autorizado"
    completo = http.post(
        "/api/v1/pagamentos/webhook",
        json=corpo,
        headers={"apikey": ambiente.chave_pagamento, "X-Webhook-Secret": ambiente.segredo_webhook},
    )
    assert completo.status_code == 404
    assert tipo(completo) == "cobranca-nao-encontrada"
