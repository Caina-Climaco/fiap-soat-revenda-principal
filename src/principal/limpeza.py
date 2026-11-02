"""Limpeza periódica do banco do principal (ADR-003 e docs/06, retenção).

- `mensageria.outbox`: mensagens ENTREGUE há mais de 7 dias. Pendentes e FALHOU nunca são
  apagadas (as FALHOU esperam reprocessamento manual).
- `catalogo.compensacoes`: marcas da regra contra ressurreição com mais de 30 dias. A marca
  só serve para recusar um T2 atrasado da mesma compra, e nenhuma requisição da saga chega
  com dias de atraso (timeout de 2 s, e T2 sem resposta cancela a compra).

Roda no laço do relay (`ExecutorRelay`), uma vez por hora e por réplica; apagar o que já foi
apagado não tem efeito, então duas réplicas não conflitam. Assim não é preciso outro
CronJob nem outro deploy.

Este módulo não conhece as tabelas: cada contexto apaga o que é seu (a outbox na
mensageria, as marcas no repositório do catálogo), e só `composicao.py` liga as peças.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from principal.shared.clock import Clock
from principal.shared.db import BancoDeDados

_logger = logging.getLogger("principal.limpeza")

# Apaga, na sessão recebida, o que é anterior ao limite; devolve quantas linhas apagou.
ApagarAnteriores = Callable[[Session, datetime], int]


@dataclass(frozen=True, slots=True)
class ResultadoLimpeza:
    mensagens_entregues: int
    compensacoes: int


class LimpezaPeriodica:
    def __init__(
        self,
        banco: BancoDeDados,
        relogio: Clock,
        *,
        apagar_entregues: ApagarAnteriores,
        apagar_compensacoes: ApagarAnteriores,
        retencao_outbox: timedelta = timedelta(days=7),
        retencao_compensacoes: timedelta = timedelta(days=30),
    ) -> None:
        self._banco = banco
        self._relogio = relogio
        self._apagar_entregues = apagar_entregues
        self._apagar_compensacoes = apagar_compensacoes
        self._retencao_outbox = retencao_outbox
        self._retencao_compensacoes = retencao_compensacoes

    def executar(self) -> ResultadoLimpeza:
        agora = self._relogio.agora()
        with self._banco.nova_sessao() as sessao:
            resultado = ResultadoLimpeza(
                mensagens_entregues=self._apagar_entregues(sessao, agora - self._retencao_outbox),
                compensacoes=self._apagar_compensacoes(sessao, agora - self._retencao_compensacoes),
            )
            sessao.commit()
        if resultado.mensagens_entregues or resultado.compensacoes:
            _logger.info(
                "limpeza periódica",
                extra={
                    "campos": {
                        "mensagens_entregues": resultado.mensagens_entregues,
                        "compensacoes": resultado.compensacoes,
                    }
                },
            )
        return resultado
