"""Limpeza periódica do banco do principal (item 4.7 da revisão).

- `mensageria.outbox`: mensagens ENTREGUE há mais de 7 dias. Pendentes e FALHOU nunca são
  apagadas (as FALHOU esperam reprocessamento manual).
- `catalogo.compensacoes`: marcas da regra contra ressurreição com mais de 30 dias. A marca
  só serve para recusar um T2 atrasado da mesma compra, e nenhuma requisição da saga chega
  com dias de atraso (timeout de 2 s, e T2 sem resposta cancela a compra).

Roda no laço do relay (`ExecutorRelay`), uma vez por hora e por réplica; apagar o que já foi
apagado não tem efeito, então duas réplicas não conflitam. Assim não é preciso outro
CronJob nem outro deploy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete

from principal.catalogo.infrastructure.tabelas import compensacoes
from principal.mensageria.relay import ENTREGUE
from principal.mensageria.tabelas import outbox
from principal.shared.clock import Clock
from principal.shared.db import BancoDeDados

_logger = logging.getLogger("principal.limpeza")


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
        retencao_outbox: timedelta = timedelta(days=7),
        retencao_compensacoes: timedelta = timedelta(days=30),
    ) -> None:
        self._banco = banco
        self._relogio = relogio
        self._retencao_outbox = retencao_outbox
        self._retencao_compensacoes = retencao_compensacoes

    def executar(self) -> ResultadoLimpeza:
        agora = self._relogio.agora()
        with self._banco.nova_sessao() as sessao:
            mensagens = cast(
                CursorResult[Any],
                sessao.execute(
                    delete(outbox).where(
                        outbox.c.status == ENTREGUE,
                        outbox.c.entregue_em < agora - self._retencao_outbox,
                    )
                ),
            )
            marcas = cast(
                CursorResult[Any],
                sessao.execute(
                    delete(compensacoes).where(
                        compensacoes.c.registrada_em < agora - self._retencao_compensacoes
                    )
                ),
            )
            sessao.commit()
        resultado = ResultadoLimpeza(int(mensagens.rowcount), int(marcas.rowcount))
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
