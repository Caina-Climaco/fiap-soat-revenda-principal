"""Autorização (consumer do Kong e token de serviço), tracing e cliente HTTP entre serviços."""

import json
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider

from principal.shared import tracing
from principal.shared.auth import exigir_consumidor, exigir_token_servico
from principal.shared.cliente_servico import ClienteServico, FalhaDeRedeError
from principal.shared.errors import registrar_tratadores_globais

TOKEN = "token-de-servico-0123456789"


@pytest.fixture
def cliente() -> TestClient:
    app = FastAPI()
    registrar_tratadores_globais(app)
    Gestor = Annotated[str, Depends(exigir_consumidor("gestor"))]  # noqa: N806

    @app.get("/gestor")
    def gestor(consumidor: Gestor) -> dict[str, str]:
        return {"consumidor": consumidor}

    @app.get("/interno", dependencies=[Depends(exigir_token_servico(TOKEN))])
    def interno() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_consumidor_do_kong(cliente: TestClient) -> None:
    assert cliente.get("/gestor", headers={"X-Consumer-Username": "gestor"}).json() == {
        "consumidor": "gestor"
    }
    sem = cliente.get("/gestor")
    assert sem.status_code == 401
    assert sem.json()["type"].endswith("nao-autenticado")
    outro = cliente.get("/gestor", headers={"X-Consumer-Username": "gateway-pagamento"})
    assert outro.status_code == 403


@pytest.mark.parametrize("token", [None, "", "errado", TOKEN + "x"])
def test_token_de_servico_invalido(cliente: TestClient, token: str | None) -> None:
    cabecalhos = {} if token is None else {"X-Service-Token": token}
    resposta = cliente.get("/interno", headers=cabecalhos)
    assert resposta.status_code == 401
    assert resposta.json()["type"].endswith("servico-nao-autorizado")


def test_token_de_servico_valido(cliente: TestClient) -> None:
    assert cliente.get("/interno", headers={"X-Service-Token": TOKEN}).status_code == 200


# ---------------------------------------------------------------- tracing


def test_tracing_desligado_sem_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tracing.VARIAVEL_ENDPOINT, raising=False)
    assert tracing.tracing_habilitado() is False
    assert tracing.configurar_tracing("teste") is False
    assert tracing.ids_do_trace_atual() == {}
    assert tracing.contexto_atual() == {}


def test_contexto_do_trace_vai_e_volta() -> None:
    provedor = TracerProvider()
    rastreador = provedor.get_tracer("teste")
    with rastreador.start_as_current_span("origem"):
        ids = tracing.ids_do_trace_atual()
        portador = tracing.contexto_atual()
    assert len(ids["trace_id"]) == 32
    assert portador["traceparent"].split("-")[1] == ids["trace_id"]
    contexto = tracing.contexto_de(portador)
    with rastreador.start_as_current_span("continuacao", context=contexto):
        assert tracing.ids_do_trace_atual()["trace_id"] == ids["trace_id"]


# ---------------------------------------------------------------- cliente HTTP


def test_cliente_envia_token_corpo_e_cabecalhos() -> None:
    recebidas: list[httpx.Request] = []

    def responder(requisicao: httpx.Request) -> httpx.Response:
        recebidas.append(requisicao)
        return httpx.Response(200, text="ok")

    cliente = ClienteServico(
        "http://vendas.local", TOKEN, transporte=httpx.MockTransport(responder)
    )
    resposta = cliente.enviar("PUT", "/internal/v1/x", {"a": 1}, {"traceparent": "00-x"})
    cliente.fechar()
    assert (resposta.status, resposta.corpo) == (200, "ok")
    [requisicao] = recebidas
    assert requisicao.headers["X-Service-Token"] == TOKEN
    assert requisicao.headers["traceparent"] == "00-x"
    assert json.loads(requisicao.content) == {"a": 1}


def test_cliente_converte_falha_de_transporte() -> None:
    def falhar(requisicao: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusada", request=requisicao)

    cliente = ClienteServico("http://vendas.local", TOKEN, transporte=httpx.MockTransport(falhar))
    with pytest.raises(FalhaDeRedeError, match="ConnectError"):
        cliente.enviar("POST", "/x", {})


def test_tracing_ligado_instrumenta_app_banco_e_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry import trace
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from sqlalchemy import create_engine

    monkeypatch.setenv(tracing.VARIAVEL_ENDPOINT, "http://127.0.0.1:9")
    app = FastAPI()
    assert tracing.configurar_tracing("teste", app=app, engine=create_engine("sqlite://"))
    provedor = trace.get_tracer_provider()
    try:
        assert isinstance(provedor, TracerProvider)
        # Uma segunda chamada (outro app no mesmo processo) reaproveita o provedor.
        assert tracing.configurar_tracing("teste", app=FastAPI())
        assert trace.get_tracer_provider() is provedor
        # Sem app nem engine (ex.: um processo sem HTTP), só o provedor e o httpx.
        assert tracing.configurar_tracing("teste")
    finally:
        HTTPXClientInstrumentor().uninstrument()
        provedor.shutdown()  # type: ignore[union-attr]
