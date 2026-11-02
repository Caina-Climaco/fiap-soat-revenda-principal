"""Implementação da porta `Outbox`: grava a mensagem na transação da sessão corrente."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, insert
from sqlalchemy.orm import Session

from principal.mensageria.relay import ENTREGUE
from principal.mensageria.tabelas import outbox
from principal.shared.clock import Clock
from principal.shared.outbox import MensagemSaida
from principal.shared.tracing import contexto_atual


class OutboxSql:
    def __init__(self, sessao: Session, relogio: Clock) -> None:
        self._sessao = sessao
        self._relogio = relogio

    def registrar(self, mensagem: MensagemSaida) -> None:
        agora = self._relogio.agora()
        self._sessao.execute(
            insert(outbox).values(
                tipo=mensagem.tipo,
                chave=mensagem.chave,
                metodo=mensagem.metodo,
                caminho=mensagem.caminho,
                corpo=mensagem.corpo,
                # O trace da requisição que originou a mensagem continua na entrega (ADR-007).
                contexto_trace=contexto_atual() or None,
                proxima_tentativa=agora,
                criado_em=agora,
            )
        )


def apagar_entregues_anteriores(sessao: Session, limite: datetime) -> int:
    """Retenção da outbox: apaga as mensagens ENTREGUE antes do limite (nunca as pendentes
    nem as FALHOU, que esperam reprocessamento manual)."""
    resultado = cast(
        CursorResult[Any],
        sessao.execute(
            delete(outbox).where(outbox.c.status == ENTREGUE, outbox.c.entregue_em < limite)
        ),
    )
    return int(resultado.rowcount)
