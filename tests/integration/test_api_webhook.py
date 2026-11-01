"""Webhook do processador de pagamentos (ADR-009) e o repasse pela outbox."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from apoio.api import Api, tipo_problema


def _linhas(engine: Engine, sql: str) -> list[dict[str, object]]:
    with engine.connect() as conexao:
        return [dict(linha._mapping) for linha in conexao.execute(text(sql))]


@pytest.mark.parametrize(("status", "cobranca"), [("PAGO", "PAGA"), ("CANCELADO", "CANCELADA")])
def test_resultado_e_aplicado_e_repassado(
    api: Api, engine: Engine, status: str, cobranca: str
) -> None:
    _, compra, codigo = api.compra_aguardando_pagamento()
    resposta = api.webhook(codigo, status)
    assert resposta.status_code == 200
    assert resposta.json() == {
        "codigo_pagamento": codigo,
        "status_cobranca": cobranca,
        "efeito": "APLICADO",
    }
    repetida = api.webhook(codigo, status)
    assert repetida.json()["efeito"] == "SEM_EFEITO"
    repasses = _linhas(
        engine, "SELECT caminho, corpo FROM mensageria.outbox WHERE tipo = 'RESULTADO_PAGAMENTO'"
    )
    assert repasses == [
        {
            "caminho": f"/internal/v1/compras/{compra}/resultado-pagamento",
            "corpo": {"codigo_pagamento": codigo, "status": status},
        }
    ]


def test_pago_depois_do_cancelamento_fica_registrado_para_estorno(api: Api, engine: Engine) -> None:
    _, compra, codigo = api.compra_aguardando_pagamento()
    api.cancelar_cobranca(compra)  # expiração (C3) ganhou a corrida
    resposta = api.webhook(codigo, "PAGO")
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "pagamento-apos-cancelamento"
    [linha] = _linhas(
        engine,
        "SELECT status, origem_cancelamento, pagamento_apos_cancelamento FROM pagamentos.cobrancas",
    )
    assert linha == {
        "status": "CANCELADA",
        "origem_cancelamento": "SAGA",
        "pagamento_apos_cancelamento": True,
    }


def test_cancelado_depois_do_pagamento(api: Api) -> None:
    _, _, codigo = api.compra_aguardando_pagamento()
    api.webhook(codigo, "PAGO")
    resposta = api.webhook(codigo, "CANCELADO")
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "cobranca-ja-paga"


def test_codigo_desconhecido(api: Api) -> None:
    resposta = api.webhook("PAG-000000000000", "PAGO")
    assert resposta.status_code == 404
    assert tipo_problema(resposta) == "cobranca-nao-encontrada"


@pytest.mark.parametrize("segredo", [None, "errado", ""])
def test_segredo_ausente_ou_errado_nao_altera_nada(
    api: Api, engine: Engine, segredo: str | None
) -> None:
    _, _, codigo = api.compra_aguardando_pagamento()
    resposta = api.webhook(codigo, "PAGO", segredo=segredo)
    assert resposta.status_code == 401
    assert tipo_problema(resposta) == "webhook-nao-autorizado"
    [linha] = _linhas(engine, "SELECT status FROM pagamentos.cobrancas")
    assert linha["status"] == "PENDENTE"


@pytest.mark.parametrize(
    "corpo",
    [
        {"codigo_pagamento": "PAG-123", "status": "PAGO"},
        {"codigo_pagamento": "PAG-3f9a1c2b7d4e", "status": "APROVADO"},
        {"codigo_pagamento": "PAG-3f9a1c2b7d4e"},
    ],
)
def test_payload_invalido(api: Api, corpo: dict[str, str]) -> None:
    resposta = api.http.post(
        "/api/v1/pagamentos/webhook",
        json=corpo,
        headers={"X-Webhook-Secret": "segredo-do-webhook-de-teste-0123456789"},
    )
    assert resposta.status_code == 422
