"""Casos de uso do Catálogo: rotas do gestor e passos da saga da compra (T2, C2, T6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from principal.catalogo.application.mensagens import mensagem_anuncio
from principal.catalogo.domain.erros import CompraCompensadaError, VeiculoNaoEncontradoError
from principal.catalogo.domain.repositorio import VeiculoRepository
from principal.catalogo.domain.veiculo import StatusVeiculo, Veiculo
from principal.shared.clock import Clock
from principal.shared.outbox import Outbox
from principal.shared.paginacao import Pagina
from principal.shared.uow import UnidadeDeTrabalho


@dataclass(frozen=True, slots=True)
class DadosCadastro:
    marca: str
    modelo: str
    ano: int
    cor: str
    preco: Decimal


@dataclass(frozen=True, slots=True)
class DadosEdicao:
    marca: str | None = None
    modelo: str | None = None
    ano: int | None = None
    cor: str | None = None
    preco: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ResultadoPasso:
    """Veículo após um passo da saga e se o passo teve efeito (False = repetição)."""

    veiculo: Veiculo
    efeito: bool


class _CasoDeUso:
    def __init__(
        self, repo: VeiculoRepository, uow: UnidadeDeTrabalho, relogio: Clock, outbox: Outbox
    ) -> None:
        self._repo = repo
        self._uow = uow
        self._relogio = relogio
        self._outbox = outbox

    def _obter_travado(self, veiculo_id: UUID) -> Veiculo:
        veiculo = self._repo.obter(veiculo_id, travar=True)
        if veiculo is None:
            raise VeiculoNaoEncontradoError(veiculo_id)
        return veiculo

    def _confirmar(self, veiculo: Veiculo) -> None:
        self._uow.registrar_eventos(veiculo.coletar_eventos())
        self._uow.confirmar()


class CadastrarVeiculo(_CasoDeUso):
    def executar(self, dados: DadosCadastro) -> Veiculo:
        veiculo = Veiculo.cadastrar(
            marca=dados.marca,
            modelo=dados.modelo,
            ano=dados.ano,
            cor=dados.cor,
            preco=dados.preco,
            agora=self._relogio.agora(),
        )
        self._repo.adicionar(veiculo)
        # Mesma transação: ou o veículo e a mensagem existem, ou nenhum dos dois (ADR-003).
        self._outbox.registrar(mensagem_anuncio(veiculo))
        self._confirmar(veiculo)
        return veiculo


class EditarVeiculo(_CasoDeUso):
    def executar(self, veiculo_id: UUID, dados: DadosEdicao) -> Veiculo:
        veiculo = self._obter_travado(veiculo_id)
        alterou = veiculo.editar(
            agora=self._relogio.agora(),
            marca=dados.marca,
            modelo=dados.modelo,
            ano=dados.ano,
            cor=dados.cor,
            preco=dados.preco,
        )
        if not alterou:
            return veiculo  # nada mudou: sem nova versão, sem mensagem, sem escrita
        self._repo.salvar(veiculo)
        self._outbox.registrar(mensagem_anuncio(veiculo))
        self._confirmar(veiculo)
        return veiculo


class ObterVeiculo:
    def __init__(self, repo: VeiculoRepository) -> None:
        self._repo = repo

    def executar(self, veiculo_id: UUID) -> Veiculo:
        veiculo = self._repo.obter(veiculo_id)
        if veiculo is None:
            raise VeiculoNaoEncontradoError(veiculo_id)
        return veiculo


class ListarVeiculos:
    def __init__(self, repo: VeiculoRepository) -> None:
        self._repo = repo

    def executar(
        self, *, status: StatusVeiculo | None, limite: int, deslocamento: int
    ) -> Pagina[Veiculo]:
        itens, total = self._repo.listar(status, limite=limite, deslocamento=deslocamento)
        return Pagina(itens=itens, total=total, limite=limite, deslocamento=deslocamento)


class BloquearVeiculo(_CasoDeUso):
    """T2 da saga: bloqueia a edição do veículo para a compra, conferindo a versão."""

    def executar(self, veiculo_id: UUID, compra_id: UUID, versao: int) -> ResultadoPasso:
        # A trava vem antes da consulta: uma compensação concorrente (que também trava o
        # veículo) ou já terminou e é vista aqui, ou só roda depois deste bloqueio.
        veiculo = self._obter_travado(veiculo_id)
        if self._repo.compensacao_registrada(compra_id):
            raise CompraCompensadaError(compra_id)
        efeito = veiculo.bloquear(compra_id, versao, self._relogio.agora())
        if efeito:
            self._repo.salvar(veiculo)
            self._confirmar(veiculo)
        return ResultadoPasso(veiculo, efeito)


class DesbloquearVeiculo(_CasoDeUso):
    """C2 da saga: desfaz o bloqueio e marca a compra como compensada."""

    def executar(self, veiculo_id: UUID, compra_id: UUID) -> ResultadoPasso:
        veiculo = self._obter_travado(veiculo_id)
        agora = self._relogio.agora()
        efeito = veiculo.desbloquear(compra_id, agora)
        if efeito:
            self._repo.salvar(veiculo)
        # Mesmo sem bloqueio (T2 talvez ainda esteja a caminho), a marca impede que um T2
        # atrasado dessa compra bloqueie o veículo depois da compensação.
        self._repo.registrar_compensacao(compra_id, veiculo_id, agora)
        self._confirmar(veiculo)
        return ResultadoPasso(veiculo, efeito)


class VenderVeiculo(_CasoDeUso):
    """T6 da saga (retentável, depois do pivô): marca o veículo como vendido."""

    def executar(self, veiculo_id: UUID, compra_id: UUID) -> ResultadoPasso:
        veiculo = self._obter_travado(veiculo_id)
        efeito = veiculo.marcar_vendido(compra_id, self._relogio.agora())
        if efeito:
            self._repo.salvar(veiculo)
            self._confirmar(veiculo)
        return ResultadoPasso(veiculo, efeito)


@dataclass(frozen=True, slots=True)
class CasosUsoCatalogo:
    """Casos de uso já ligados a uma sessão; entregues aos routers por injeção."""

    cadastrar: CadastrarVeiculo
    editar: EditarVeiculo
    obter: ObterVeiculo
    listar: ListarVeiculos
    bloquear: BloquearVeiculo
    desbloquear: DesbloquearVeiculo
    vender: VenderVeiculo


FabricaCasosUsoCatalogo = Callable[..., CasosUsoCatalogo]
