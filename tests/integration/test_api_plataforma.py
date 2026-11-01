"""Saúde, OpenAPI, erros, correlação, logs sem segredos e métricas do serviço principal."""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import principal.main
from apoio.api import SEGREDO_WEBHOOK, TOKEN_ENTRADA, Api, tipo_problema
from apoio.fakes import EntregadorFalso
from principal.main import criar_app
from principal.shared.config import Settings
from principal.shared.db import BancoDeDados, criar_engine
from principal.shared.logging import FormatadorJson

# ---------------------------------------------------------------- saúde e montagem


def test_health_live_e_ready(api: Api) -> None:
    assert api.http.get("/health/live").json() == {"status": "ok"}
    pronto = api.http.get("/health/ready")
    assert pronto.status_code == 200
    assert pronto.json() == {"status": "ok", "verificacoes": {"banco": "ok"}}


def test_health_ready_503_sem_banco(settings: Settings) -> None:
    sem_banco = BancoDeDados(criar_engine("postgresql+psycopg://x:y@127.0.0.1:1/nada"))
    app = criar_app(settings, banco=sem_banco, entregador=EntregadorFalso())
    with TestClient(app) as cliente:
        assert cliente.get("/health/live").status_code == 200
        resposta = cliente.get("/health/ready")
    assert resposta.status_code == 503
    assert tipo_problema(resposta) == "indisponivel"


def test_app_padrao_monta_banco_cliente_e_relay(settings: Settings) -> None:
    ativo = settings.model_copy(update={"outbox_relay_ativo": True})
    app = criar_app(ativo)
    with TestClient(app) as cliente:  # o relay roda em segundo plano durante o ciclo de vida
        assert cliente.get("/health/ready").status_code == 200


