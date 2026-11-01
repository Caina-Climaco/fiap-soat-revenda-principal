"""Rotas HTTP de Pagamentos: webhook do processador e rotas internas da saga (T3, C3)."""

# Sem `from __future__ import annotations` neste módulo: as dependências usam aliases
# `Annotated[..., Depends(...)]` locais à fábrica, e o FastAPI só os resolve se as
# anotações forem avaliadas na definição da função (com o escopo local).

import hmac
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Response, status
from fastapi.security import APIKeyHeader

from principal.pagamentos.application.casos_uso import (
    CasosUsoPagamentos,
    FabricaCasosUsoPagamentos,
    ResultadoCobranca,
)
from principal.pagamentos.domain.erros import (
    CobrancaJaPagaError,
    CobrancaNaoEncontradaError,
    CompraCompensadaError,
    PagamentoAposCancelamentoError,
    ValorInvalidoError,
)
from principal.pagamentos.interfaces.esquemas import (
    CobrancaRequisicao,
    CobrancaResposta,
    NotificacaoPagamento,
    RespostaWebhook,
)
from principal.shared.errors import VALIDACAO, TipoProblema, WebhookNaoAutorizadoError
from principal.shared.http import respostas_problema

PROBLEMAS_PAGAMENTOS: dict[type[Exception], TipoProblema] = {
    CobrancaNaoEncontradaError: TipoProblema(
        "cobranca-nao-encontrada", "Cobrança não encontrada", 404
    ),
    CobrancaJaPagaError: TipoProblema("cobranca-ja-paga", "Cobrança já paga", 409),
    PagamentoAposCancelamentoError: TipoProblema(
        "pagamento-apos-cancelamento", "Pagamento após o cancelamento", 409
    ),
    CompraCompensadaError: TipoProblema("compra-compensada", "Compra já compensada", 409),
    ValorInvalidoError: VALIDACAO,
}


def criar_router_pagamentos(
    segredo_webhook: str, casos_uso: FabricaCasosUsoPagamentos
) -> APIRouter:
    """Camada anticorrupção do processador: autentica e traduz o payload em comando."""
    router = APIRouter(prefix="/pagamentos", tags=["Pagamentos (processador)"])
    esquema = APIKeyHeader(
        name="X-Webhook-Secret",
        scheme_name="webhook",
        description="Segredo compartilhado com o processador de pagamentos.",
        auto_error=False,
    )
    esperado = segredo_webhook.encode()

    def verificar_segredo(recebido: Annotated[str | None, Depends(esquema)]) -> None:
        # Comparação em tempo constante; ausente e incorreto respondem igual (RN-13).
        if not recebido or not hmac.compare_digest(recebido.encode(), esperado):
            raise WebhookNaoAutorizadoError("Segredo do webhook ausente ou inválido.")

    CasosUso = Annotated[CasosUsoPagamentos, Depends(casos_uso)]

    @router.post(
        "/webhook",
        summary="Resultado do pagamento (processador)",
        description=(
            "Chamado pelo processador, a partir do código do pagamento. PAGO registra o "
            "pagamento efetuado (a compra é efetivada no serviço de venda); CANCELADO "
            "cancela a cobrança (a compra é cancelada e o veículo volta à vitrine). "
            "Notificações repetidas respondem 200 sem efeito; PAGO para cobrança já "
            "cancelada responde 409 e fica registrado para estorno."
        ),
        response_model=RespostaWebhook,
        dependencies=[Depends(verificar_segredo)],
        responses=respostas_problema(400, 401, 404, 409, 422, s404="Código desconhecido"),
    )
    def webhook(uc: CasosUso, notificacao: NotificacaoPagamento) -> RespostaWebhook:
        resultado = uc.registrar_resultado.executar(
            notificacao.codigo_pagamento, pago=notificacao.status == "PAGO"
        )
        return RespostaWebhook(
            codigo_pagamento=str(resultado.cobranca.codigo),
            status_cobranca=resultado.cobranca.status,
            efeito="APLICADO" if resultado.efeito else "SEM_EFEITO",
        )

    return router


def _cobranca(resultado: ResultadoCobranca) -> CobrancaResposta:
    cobranca = resultado.cobranca
    return CobrancaResposta(
        codigo_pagamento=str(cobranca.codigo),
        compra_id=cobranca.compra_id,
        valor=cobranca.valor,
        status=cobranca.status,
        efeito=resultado.efeito,
    )


def criar_router_interno_cobrancas(
    exigir_servico: Callable[..., Any], casos_uso: FabricaCasosUsoPagamentos
) -> APIRouter:
    router = APIRouter(
        prefix="/cobrancas",
        tags=["Interno: saga da compra"],
        dependencies=[Depends(exigir_servico)],
    )
    CasosUso = Annotated[CasosUsoPagamentos, Depends(casos_uso)]

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        summary="T3: gerar a cobrança da compra",
        description=(
            "Idempotente pela compra_id: a primeira chamada responde 201; repetições "
            "respondem 200 com a mesma cobrança. Depois da compensação, 409."
        ),
        response_model=CobrancaResposta,
        responses={
            200: {
                "model": CobrancaResposta,
                "description": "Repetição: a cobrança já existia (efeito false)",
            },
            **respostas_problema(401, 409, 422),
        },
    )
    def gerar(uc: CasosUso, corpo: CobrancaRequisicao, response: Response) -> CobrancaResposta:
        resultado = uc.gerar.executar(corpo.compra_id, corpo.valor)
        if not resultado.efeito:
            response.status_code = status.HTTP_200_OK
        return _cobranca(resultado)

    @router.post(
        "/{compra_id}/cancelamento",
        summary="C3: cancelar a cobrança da compra",
        description=(
            "Compensação. Cobrança já paga → 409 cobranca-ja-paga (o pivô aconteceu e a "
            "saga deve seguir em frente). Sem cobrança, grava a marca de compensação."
        ),
        response_model=CobrancaResposta,
        responses=respostas_problema(401, 409, 422),
    )
    def cancelar(
        uc: CasosUso,
        compra_id: Annotated[UUID, Path(description="Identificador da compra (UUID).")],
    ) -> CobrancaResposta:
        return _cobranca(uc.cancelar.executar(compra_id))

    return router
