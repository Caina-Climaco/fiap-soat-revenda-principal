"""A borda (Kong): autenticacao por consumer, ACL, rotas fechadas e correlacao."""

from __future__ import annotations

import uuid

import httpx
import pytest
from apoio_e2e import Ambiente

pytestmark = pytest.mark.e2e

CORPO = {"marca": "E2E", "modelo": "Borda", "ano": 2021, "cor": "Azul", "preco": "50000.00"}


def test_gestor_sem_chave_e_barrado_no_kong(http: httpx.Client) -> None:
    resposta = http.post("/api/v1/veiculos", json=CORPO)
    assert resposta.status_code == 401
    assert "x-consumer-username" not in {h.lower() for h in resposta.headers}


def test_header_de_consumer_forjado_nao_passa_pela_borda(http: httpx.Client) -> None:
    """O principal confia em X-Consumer-Username; sem chave valida o Kong nem encaminha."""
    resposta = http.post("/api/v1/veiculos", json=CORPO, headers={"X-Consumer-Username": "gestor"})
    assert resposta.status_code == 401


def test_chave_invalida_e_recusada(http: httpx.Client) -> None:
    resposta = http.get("/api/v1/veiculos", headers={"apikey": "chave-que-nao-existe"})
    assert resposta.status_code == 401


def test_consumer_do_pagamento_nao_acessa_rotas_do_gestor(
    http: httpx.Client, ambiente: Ambiente
) -> None:
    resposta = http.get("/api/v1/veiculos", headers={"apikey": ambiente.chave_pagamento})
    assert resposta.status_code == 403


def test_gestor_nao_chama_o_webhook(http: httpx.Client, ambiente: Ambiente) -> None:
    resposta = http.post(
        "/api/v1/pagamentos/webhook",
        json={"codigo_pagamento": "PAG-000000000000", "status": "PAGO"},
        headers={"apikey": ambiente.chave_gestor, "X-Webhook-Secret": ambiente.segredo_webhook},
    )
    assert resposta.status_code == 403


@pytest.mark.parametrize(
    "caminho",
    [
        f"/internal/v1/veiculos/{uuid.uuid4()}/bloqueio",
        "/internal/v1/cobrancas",
        "/metrics",
        "/health/ready",
    ],
)
def test_rotas_internas_e_metricas_nao_tem_rota_publica(http: httpx.Client, caminho: str) -> None:
    assert http.get(caminho).status_code == 404
    assert http.post(caminho, json={}).status_code == 404


def test_documentacao_do_principal_pelo_kong(http: httpx.Client) -> None:
    pagina = http.get("/principal/docs")
    assert pagina.status_code == 200
    assert "/principal/openapi.json" in pagina.text
    esquema = http.get("/principal/openapi.json").json()
    assert "/api/v1/veiculos" in esquema["paths"]


def test_x_request_id_gerado_ou_propagado(http: httpx.Client, gestor: dict[str, str]) -> None:
    gerado = http.get("/api/v1/veiculos", headers=gestor)
    assert uuid.UUID(gerado.headers["x-request-id"].split("#")[0])
    proprio = http.get("/api/v1/veiculos", headers={**gestor, "X-Request-ID": "e2e-correlacao"})
    assert proprio.headers["x-request-id"] == "e2e-correlacao"


def test_rota_inexistente_no_kong(http: httpx.Client) -> None:
    assert http.get("/api/v1/nao-existe").status_code == 404
