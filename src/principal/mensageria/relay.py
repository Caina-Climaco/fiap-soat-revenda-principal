"""Relay da outbox (ADR-003): entrega as mensagens pendentes ao serviço de venda por HTTP.

- Cada mensagem é entregue numa transação própria: a linha é travada com
  `FOR UPDATE SKIP LOCKED`, então duas réplicas do principal nunca entregam a mesma mensagem.
- Ordem por agregado: uma mensagem só sai se não houver outra pendente mais antiga com a
  mesma chave (a réplica de um veículo nunca chega fora de ordem).
- 2xx: entregue. Erro de rede, 404, 408, 425, 429 e 5xx: nova tentativa com backoff
  exponencial (até 60 s), sem limite de tentativas: só desiste quando a mensagem passa do
  prazo de entrega (padrão 24 h), para que uma queda longa do serviço de venda não perca
  réplicas nem resultados de pagamento. Demais 4xx (erro de contrato, não adianta repetir)
  ou prazo vencido: FALHOU (alerta e métrica), reprocessável depois da correção
  (`python -m principal.outbox reprocessar`).
- O gauge de pendentes é atualizado quando o ciclo entregou algo ou a cada 10 s, não com
  um `count(*)` a cada ciclo ocioso.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.orm import aliased

from principal.mensageria.tabelas import outbox
from principal.shared.cliente_servico import FalhaDeRedeError, RespostaServico
from principal.shared.clock import Clock
from principal.shared.db import BancoDeDados
from principal.shared.metricas import Metricas
from principal.shared.tracing import contexto_atual, contexto_de, tracer

_logger = logging.getLogger("principal.outbox")

PENDENTE = "PENDENTE"
ENTREGUE = "ENTREGUE"
FALHOU = "FALHOU"
_TRANSITORIOS = frozenset({404, 408, 425, 429})
_BACKOFF_MAXIMO = 60.0
PRAZO_ENTREGA_PADRAO = timedelta(hours=24)
_INTERVALO_GAUGE_SEGUNDOS = 10.0


class Entregador(Protocol):
    def enviar(
        self,
        metodo: str,
        caminho: str,
        corpo: Mapping[str, object],
        cabecalhos: Mapping[str, str] | None = None,
    ) -> RespostaServico: ...


def backoff(tentativas: int) -> timedelta:
    """0,5 s, 1 s, 2 s, 4 s... até 60 s."""
    return timedelta(seconds=min(0.5 * 2 ** max(tentativas - 1, 0), _BACKOFF_MAXIMO))


def transitorio(status: int) -> bool:
    return status >= 500 or status in _TRANSITORIOS


def definitivo(status: int, idade: timedelta, prazo: timedelta) -> bool:
    """FALHOU: 4xx que não adianta repetir, ou a mensagem passou do prazo de entrega.

    `status` 0 é erro de rede. Rede e 5xx nunca esgotam por número de tentativas.
    """
    return (status != 0 and not transitorio(status)) or idade >= prazo


class RelayOutbox:
    def __init__(
        self,
        banco: BancoDeDados,
        entregador: Entregador,
        relogio: Clock,
        *,
        lote: int = 50,
        prazo_entrega: timedelta = PRAZO_ENTREGA_PADRAO,
        metricas: Metricas | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._banco = banco
        self._entregador = entregador
        self._relogio = relogio
        self._lote = lote
        self._prazo = prazo_entrega
        self._metricas = metricas
        self._monotonic = monotonic
        self._gauge_em: float | None = None

    def entregar_pendentes(self) -> int:
        """Processa até `lote` mensagens prontas; devolve quantas foram processadas."""
        processadas = 0
        while processadas < self._lote and self._entregar_uma():
            processadas += 1
        self._atualizar_gauge(processadas)
        return processadas

    def _atualizar_gauge(self, processadas: int) -> None:
        if self._metricas is None:
            return
        agora = self._monotonic()
        ocioso_recente = (
            self._gauge_em is not None and agora - self._gauge_em < _INTERVALO_GAUGE_SEGUNDOS
        )
        if processadas == 0 and ocioso_recente:
            return
        self._metricas.outbox_pendentes.set(self.pendentes())
        self._gauge_em = agora

    def pendentes(self) -> int:
        with self._banco.nova_sessao() as sessao:
            total = sessao.execute(
                select(func.count()).select_from(outbox).where(outbox.c.status == PENDENTE)
            ).scalar_one()
        return int(total)

    def _entregar_uma(self) -> bool:
        with self._banco.nova_sessao() as sessao:
            mensagem = sessao.execute(self._proxima()).mappings().first()
            if mensagem is None:
                return False
            valores = self._tentar(dict(mensagem))
            sessao.execute(update(outbox).where(outbox.c.id == mensagem["id"]).values(**valores))
            sessao.commit()
        return True

    def _proxima(self) -> Any:
        anterior = aliased(outbox)
        mais_antiga_pendente = exists().where(
            and_(
                anterior.c.chave == outbox.c.chave,
                anterior.c.status == PENDENTE,
                anterior.c.id < outbox.c.id,
            )
        )
        return (
            select(outbox)
            .where(
                outbox.c.status == PENDENTE,
                outbox.c.proxima_tentativa <= self._relogio.agora(),
                ~mais_antiga_pendente,
            )
            .order_by(outbox.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def _tentar(self, mensagem: Mapping[str, Any]) -> dict[str, Any]:
        tentativas = int(mensagem["tentativas"]) + 1
        agora = self._relogio.agora()
        contexto = contexto_de(mensagem["contexto_trace"] or {})
        with tracer.start_as_current_span(
            f"outbox.entregar {mensagem['tipo']}",
            context=contexto,
            attributes={"outbox.tipo": mensagem["tipo"], "outbox.tentativa": tentativas},
        ):
            try:
                resposta = self._entregador.enviar(
                    mensagem["metodo"], mensagem["caminho"], mensagem["corpo"], contexto_atual()
                )
                status, erro = resposta.status, resposta.corpo
            except FalhaDeRedeError as exc:
                status, erro = 0, f"erro de rede: {exc}"

        if 200 <= status < 300:
            self._registrar_metrica(mensagem, "entregue", agora)
            return {"status": ENTREGUE, "tentativas": tentativas, "entregue_em": agora}

        descricao = f"HTTP {status}: {erro}" if status else erro
        if definitivo(status, agora - mensagem["criado_em"], self._prazo):
            _logger.error(
                "mensagem da outbox sem entrega",
                extra={
                    "campos": {
                        "outbox_id": mensagem["id"],
                        "tipo": mensagem["tipo"],
                        "tentativas": tentativas,
                        "erro": descricao[:200],
                    }
                },
            )
            self._registrar_metrica(mensagem, "falhou", agora)
            return {"status": FALHOU, "tentativas": tentativas, "ultimo_erro": descricao[:500]}
        _logger.warning(
            "entrega da outbox adiada",
            extra={
                "campos": {
                    "outbox_id": mensagem["id"],
                    "tipo": mensagem["tipo"],
                    "tentativas": tentativas,
                    "erro": descricao[:200],
                }
            },
        )
        self._registrar_metrica(mensagem, "adiada", agora)
        return {
            "tentativas": tentativas,
            "ultimo_erro": descricao[:500],
            "proxima_tentativa": agora + backoff(tentativas),
        }

    def _registrar_metrica(self, mensagem: Mapping[str, Any], resultado: str, agora: Any) -> None:
        if self._metricas is None:
            return
        self._metricas.outbox_entregas.labels(tipo=mensagem["tipo"], resultado=resultado).inc()
        if resultado == "entregue":
            atraso = (agora - mensagem["criado_em"]).total_seconds()
            self._metricas.outbox_atraso.labels(tipo=mensagem["tipo"]).observe(max(atraso, 0.0))


class ExecutorRelay:
    """Laço em segundo plano (thread) que chama o relay até ser parado.

    Opcionalmente executa uma tarefa de manutenção (`limpeza`) a cada `intervalo_limpeza`
    segundos, no mesmo laço; uma falha nela só é registrada.
    """

    def __init__(
        self,
        relay: RelayOutbox,
        intervalo_segundos: float,
        *,
        limpeza: Callable[[], object] | None = None,
        intervalo_limpeza_segundos: float = 3600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._relay = relay
        self._intervalo = intervalo_segundos
        self._limpeza = limpeza
        self._intervalo_limpeza = intervalo_limpeza_segundos
        self._monotonic = monotonic
        self._limpeza_em: float | None = None
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    def iniciar(self) -> None:
        self._thread = threading.Thread(target=self._laco, name="relay-outbox", daemon=True)
        self._thread.start()

    def parar(self, timeout: float = 5.0) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                processadas = self._relay.entregar_pendentes()
            except Exception:  # o laço nunca morre: banco fora volta a tentar no próximo ciclo
                _logger.exception("falha no ciclo do relay da outbox")
                processadas = 0
            self._limpar_se_for_a_hora()
            if processadas == 0:
                self._parar.wait(self._intervalo)

    def _limpar_se_for_a_hora(self) -> None:
        if self._limpeza is None:
            return
        agora = self._monotonic()
        if self._limpeza_em is not None and agora - self._limpeza_em < self._intervalo_limpeza:
            return
        self._limpeza_em = agora
        try:
            self._limpeza()
        except Exception:  # tenta de novo na próxima janela
            _logger.exception("falha na limpeza periódica")
