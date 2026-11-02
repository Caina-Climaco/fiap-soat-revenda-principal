"""App factory do serviço principal. Entrada do container: `uvicorn principal.main:app`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.openapi.utils import get_openapi

from principal import __version__
from principal.catalogo.interfaces.router import (
    PROBLEMAS_CATALOGO,
    criar_router_interno_veiculos,
    criar_router_veiculos,
)
from principal.composicao import Composicao, acoes_metricas_negocio, nova_limpeza
from principal.mensageria.relay import Entregador, ExecutorRelay, RelayOutbox
from principal.pagamentos.interfaces.router import (
    PROBLEMAS_PAGAMENTOS,
    criar_router_interno_cobrancas,
    criar_router_pagamentos,
)
from principal.shared.auth import exigir_consumidor, exigir_token_servico
from principal.shared.cliente_servico import ClienteServico
from principal.shared.clock import Clock, RelogioSistema
from principal.shared.config import Settings
from principal.shared.db import BancoDeDados, criar_engine
from principal.shared.errors import registrar_problemas, registrar_tratadores_globais
from principal.shared.eventos import PublicadorEventos, PublicadorEventosLog
from principal.shared.http import Problema
from principal.shared.logging import configurar_logs
from principal.shared.metricas import Metricas, MetricasMiddleware, PublicadorEventosComMetricas
from principal.shared.middleware import CorrelacaoMiddleware
from principal.shared.saude import criar_router_saude
from principal.shared.tracing import configurar_tracing

PREFIXO_API = "/api/v1"
PREFIXO_INTERNO = "/internal/v1"
NOME_SERVICO = "revenda-principal"

DESCRICAO = """
Serviço principal da revenda de veículos (Fase 4): cadastro e edição de veículos pela loja,
cobrança e webhook do processador de pagamentos. As listagens e a compra ficam no serviço
de venda, que recebe os anúncios e os resultados de pagamento deste serviço por HTTP.

**Autorização** (pelo Kong): rotas do gestor exigem a chave do consumer `gestor`; o webhook
exige a chave do consumer `gateway-pagamento` e o header `X-Webhook-Secret`. As rotas
`/internal/v1` não têm rota no Kong e exigem `X-Service-Token`.

Erros seguem a RFC 9457 (`application/problem+json`).
"""

TAGS = [
    {"name": "Veículos (gestor)", "description": "Catálogo: cadastro, edição e consulta."},
    {"name": "Pagamentos (processador)", "description": "Resultado do pagamento (webhook)."},
    {"name": "Interno: saga da compra", "description": "Passos chamados pelo serviço de venda."},
    {"name": "Saúde", "description": "Probes de liveness e readiness."},
]


def criar_app(
    settings: Settings | None = None,
    *,
    banco: BancoDeDados | None = None,
    relogio: Clock | None = None,
    publicador: PublicadorEventos | None = None,
    entregador: Entregador | None = None,
) -> FastAPI:
    """Monta a aplicação; os parâmetros opcionais permitem substituir adaptadores nos testes."""
    settings = settings or Settings()  # valores vêm do ambiente
    configurar_logs(settings.log_level)

    banco = banco or BancoDeDados(criar_engine(settings.url_banco()))
    relogio = relogio or RelogioSistema()
    metricas = Metricas()
    composicao = Composicao(
        banco,
        relogio,
        PublicadorEventosComMetricas(
            publicador or PublicadorEventosLog(), acoes_metricas_negocio(metricas)
        ),
    )
    dep_catalogo, dep_pagamentos = composicao.dependencias()

    cliente_vendas: ClienteServico | None = None
    if entregador is None:
        cliente_vendas = ClienteServico(
            settings.vendas_url,
            settings.token_servico_saida.get_secret_value(),
            timeout_segundos=settings.outbox_timeout_segundos,
        )
        entregador = cliente_vendas
    relay = RelayOutbox(
        banco,
        entregador,
        relogio,
        lote=settings.outbox_lote,
        prazo_entrega=timedelta(hours=settings.outbox_prazo_entrega_horas),
        metricas=metricas,
    )
    limpeza = nova_limpeza(
        banco,
        relogio,
        retencao_outbox=timedelta(days=settings.outbox_retencao_dias),
        retencao_compensacoes=timedelta(days=settings.compensacoes_retencao_dias),
    )
    executor = ExecutorRelay(relay, settings.outbox_intervalo_segundos, limpeza=limpeza.executar)

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
        if settings.outbox_relay_ativo:
            executor.iniciar()
        try:
            yield
        finally:
            executor.parar()
            if cliente_vendas is not None:
                cliente_vendas.fechar()

    app = FastAPI(
        title="Revenda de Veículos: serviço principal",
        version=__version__,
        description=DESCRICAO,
        openapi_tags=TAGS,
        lifespan=ciclo_de_vida,
        docs_url=f"{settings.docs_prefixo}/docs",
        openapi_url=f"{settings.docs_prefixo}/openapi.json",
        redoc_url=None,
    )
    # O último adicionado é o mais externo: a correlação envolve as métricas, então o 500
    # que ela produz para uma exceção também é contado.
    app.add_middleware(MetricasMiddleware, metricas=metricas)
    app.add_middleware(CorrelacaoMiddleware)
    registrar_tratadores_globais(app)
    registrar_problemas(app, {**PROBLEMAS_CATALOGO, **PROBLEMAS_PAGAMENTOS})

    exigir_gestor = exigir_consumidor(settings.consumidor_gestor)
    exigir_servico = exigir_token_servico(settings.token_servico_entrada.get_secret_value())

    api = APIRouter(prefix=PREFIXO_API)
    api.include_router(criar_router_veiculos(exigir_gestor, dep_catalogo))
    api.include_router(
        criar_router_pagamentos(settings.webhook_secret.get_secret_value(), dep_pagamentos)
    )
    interno = APIRouter(prefix=PREFIXO_INTERNO)
    interno.include_router(criar_router_interno_veiculos(exigir_servico, dep_catalogo))
    interno.include_router(criar_router_interno_cobrancas(exigir_servico, dep_pagamentos))
    app.include_router(api)
    app.include_router(interno)
    app.include_router(criar_router_saude(banco.disponivel))
    # Sem rota no Kong; o Prometheus coleta pela rede interna do cluster.
    app.add_route("/metrics", metricas.endpoint(), methods=["GET"], include_in_schema=False)

    _incluir_esquema_problema(app)
    configurar_tracing(NOME_SERVICO, app=app, engine=banco.engine)
    app.state.banco = banco
    app.state.metricas = metricas
    app.state.relay = relay
    return app


def _incluir_esquema_problema(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            esquema = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
                tags=app.openapi_tags,
            )
            componentes = esquema.setdefault("components", {}).setdefault("schemas", {})
            componentes["Problema"] = Problema.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            componentes.update(componentes["Problema"].pop("$defs", {}))
            app.openapi_schema = esquema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


_app: FastAPI | None = None


def __getattr__(nome: str) -> Any:
    # `app` é criado sob demanda (PEP 562): importar principal.main nos testes ou no Alembic
    # não exige as variáveis de ambiente da aplicação; o uvicorn acessa `principal.main:app`.
    global _app
    if nome == "app":
        if _app is None:
            _app = criar_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")
