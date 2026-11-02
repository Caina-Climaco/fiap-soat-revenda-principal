"""Regras puras do relay da outbox e o laço em segundo plano."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from principal.mensageria.relay import ExecutorRelay, backoff, definitivo, transitorio


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


DIA = timedelta(hours=24)


@pytest.mark.parametrize(
    ("status", "idade", "esperado"),
    [
        (0, timedelta(minutes=10), False),  # rede: repete
        (503, timedelta(hours=23), False),  # 5xx dentro do prazo: repete, mesmo na 20ª
        (404, timedelta(hours=1), False),
        (422, timedelta(seconds=1), True),  # erro de contrato: não adianta repetir
        (0, DIA, True),  # prazo vencido: desiste
        (503, DIA + timedelta(seconds=1), True),
    ],
)
def test_definitivo_so_por_4xx_ou_prazo_vencido(
    status: int, idade: timedelta, esperado: bool
) -> None:
    assert definitivo(status, idade, DIA) is esperado


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


def test_executor_limpa_na_primeira_volta_e_depois_so_a_cada_intervalo() -> None:
    """Item 4.7 da revisão: limpeza no laço do relay, com falha que não derruba o laço."""
    instante = [0.0]
    limpezas: list[float] = []

    def limpar() -> None:
        limpezas.append(instante[0])
        if len(limpezas) == 2:
            raise RuntimeError("banco fora durante a limpeza")

    executor = ExecutorRelay(
        RelayDeMentira(),  # type: ignore[arg-type]
        intervalo_segundos=0.01,
        limpeza=limpar,
        intervalo_limpeza_segundos=3600,
        monotonic=lambda: instante[0],
    )
    executor._limpar_se_for_a_hora()
    instante[0] = 1800.0
    executor._limpar_se_for_a_hora()  # dentro da hora: não limpa
    instante[0] = 3600.0
    executor._limpar_se_for_a_hora()  # falha: só registra
    instante[0] = 7200.0
    executor._limpar_se_for_a_hora()
    assert limpezas == [0.0, 3600.0, 7200.0]
    sem_limpeza = ExecutorRelay(RelayDeMentira(), intervalo_segundos=0.01)  # type: ignore[arg-type]
    sem_limpeza._limpar_se_for_a_hora()  # sem tarefa configurada: nada a fazer
