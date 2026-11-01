"""Rotas HTTP do Catálogo: /api/v1/veiculos (gestor) e /internal/v1/veiculos (saga)."""

# Sem `from __future__ import annotations` neste módulo: as dependências usam aliases
# `Annotated[..., Depends(...)]` locais à fábrica, e o FastAPI só os resolve se as
# anotações forem avaliadas na definição da função (com o escopo local).

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from principal.catalogo.application.casos_uso import (
    CasosUsoCatalogo,
    DadosCadastro,
    DadosEdicao,
    FabricaCasosUsoCatalogo,
    ResultadoPasso,
)
from principal.catalogo.domain.erros import (
    BloqueioDeOutraCompraError,
    CompraCompensadaError,
    DadosVeiculoInvalidosError,
    VeiculoIndisponivelError,
    VeiculoJaVendidoError,
    VeiculoNaoEditavelError,
    VeiculoNaoEncontradoError,
    VersaoDivergenteError,
)
from principal.catalogo.domain.veiculo import StatusVeiculo
from principal.catalogo.interfaces.esquemas import (
    BloqueioRequisicao,
    PaginaVeiculos,
    PassoSagaResposta,
    VeiculoCriacao,
    VeiculoEdicao,
    VeiculoResposta,
    VendaRequisicao,
)
from principal.shared.errors import VALIDACAO, TipoProblema
from principal.shared.http import ParametrosPaginacao, paginacao, respostas_problema

PROBLEMAS_CATALOGO: dict[type[Exception], TipoProblema] = {
    DadosVeiculoInvalidosError: VALIDACAO,
    VeiculoNaoEncontradoError: TipoProblema(
        "veiculo-nao-encontrado", "Veículo não encontrado", 404
    ),
    VeiculoNaoEditavelError: TipoProblema("veiculo-nao-editavel", "Veículo não editável", 409),
    VeiculoIndisponivelError: TipoProblema("veiculo-indisponivel", "Veículo indisponível", 409),
    VersaoDivergenteError: TipoProblema("versao-divergente", "Versão do anúncio divergente", 409),
    VeiculoJaVendidoError: TipoProblema("veiculo-ja-vendido", "Veículo já vendido", 409),
    BloqueioDeOutraCompraError: TipoProblema(
        "bloqueio-de-outra-compra", "Veículo bloqueado por outra compra", 409
    ),
    CompraCompensadaError: TipoProblema("compra-compensada", "Compra já compensada", 409),
}

IdVeiculo = Annotated[UUID, Path(description="Identificador do veículo (UUID).")]
IdCompra = Annotated[UUID, Path(description="Identificador da compra (UUID).")]


