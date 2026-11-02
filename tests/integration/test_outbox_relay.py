"""Relay da outbox contra PostgreSQL real (ADR-003): entrega, ordem, backoff e falhas."""

from __future__ import annotations

import threading
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import Engine, text

from apoio.api import Api
from apoio.fakes import FALHA_DE_REDE, EntregadorFalso, RelogioMonotonico
from principal import outbox as outbox_cli
from principal.mensageria.relay import RelayOutbox
from principal.outbox import reprocessar
from principal.shared.cliente_servico import ClienteServico
from principal.shared.clock import RelogioFixo
from principal.shared.db import BancoDeDados
from principal.shared.metricas import Metricas


def _estado(engine: Engine) -> list[tuple[str, str, int]]:
    with engine.connect() as conexao:
        linhas = conexao.execute(
            text("SELECT tipo, status, tentativas FROM mensageria.outbox ORDER BY id")
        )
        return [tuple(linha) for linha in linhas]  # type: ignore[misc]


def test_entrega_tudo_e_marca_entregue(
    api: Api, relay: RelayOutbox, vendas: EntregadorFalso, engine: Engine
) -> None:
    _, compra, codigo = api.compra_aguardando_pagamento()
    api.webhook(codigo, "PAGO")
    assert relay.entregar_pendentes() == 2
    assert relay.entregar_pendentes() == 0
    assert vendas.caminhos[1] == f"/internal/v1/compras/{compra}/resultado-pagamento"
    assert _estado(engine) == [("ANUNCIO", "ENTREGUE", 1), ("RESULTADO_PAGAMENTO", "ENTREGUE", 1)]
    assert relay.pendentes() == 0


def test_falha_transitoria_adia_com_backoff_e_preserva_a_ordem_da_chave(
    api: Api, relay: RelayOutbox, vendas: EntregadorFalso, relogio: RelogioFixo, engine: Engine
) -> None:
    """Com o serviço de venda fora, a versão 2 do anúncio não passa na frente da versão 1."""
    veiculo = api.cadastrar()
    api.editar(veiculo["id"], preco="100000.00")
    vendas.responder(FALHA_DE_REDE)
    assert relay.entregar_pendentes() == 1  # v1 adiada; v2 esperando atrás dela
    assert _estado(engine) == [("ANUNCIO", "PENDENTE", 1), ("ANUNCIO", "PENDENTE", 0)]
    assert relay.entregar_pendentes() == 0  # ainda no backoff
    relogio.avancar(timedelta(seconds=1))
    assert relay.entregar_pendentes() == 2
    versoes = [corpo["versao"] for _, _, corpo, _ in vendas.chamadas]
    assert versoes == [1, 1, 2]


def test_404_e_5xx_repetem_e_4xx_definitivo_falha(
    api: Api, relay: RelayOutbox, vendas: EntregadorFalso, relogio: RelogioFixo, engine: Engine
) -> None:
    api.cadastrar()
    api.cadastrar()
    vendas.responder(404, 422)  # 1º: transitório; 2º: erro do contrato
    relay.entregar_pendentes()
    assert [s for _, s, _ in _estado(engine)] == ["PENDENTE", "FALHOU"]
    with engine.connect() as conexao:
        erro = conexao.execute(
            text("SELECT ultimo_erro FROM mensageria.outbox WHERE status = 'FALHOU'")
        ).scalar_one()
    assert erro.startswith("HTTP 422")
    relogio.avancar(timedelta(seconds=1))
    relay.entregar_pendentes()
    assert [s for _, s, _ in _estado(engine)] == ["ENTREGUE", "FALHOU"]


def test_5xx_nao_esgota_por_tentativas_so_pelo_prazo(
    banco: BancoDeDados, api: Api, relogio: RelogioFixo, engine: Engine
) -> None:
    """ADR-003: queda longa do serviço de venda não perde a mensagem."""
    api.cadastrar()
    vendas = EntregadorFalso(*([503] * 25))
    relay = RelayOutbox(banco, vendas, relogio, prazo_entrega=timedelta(hours=2))
    for _ in range(21):
        relay.entregar_pendentes()
        relogio.avancar(timedelta(minutes=1))
    assert _estado(engine) == [("ANUNCIO", "PENDENTE", 21)]  # 20 tentativas não bastam
    relogio.avancar(timedelta(hours=2))
    relay.entregar_pendentes()
    assert _estado(engine) == [("ANUNCIO", "FALHOU", 22)]


def test_duas_replicas_nunca_entregam_a_mesma_mensagem(
    banco: BancoDeDados, api: Api, relogio: RelogioFixo
) -> None:
    for _ in range(20):
        api.cadastrar()
    vendas = EntregadorFalso()
    trava = threading.Lock()

    class EntregadorSeguro:
        def enviar(self, *args: object, **kwargs: object) -> object:
            with trava:
                return vendas.enviar(*args, **kwargs)  # type: ignore[arg-type]

    relays = [RelayOutbox(banco, EntregadorSeguro(), relogio, lote=5) for _ in range(2)]  # type: ignore[arg-type]

    def drenar(relay: RelayOutbox) -> None:
        while relay.entregar_pendentes():
            pass

    threads = [threading.Thread(target=drenar, args=(r,)) for r in relays]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(vendas.chamadas) == 20
    assert len(set(vendas.caminhos)) == 20


