"""Implementação da porta `Outbox`: grava a mensagem na transação da sessão corrente."""

from __future__ import annotations

from sqlalchemy import insert
from sqlalchemy.orm import Session

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
