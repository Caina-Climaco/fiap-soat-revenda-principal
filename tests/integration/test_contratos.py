"""Contratos das rotas internas (ADR-008), com a aplicacao real e o PostgreSQL.

- Provedor: as respostas reais das rotas internas deste servico seguem o contrato
  publicado (contratos/principal-interno.openapi.json), inclusive os erros.
- Consumidor: tudo o que a outbox envia ao servico de venda (replica do anuncio e
  resultado do pagamento) segue a copia do contrato dele
  (contratos/vendas-interno.openapi.json).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import Response

from apoio.api import Api
from apoio.fakes import EntregadorFalso
from contrato.openapi_contrato import Contrato, carregar

pytestmark = pytest.mark.contrato

PROPRIO = Contrato(carregar("principal-interno.openapi.json"))
VENDAS = Contrato(carregar("vendas-interno.openapi.json"))


def _conferir(resposta: Response) -> Response:
    requisicao = resposta.request
    corpo: Any = json.loads(requisicao.content) if requisicao.content else None
    PROPRIO.validar_requisicao(requisicao.method, requisicao.url.path, corpo)
    PROPRIO.validar_resposta(
        requisicao.method, requisicao.url.path, resposta.status_code, resposta.json()
    )
    return resposta


def test_provedor_passos_da_saga_e_cobranca(api: Api) -> None:
    veiculo = api.cadastrar()
    compra = str(uuid.uuid4())
    # T2 e as recusas que o servico de venda trata (409 com o tipo do problema)
    assert _conferir(api.bloquear(veiculo["id"], compra)).status_code == 200
    assert _conferir(api.bloquear(veiculo["id"], str(uuid.uuid4()))).status_code == 409
    assert _conferir(api.bloquear(str(uuid.uuid4()), compra)).status_code == 404
    # T3, repeticao idempotente e C3
    assert _conferir(api.gerar_cobranca(compra, veiculo["preco"])).status_code == 201
    assert _conferir(api.gerar_cobranca(compra, veiculo["preco"])).status_code == 200
    assert _conferir(api.cancelar_cobranca(compra)).status_code == 200
    # C2 e a regra contra ressurreicao: T2 atrasado da compra compensada
    assert _conferir(api.desbloquear(veiculo["id"], compra)).status_code == 200
    atrasado = _conferir(api.bloquear(veiculo["id"], compra))
    assert atrasado.status_code == 409
    assert atrasado.json()["type"].endswith("compra-compensada")


def test_provedor_venda_no_catalogo(api: Api) -> None:
    veiculo_id, compra, codigo = api.compra_aguardando_pagamento()
    assert api.webhook(codigo, "PAGO").status_code == 200
    assert _conferir(api.vender(veiculo_id, compra)).status_code == 200
    assert _conferir(api.vender(veiculo_id, compra)).status_code == 200  # repeticao


def test_provedor_token_de_servico_obrigatorio(api: Api) -> None:
    resposta = api.http.post("/internal/v1/cobrancas", json={})
    PROPRIO.validar_resposta("POST", "/internal/v1/cobrancas", 401, resposta.json())


def test_consumidor_mensagens_da_outbox(app: FastAPI, api: Api, vendas: EntregadorFalso) -> None:
    veiculo = api.cadastrar()
    assert api.editar(veiculo["id"], preco="99000.00").status_code == 200
    _, _, pago = api.compra_aguardando_pagamento()
    _, _, cancelado = api.compra_aguardando_pagamento()
    assert api.webhook(pago, "PAGO").status_code == 200
    assert api.webhook(cancelado, "CANCELADO").status_code == 200
    app.state.relay.entregar_pendentes()

    tipos = set()
    for metodo, caminho, corpo, _ in vendas.chamadas:
        tipos.add(VENDAS.validar_requisicao(metodo, caminho, corpo).modelo)
    assert tipos == {
        "/internal/v1/anuncios/{veiculo_id}",
        "/internal/v1/compras/{compra_id}/resultado-pagamento",
    }