def test_relay_com_cliente_http_real(banco: BancoDeDados, api: Api, relogio: RelogioFixo) -> None:
    """Do banco ao destino HTTP: token de serviço, corpo JSON e `traceparent` opcional."""
    veiculo = api.cadastrar()
    recebidas: list[httpx.Request] = []

    def vendas(requisicao: httpx.Request) -> httpx.Response:
        recebidas.append(requisicao)
        return httpx.Response(200, json={"aplicado": True})

    cliente = ClienteServico(
        "http://vendas", "token-saida-0123456789", transporte=httpx.MockTransport(vendas)
    )
    RelayOutbox(banco, cliente, relogio).entregar_pendentes()
    cliente.fechar()
    [requisicao] = recebidas
    assert requisicao.method == "PUT"
    assert requisicao.url.path == f"/internal/v1/anuncios/{veiculo['id']}"
    assert requisicao.headers["X-Service-Token"] == "token-saida-0123456789"


def test_gauge_de_pendentes_nao_conta_a_cada_ciclo_ocioso(
    banco: BancoDeDados, api: Api, relogio: RelogioFixo
) -> None:
    """ADR-003: `count(*)` só quando entregou algo ou a cada 10 s ocioso."""
    monotonico = RelogioMonotonico()
    metricas = Metricas()
    relay = RelayOutbox(banco, EntregadorFalso(), relogio, metricas=metricas, monotonic=monotonico)
    contagens: list[int] = []
    original = relay.pendentes

    def contar() -> int:
        contagens.append(1)
        return original()

    relay.pendentes = contar  # type: ignore[method-assign]
    relay.entregar_pendentes()  # primeiro ciclo: sempre atualiza
    monotonico.instante = 5.0
    relay.entregar_pendentes()  # ocioso há 5 s: não consulta
    assert len(contagens) == 1
    api.cadastrar()
    relay.entregar_pendentes()  # entregou: atualiza na hora
    assert len(contagens) == 2
    monotonico.instante = 16.0
    relay.entregar_pendentes()  # ocioso há mais de 10 s: atualiza
    assert len(contagens) == 3
    assert "revenda_outbox_pendentes 0.0" in metricas.exportar().decode()


def test_reprocessar_devolve_falhou_a_pendente(
    banco: BancoDeDados,
    api: Api,
    relay: RelayOutbox,
    vendas: EntregadorFalso,
    relogio: RelogioFixo,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-003: depois de corrigida a causa, a mensagem volta para a fila."""
    api.cadastrar()
    api.cadastrar()
    vendas.responder(422, 422)  # contrato quebrado: as duas viram FALHOU
    relay.entregar_pendentes()
    assert [s for _, s, _ in _estado(engine)] == ["FALHOU", "FALHOU"]
    with engine.connect() as conexao:
        primeira = conexao.execute(text("SELECT min(id) FROM mensageria.outbox")).scalar_one()
    assert reprocessar(banco, relogio.agora(), [primeira]) == 1
    assert _estado(engine) == [("ANUNCIO", "PENDENTE", 0), ("ANUNCIO", "FALHOU", 1)]
    assert outbox_cli.main(["reprocessar"], banco=banco, relogio=relogio) == 0
    assert _estado(engine) == [("ANUNCIO", "PENDENTE", 0), ("ANUNCIO", "PENDENTE", 0)]
    assert relay.entregar_pendentes() == 2
    assert [s for _, s, _ in _estado(engine)] == ["ENTREGUE", "ENTREGUE"]
    # Pela linha de comando, com a conexão montada das variáveis de ambiente.
    url = engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    assert outbox_cli.main(["reprocessar", "999999"]) == 0


def test_reprocessada_depois_do_prazo_vencido_ganha_prazo_novo(
    banco: BancoDeDados, api: Api, relogio: RelogioFixo, engine: Engine
) -> None:
    """ADR-003, reprocessamento: depois de uma queda longa, a mensagem reprocessada não volta
    já vencida (o prazo conta do reprocessamento, não da criação original)."""
    api.cadastrar()
    vendas = EntregadorFalso(503, 503)
    relay = RelayOutbox(banco, vendas, relogio, prazo_entrega=timedelta(hours=24))
    relogio.avancar(timedelta(days=2))  # o serviço de venda ficou fora por 2 dias
    relay.entregar_pendentes()
    assert [s for _, s, _ in _estado(engine)] == ["FALHOU"]
    assert reprocessar(banco, relogio.agora()) == 1
    relay.entregar_pendentes()  # 503 de novo, logo depois do reprocessamento
    assert _estado(engine) == [("ANUNCIO", "PENDENTE", 1)]
