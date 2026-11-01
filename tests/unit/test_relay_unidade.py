"""Regras puras do relay da outbox e o laço em segundo plano."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from principal.mensageria.relay import ExecutorRelay, backoff, transitorio


@pytest.mark.parametrize(
    ("tentativas", "segundos"), [(1, 0.5), (2, 1.0), (3, 2.0), (8, 60.0), (30, 60.0)]
)
def test_backoff_exponencial_com_teto(tentativas: int, segundos: float) -> None:
    assert backoff(tentativas) == timedelta(seconds=segundos)


@pytest.mark.parametrize(
    ("status", "esperado"),
    [(500, True), (503, True), (404, True), (429, True), (408, True), (400, False), (409, False)],
)
def test_status_transitorios(status: int, esperado: bool) -> None:
    assert transitorio(status) is esperado


class RelayDeMentira:
    """Falha no primeiro ciclo, entrega no segundo e avisa quando já rodou três vezes."""

    def __init__(self) -> None:
        self.ciclos = 0
        self.pronto = threading.Event()

    def entregar_pendentes(self) -> int:
        self.ciclos += 1
        if self.ciclos >= 3:
            self.pronto.set()
        if self.ciclos == 1:
            raise RuntimeError("banco fora")
        return 1 if self.ciclos == 2 else 0


def test_executor_sobrevive_a_falhas_e_para() -> None:
    relay = RelayDeMentira()
    executor = ExecutorRelay(relay, intervalo_segundos=0.01)  # type: ignore[arg-type]
    executor.iniciar()
    assert relay.pronto.wait(5)
    executor.parar()
    ciclos = relay.ciclos
    assert ciclos >= 3
    assert executor._thread is not None
    assert not executor._thread.is_alive()


def test_parar_sem_iniciar_nao_falha() -> None:
    executor = ExecutorRelay(object(), 1.0)  # type: ignore[arg-type]
    executor.parar()
    assert executor._thread is None
