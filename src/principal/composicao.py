"""Composição: o único lugar que conhece os dois contextos e a outbox ao mesmo tempo.

Cada requisição recebe uma sessão; repositórios, outbox, Unit of Work e casos de uso são
montados sobre ela, de modo que a mudança de negócio e a mensagem para o serviço de venda
entram na mesma transação (ADR-003).
"""

# Sem `from __future__ import annotations` neste módulo: as dependências usam aliases
# `Annotated[..., Depends(...)]` locais à fábrica, e o FastAPI só os resolve se as
# anotações forem avaliadas na definição da função (com o escopo local).

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from principal.catalogo.application.casos_uso import (
    BloquearVeiculo,
    CadastrarVeiculo,
    CasosUsoCatalogo,
    DesbloquearVeiculo,
    EditarVeiculo,
    ListarVeiculos,
    ObterVeiculo,
    VenderVeiculo,
)
from principal.catalogo.domain.eventos import (
    VeiculoBloqueado,
    VeiculoCadastrado,
    VeiculoDesbloqueado,
    VeiculoEditado,
    VeiculoVendido,
)
from principal.catalogo.infrastructure.repositorio_sql import SqlVeiculoRepository
from principal.mensageria.outbox_sql import OutboxSql
from principal.pagamentos.application.casos_uso import (
    CancelarCobrancaDaCompra,
    CasosUsoPagamentos,
    GerarCobranca,
    RegistrarResultadoPagamento,
)
from principal.pagamentos.domain.eventos import (
    CobrancaCancelada,
    CobrancaGerada,
    CobrancaPaga,
    PagamentoAposCancelamento,
)
from principal.pagamentos.infrastructure.repositorio_sql import SqlCobrancaRepository
from principal.shared.clock import Clock
from principal.shared.db import BancoDeDados, SqlUnidadeDeTrabalho
from principal.shared.eventos import EventoDominio, PublicadorEventos
from principal.shared.metricas import AcaoMetrica, Metricas, rotulo_do_evento


class Composicao:
    def __init__(self, banco: BancoDeDados, relogio: Clock, publicador: PublicadorEventos) -> None:
        self._banco = banco
        self._relogio = relogio
        self._publicador = publicador

    def dependencias(
        self,
    ) -> tuple[Callable[..., CasosUsoCatalogo], Callable[..., CasosUsoPagamentos]]:
        """Dependências FastAPI que entregam os casos de uso de cada contexto."""

        def sessao() -> Iterator[Session]:
            yield from self._banco.sessao()

        Sessao = Annotated[Session, Depends(sessao)]

        def catalogo(sessao: Sessao) -> CasosUsoCatalogo:
            return self.casos_uso_catalogo(sessao)

        def pagamentos(sessao: Sessao) -> CasosUsoPagamentos:
            return self.casos_uso_pagamentos(sessao)

        return catalogo, pagamentos

    def casos_uso_catalogo(self, sessao: Session) -> CasosUsoCatalogo:
        uow = SqlUnidadeDeTrabalho(sessao, self._publicador)
        repo = SqlVeiculoRepository(sessao)
        outbox = OutboxSql(sessao, self._relogio)
        argumentos = (repo, uow, self._relogio, outbox)
        return CasosUsoCatalogo(
            cadastrar=CadastrarVeiculo(*argumentos),
            editar=EditarVeiculo(*argumentos),
            obter=ObterVeiculo(repo),
            listar=ListarVeiculos(repo),
            bloquear=BloquearVeiculo(*argumentos),
            desbloquear=DesbloquearVeiculo(*argumentos),
            vender=VenderVeiculo(*argumentos),
        )

    def casos_uso_pagamentos(self, sessao: Session) -> CasosUsoPagamentos:
        uow = SqlUnidadeDeTrabalho(sessao, self._publicador)
        argumentos = (
            SqlCobrancaRepository(sessao),
            uow,
            self._relogio,
            OutboxSql(sessao, self._relogio),
        )
        return CasosUsoPagamentos(
            gerar=GerarCobranca(*argumentos),
            cancelar=CancelarCobrancaDaCompra(*argumentos),
            registrar_resultado=RegistrarResultadoPagamento(*argumentos),
        )


def acoes_metricas_negocio(metricas: Metricas) -> dict[str, AcaoMetrica]:
    """Liga os eventos de domínio (já confirmados) aos contadores de negócio."""

    def passo(nome: str) -> AcaoMetrica:
        return lambda _evento: metricas.passos_saga.labels(passo=nome).inc()

    def cobranca(nome: str) -> AcaoMetrica:
        def acao(evento: EventoDominio) -> None:
            origem = rotulo_do_evento(evento, "origem", padrao="")
            metricas.cobrancas.labels(evento=nome, origem=origem).inc()

        return acao

    return {
        VeiculoCadastrado.NOME: lambda _evento: metricas.veiculos_cadastrados.inc(),
        VeiculoEditado.NOME: lambda _evento: metricas.veiculos_editados.inc(),
        VeiculoBloqueado.NOME: passo("bloqueio"),
        VeiculoDesbloqueado.NOME: passo("desbloqueio"),
        VeiculoVendido.NOME: passo("venda"),
        CobrancaGerada.NOME: cobranca("gerada"),
        CobrancaPaga.NOME: cobranca("paga"),
        CobrancaCancelada.NOME: cobranca("cancelada"),
        PagamentoAposCancelamento.NOME: cobranca("pagamento_apos_cancelamento"),
    }
