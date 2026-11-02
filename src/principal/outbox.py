"""Reprocessamento da outbox (ADR-003): `python -m principal.outbox reprocessar [ID ...]`.

Uma mensagem vira FALHOU quando o serviço de venda a recusa com um 4xx que não adianta
repetir (erro de contrato) ou quando passa do prazo de entrega. Depois de corrigida a
causa, este comando a devolve a PENDENTE com `tentativas = 0` e `criado_em = agora`, e o
relay a entrega no próximo ciclo, respeitando a ordem por agregado. Sem IDs, reprocessa
todas as FALHOU. O `criado_em` é renovado porque o prazo de entrega conta a partir dele:
sem isso, uma mensagem que falhou por prazo vencido voltaria já vencida e viraria FALHOU
de novo na primeira falha de rede.

No cluster (mesmas variáveis de banco da API):

    kubectl -n principal exec deployment/revenda-principal -- python -m principal.outbox reprocessar
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, update

from principal.mensageria.relay import FALHOU, PENDENTE
from principal.mensageria.tabelas import outbox
from principal.shared.clock import Clock, RelogioSistema
from principal.shared.config import DatabaseSettings
from principal.shared.db import BancoDeDados, criar_engine
from principal.shared.logging import configurar_logs

_logger = logging.getLogger("principal.outbox")


def reprocessar(banco: BancoDeDados, agora: datetime, ids: Sequence[int] = ()) -> int:
    """Devolve as mensagens FALHOU (todas, ou só as dos `ids`) a PENDENTE; quantas mudaram."""
    comando = update(outbox).where(outbox.c.status == FALHOU)
    if ids:
        comando = comando.where(outbox.c.id.in_(ids))
    with banco.nova_sessao() as sessao:
        resultado = cast(
            CursorResult[Any],
            sessao.execute(
                comando.values(
                    status=PENDENTE,
                    tentativas=0,
                    proxima_tentativa=agora,
                    ultimo_erro=None,
                    criado_em=agora,  # o prazo de entrega recomeça no reprocessamento
                )
            ),
        )
        sessao.commit()
    return int(resultado.rowcount)


def main(
    argv: Sequence[str] | None = None,
    *,
    banco: BancoDeDados | None = None,
    relogio: Clock | None = None,
) -> int:
    leitor = argparse.ArgumentParser(prog="python -m principal.outbox")
    acoes = leitor.add_subparsers(dest="acao", required=True)
    acao = acoes.add_parser("reprocessar", help="devolve mensagens FALHOU a PENDENTE")
    acao.add_argument("ids", nargs="*", type=int, help="IDs da outbox (padrão: todas FALHOU)")
    argumentos = leitor.parse_args(argv)

    configurar_logs()
    proprio = banco is None
    banco = banco or BancoDeDados(
        criar_engine(DatabaseSettings().url_banco(), pool_size=1, max_overflow=0)
    )
    try:
        quantas = reprocessar(banco, (relogio or RelogioSistema()).agora(), argumentos.ids)
    finally:
        if proprio:
            banco.engine.dispose()
    _logger.info(
        "mensagens da outbox devolvidas a PENDENTE",
        extra={"campos": {"reprocessadas": quantas, "ids": argumentos.ids or "todas FALHOU"}},
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - ponto de entrada manual
    raise SystemExit(main(sys.argv[1:]))
