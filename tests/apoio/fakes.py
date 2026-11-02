"""Adaptadores em memória para os testes de unidade dos casos de uso."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from uuid import UUID

from principal.catalogo.domain.veiculo import StatusVeiculo, Veiculo
from principal.pagamentos.domain.cobranca import Cobranca
from principal.shared.cliente_servico import FalhaDeRedeError, RespostaServico
from principal.shared.eventos import EventoDominio
from principal.shared.outbox import MensagemSaida


class UowFalsa:
    def __init__(self) -> None:
        self.confirmacoes = 0
        self.eventos: list[EventoDominio] = []
        self._pendentes: list[EventoDominio] = []

    def registrar_eventos(self, eventos: Iterable[EventoDominio]) -> None:
        self._pendentes.extend(eventos)

    def confirmar(self) -> None:
        self.confirmacoes += 1
        self.eventos.extend(self._pendentes)
        self._pendentes = []

    @property
    def nomes(self) -> list[str]:
        return [e.nome for e in self.eventos]


class OutboxFalsa:
    def __init__(self) -> None:
        self.mensagens: list[MensagemSaida] = []

    def registrar(self, mensagem: MensagemSaida) -> None:
        self.mensagens.append(mensagem)


class VeiculoRepoMemoria:
    def __init__(self) -> None:
        self.dados: dict[UUID, Veiculo] = {}
        self.compensacoes: dict[UUID, UUID] = {}
        self.travados: list[UUID] = []

    def adicionar(self, veiculo: Veiculo) -> None:
        self.salvar(veiculo)

    def obter(self, veiculo_id: UUID, *, travar: bool = False) -> Veiculo | None:
        if travar:
            self.travados.append(veiculo_id)
        veiculo = self.dados.get(veiculo_id)
        return None if veiculo is None else copy.deepcopy(veiculo)

    def salvar(self, veiculo: Veiculo) -> None:
        guardado = copy.deepcopy(veiculo)
        guardado.coletar_eventos()
        self.dados[veiculo.id] = guardado

    def listar(
        self, status: StatusVeiculo | None, *, limite: int, deslocamento: int
    ) -> tuple[Sequence[Veiculo], int]:
        itens = [v for v in self.dados.values() if status is None or v.status is status]
        itens.sort(key=lambda v: v.criado_em, reverse=True)
        return itens[deslocamento : deslocamento + limite], len(itens)

    def compensacao_registrada(self, compra_id: UUID) -> bool:
        return compra_id in self.compensacoes

    def registrar_compensacao(self, compra_id: UUID, veiculo_id: UUID, agora: datetime) -> None:
        self.compensacoes.setdefault(compra_id, veiculo_id)


class CobrancaRepoMemoria:
    def __init__(self) -> None:
        self.dados: dict[UUID, Cobranca] = {}
        # Simula a corrida: na próxima inserção, outra transação já gravou esta cobrança.
        self.concorrente: Cobranca | None = None

    def adicionar_se_ausente(self, cobranca: Cobranca) -> bool:
        if self.concorrente is not None:
            self.dados[self.concorrente.compra_id] = self.concorrente
            self.concorrente = None
        if cobranca.compra_id in self.dados:
            return False
        self.dados[cobranca.compra_id] = self._copia(cobranca)
        return True

    def por_compra(self, compra_id: UUID, *, travar: bool = False) -> Cobranca | None:
        cobranca = self.dados.get(compra_id)
        return None if cobranca is None else copy.deepcopy(cobranca)

    def por_codigo(self, codigo: str, *, travar: bool = False) -> Cobranca | None:
        for cobranca in self.dados.values():
            if str(cobranca.codigo) == codigo:
                return copy.deepcopy(cobranca)
        return None

    def salvar(self, cobranca: Cobranca) -> None:
        self.dados[cobranca.compra_id] = self._copia(cobranca)

    @staticmethod
    def _copia(cobranca: Cobranca) -> Cobranca:
        guardada = copy.deepcopy(cobranca)
        guardada.coletar_eventos()
        return guardada


class EntregadorFalso:
    """Destino HTTP das mensagens da outbox, com respostas programáveis por chamada."""

    def __init__(self, *respostas: int | Exception) -> None:
        self._respostas = list(respostas)
        self.chamadas: list[tuple[str, str, dict[str, object], dict[str, str]]] = []

    def responder(self, *respostas: int | Exception) -> None:
        self._respostas.extend(respostas)

    def enviar(
        self,
        metodo: str,
        caminho: str,
        corpo: Mapping[str, object],
        cabecalhos: Mapping[str, str] | None = None,
    ) -> RespostaServico:
        self.chamadas.append((metodo, caminho, dict(corpo), dict(cabecalhos or {})))
        resposta = self._respostas.pop(0) if self._respostas else 200
        if isinstance(resposta, Exception):
            raise resposta
        return RespostaServico(resposta, "" if resposta < 400 else "erro simulado")

    @property
    def caminhos(self) -> list[str]:
        return [caminho for _, caminho, _, _ in self.chamadas]


FALHA_DE_REDE = FalhaDeRedeError("ConnectError")


class RelogioMonotonico:
    """Substituto de `time.monotonic` que só anda quando o teste manda."""

    def __init__(self, instante: float = 0.0) -> None:
        self.instante = instante

    def __call__(self) -> float:
        return self.instante