def criar_router_veiculos(
    exigir_gestor: Callable[..., str], casos_uso: FabricaCasosUsoCatalogo
) -> APIRouter:
    """Rotas públicas (pelo Kong), todas do consumer `gestor`."""
    router = APIRouter(
        prefix="/veiculos", tags=["Veículos (gestor)"], dependencies=[Depends(exigir_gestor)]
    )
    CasosUso = Annotated[CasosUsoCatalogo, Depends(casos_uso)]
    Paginacao = Annotated[ParametrosPaginacao, Depends(paginacao)]

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        summary="Cadastrar veículo",
        description=(
            "O veículo nasce à venda (A_VENDA), com versão 1, e é publicado na vitrine do "
            "serviço de venda pela outbox."
        ),
        response_model=VeiculoResposta,
        responses=respostas_problema(400, 401, 403, 422),
    )
    def cadastrar(
        uc: CasosUso, corpo: VeiculoCriacao, request: Request, response: Response
    ) -> VeiculoResposta:
        veiculo = uc.cadastrar.executar(DadosCadastro(**corpo.model_dump()))
        response.headers["Location"] = f"{request.url.path.rstrip('/')}/{veiculo.id}"
        return VeiculoResposta.model_validate(veiculo)

    @router.patch(
        "/{veiculo_id}",
        summary="Editar veículo",
        description=(
            "Atualização parcial de marca, modelo, ano, cor e/ou preço; só com o veículo à "
            "venda (bloqueado por uma compra ou vendido → 409). A nova versão é publicada "
            "na vitrine."
        ),
        response_model=VeiculoResposta,
        responses=respostas_problema(400, 401, 403, 404, 409, 422),
    )
    def editar(uc: CasosUso, veiculo_id: IdVeiculo, corpo: VeiculoEdicao) -> VeiculoResposta:
        dados = DadosEdicao(**corpo.model_dump(exclude_unset=True))
        return VeiculoResposta.model_validate(uc.editar.executar(veiculo_id, dados))

    @router.get(
        "/{veiculo_id}",
        summary="Consultar veículo",
        description="Retorna o veículo em qualquer status.",
        response_model=VeiculoResposta,
        responses=respostas_problema(401, 403, 404, 422),
    )
    def obter(uc: CasosUso, veiculo_id: IdVeiculo) -> VeiculoResposta:
        return VeiculoResposta.model_validate(uc.obter.executar(veiculo_id))

    @router.get(
        "",
        summary="Listar veículos (visão da loja)",
        description="Todos os veículos, do mais recente ao mais antigo, com filtro por status.",
        response_model=PaginaVeiculos,
        responses=respostas_problema(401, 403, 422),
    )
    def listar(
        uc: CasosUso,
        pag: Paginacao,
        status_veiculo: Annotated[
            StatusVeiculo | None, Query(alias="status", description="Filtra pelo status.")
        ] = None,
    ) -> PaginaVeiculos:
        pagina = uc.listar.executar(
            status=status_veiculo, limite=pag.limite, deslocamento=pag.deslocamento
        )
        return PaginaVeiculos.model_validate(pagina)

    return router


def _passo(resultado: ResultadoPasso) -> PassoSagaResposta:
    veiculo = resultado.veiculo
    return PassoSagaResposta(
        veiculo_id=veiculo.id,
        status=veiculo.status,
        versao=veiculo.versao,
        compra_id=veiculo.compra_id,
        efeito=resultado.efeito,
    )


def criar_router_interno_veiculos(
    exigir_servico: Callable[..., Any], casos_uso: FabricaCasosUsoCatalogo
) -> APIRouter:
    """Rotas internas chamadas pela saga do serviço de venda (docs/05-api.md, seção 5.6)."""
    router = APIRouter(
        prefix="/veiculos",
        tags=["Interno: saga da compra"],
        dependencies=[Depends(exigir_servico)],
    )
    CasosUso = Annotated[CasosUsoCatalogo, Depends(casos_uso)]

    @router.post(
        "/{veiculo_id}/bloqueio",
        summary="T2: bloquear veículo para a compra",
        description="Idempotente pela compra_id; confere a versão vista pelo comprador.",
        response_model=PassoSagaResposta,
        responses=respostas_problema(401, 404, 409, 422),
    )
    def bloquear(
        uc: CasosUso, veiculo_id: IdVeiculo, corpo: BloqueioRequisicao
    ) -> PassoSagaResposta:
        return _passo(uc.bloquear.executar(veiculo_id, corpo.compra_id, corpo.versao))

    @router.delete(
        "/{veiculo_id}/bloqueio/{compra_id}",
        summary="C2: desfazer o bloqueio da compra",
        description=(
            "Compensação. Sem bloqueio dessa compra, responde 200 sem efeito; em qualquer "
            "caso, a compra passa a ser recusada em novos pedidos de bloqueio."
        ),
        response_model=PassoSagaResposta,
        responses=respostas_problema(401, 404, 409, 422),
    )
    def desbloquear(uc: CasosUso, veiculo_id: IdVeiculo, compra_id: IdCompra) -> PassoSagaResposta:
        return _passo(uc.desbloquear.executar(veiculo_id, compra_id))

    @router.post(
        "/{veiculo_id}/venda",
        summary="T6: marcar o veículo como vendido",
        description="Passo retentável (depois do pivô); idempotente pela compra_id.",
        response_model=PassoSagaResposta,
        responses=respostas_problema(401, 404, 409, 422),
    )
    def vender(uc: CasosUso, veiculo_id: IdVeiculo, corpo: VendaRequisicao) -> PassoSagaResposta:
        return _passo(uc.vender.executar(veiculo_id, corpo.compra_id))

    return router