def test_app_do_uvicorn_e_criado_sob_demanda_a_partir_do_ambiente(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert settings.database_url
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("WEBHOOK_SECRET", SEGREDO_WEBHOOK)
    monkeypatch.setenv("TOKEN_SERVICO_ENTRADA", TOKEN_ENTRADA)
    monkeypatch.setenv("TOKEN_SERVICO_SAIDA", TOKEN_ENTRADA)
    monkeypatch.setattr(principal.main, "_app", None)
    app = principal.main.app
    assert isinstance(app, FastAPI)
    assert principal.main.app is app
    with pytest.raises(AttributeError):
        _ = principal.main.nao_existe


# ---------------------------------------------------------------- OpenAPI


def test_openapi_documenta_autorizacao_e_problem_json(api: Api) -> None:
    esquema = api.http.get("/openapi.json").json()
    seguranca = esquema["components"]["securitySchemes"]
    assert seguranca["consumidor-kong"]["name"] == "X-Consumer-Username"
    assert seguranca["token-servico"]["name"] == "X-Service-Token"
    assert seguranca["webhook"]["name"] == "X-Webhook-Secret"
    assert "Problema" in esquema["components"]["schemas"]
    caminhos = set(esquema["paths"])
    assert {
        "/api/v1/veiculos",
        "/api/v1/pagamentos/webhook",
        "/internal/v1/veiculos/{veiculo_id}/bloqueio",
        "/internal/v1/cobrancas",
    } <= caminhos
    assert api.http.get("/docs").status_code == 200
    # O esquema é montado uma vez e reaproveitado
    assert api.http.get("/openapi.json").json() == esquema


def test_documentacao_com_prefixo_do_kong(settings: Settings, banco: BancoDeDados) -> None:
    com_prefixo = settings.model_copy(update={"docs_prefixo": "/principal"})
    app = criar_app(com_prefixo, banco=banco, entregador=EntregadorFalso())
    with TestClient(app) as cliente:
        pagina = cliente.get("/principal/docs")
        assert pagina.status_code == 200
        assert "/principal/openapi.json" in pagina.text
        assert cliente.get("/principal/openapi.json").json()["paths"]
        assert cliente.get("/docs").status_code == 404


@pytest.mark.parametrize("prefixo", ["principal", "/principal/", "/Principal", "/a b"])
def test_prefixo_da_documentacao_invalido(settings: Settings, prefixo: str) -> None:
    with pytest.raises(ValueError, match="docs_prefixo"):
        Settings.model_validate({**settings.model_dump(), "docs_prefixo": prefixo})


# ---------------------------------------------------------------- erros e correlação


def test_rota_inexistente_e_metodo_nao_permitido_em_problem_json(api: Api) -> None:
    nao_existe = api.http.get("/api/v1/nao-existe")
    assert nao_existe.status_code == 404
    assert nao_existe.json()["type"] == "about:blank"
    metodo = api.http.delete("/api/v1/pagamentos/webhook")
    assert metodo.status_code == 405
    assert metodo.json()["title"] == "Método não permitido"


def test_json_malformado_e_400(api: Api) -> None:
    resposta = api.http.post(
        "/api/v1/veiculos",
        content="{nao e json",
        headers={"X-Consumer-Username": "gestor", "Content-Type": "application/json"},
    )
    assert resposta.status_code == 400
    assert tipo_problema(resposta) == "requisicao-malformada"


def test_x_request_id_e_propagado_ou_gerado(api: Api) -> None:
    assert api.http.get("/health/live", headers={"X-Request-ID": "abc-1"}).headers[
        "x-request-id"
    ] == ("abc-1")
    assert uuid.UUID(api.http.get("/health/live").headers["x-request-id"])
    injetado = api.http.get("/health/live", headers={"X-Request-ID": "a\tb"}).headers[
        "x-request-id"
    ]
    assert uuid.UUID(injetado)


@pytest.fixture
def logs(app: FastAPI) -> Iterator[io.StringIO]:
    """Captura o log JSON da aplicação (o handler é instalado depois de criar_app)."""
    saida = io.StringIO()
    handler = logging.StreamHandler(saida)
    handler.setFormatter(FormatadorJson())
    raiz = logging.getLogger()
    raiz.addHandler(handler)
    yield saida
    raiz.removeHandler(handler)


def test_logs_sem_segredos_com_eventos_e_consumidor(api: Api, logs: io.StringIO) -> None:
    _, _, codigo = api.compra_aguardando_pagamento()
    api.http.post(
        "/api/v1/pagamentos/webhook",
        json={"codigo_pagamento": codigo, "status": "PAGO"},
        headers={"X-Webhook-Secret": SEGREDO_WEBHOOK, "X-Request-ID": "req-log-1"},
    )
    texto = logs.getvalue()
    assert SEGREDO_WEBHOOK not in texto
    assert TOKEN_ENTRADA not in texto
    registros = [json.loads(linha) for linha in texto.splitlines() if linha.strip()]
    acesso = [r for r in registros if r.get("request_id") == "req-log-1" and "rota" in r]
    assert [(r["logger"], r["rota"], r["status"]) for r in acesso] == [
        ("principal.acesso", "/api/v1/pagamentos/webhook", 200)
    ]
    eventos = {r.get("evento") for r in registros if r.get("logger") == "principal.eventos"}
    assert {"VeiculoCadastrado", "VeiculoBloqueado", "CobrancaGerada", "CobrancaPaga"} <= eventos
    # O consumer do Kong fica no log de acesso das rotas do gestor (auditoria)
    cadastros = [
        r for r in registros if r.get("rota") == "/api/v1/veiculos" and r.get("status") == 201
    ]
    assert cadastros
    assert {r.get("consumidor") for r in cadastros} == {"gestor"}


def test_erro_500_registra_um_unico_stack_trace_sem_parametros_do_banco(
    app: FastAPI, logs: io.StringIO
) -> None:
    banco: BancoDeDados = app.state.banco

    def consulta_quebrada() -> None:
        with banco.nova_sessao() as sessao:
            sessao.execute(
                text("SELECT CAST(:valor AS integer) / 0"), {"valor": "987654321"}
            ).scalar()

    app.add_api_route("/falha-banco", consulta_quebrada)
    with TestClient(app, raise_server_exceptions=True) as cliente:
        resposta = cliente.get("/falha-banco", headers={"X-Request-ID": "req-500"})
    assert resposta.status_code == 500
    assert tipo_problema(resposta) == "erro-interno"
    registros = [json.loads(linha) for linha in logs.getvalue().splitlines() if linha.strip()]
    com_stack = [r for r in registros if "excecao" in r]
    assert len(com_stack) == 1
    assert (com_stack[0]["logger"], com_stack[0]["request_id"]) == ("principal.erros", "req-500")
    assert "987654321" not in logs.getvalue()


# ---------------------------------------------------------------- métricas


def test_metricas_de_negocio_e_da_outbox(api: Api, relay: object, vendas: EntregadorFalso) -> None:
    _, _, codigo = api.compra_aguardando_pagamento()
    api.webhook(codigo, "PAGO")
    vendas.responder(200, 503)
    relay.entregar_pendentes()  # type: ignore[attr-defined]
    texto = api.http.get("/metrics").text
    assert "revenda_veiculos_cadastrados_total 1.0" in texto
    assert 'revenda_principal_passos_saga_total{passo="bloqueio"} 1.0' in texto
    assert 'revenda_cobrancas_total{evento="paga",origem=""} 1.0' in texto
    assert 'revenda_outbox_entregas_total{resultado="entregue",tipo="ANUNCIO"} 1.0' in texto
    assert (
        'revenda_outbox_entregas_total{resultado="adiada",tipo="RESULTADO_PAGAMENTO"} 1.0' in texto
    )
    assert "revenda_outbox_pendentes 1.0" in texto
    assert 'revenda_http_requisicoes_total{metodo="POST",rota="/api/v1/veiculos",status="201"}' in (
        texto
    )
