"""Corridas reais no PostgreSQL: o banco decide um único vencedor (ADR-009, RN-03)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

from sqlalchemy import Engine, text

from apoio.cenario import AS_10H, PRECO
from principal.catalogo.application.casos_uso import DadosCadastro
from principal.catalogo.domain.veiculo import StatusVeiculo, Veiculo
from principal.composicao import Composicao
from principal.shared.clock import RelogioFixo
from principal.shared.db import BancoDeDados
from principal.shared.eventos import PublicadorEventosLog


def _em_paralelo(tarefas: list[Callable[[], object]]) -> list[object]:
    largada = threading.Barrier(len(tarefas))
    resultados: list[object] = [None] * len(tarefas)

    def rodar(indice: int, tarefa: Callable[[], object]) -> None:
        largada.wait()
        try:
            resultados[indice] = tarefa()
        except Exception as exc:  # o perdedor sai por exceção de domínio
            resultados[indice] = exc

    threads = [threading.Thread(target=rodar, args=(i, t)) for i, t in enumerate(tarefas)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return resultados


def _composicao(banco: BancoDeDados) -> Composicao:
    return Composicao(banco, RelogioFixo(AS_10H), PublicadorEventosLog())


def _veiculo(banco: BancoDeDados, composicao: Composicao) -> Veiculo:
    with banco.nova_sessao() as sessao:
        return composicao.casos_uso_catalogo(sessao).cadastrar.executar(
            DadosCadastro(marca="Fiat", modelo="Argo", ano=2022, cor="Prata", preco=PRECO)
        )


def test_pagamento_e_expiracao_simultaneos_tem_um_unico_vencedor(
    banco: BancoDeDados, engine: Engine
) -> None:
    composicao = _composicao(banco)
    for _ in range(10):
        compra = uuid.uuid4()
        with banco.nova_sessao() as sessao:
            cobranca = composicao.casos_uso_pagamentos(sessao).gerar.executar(compra, PRECO)
        codigo = str(cobranca.cobranca.codigo)

        def pagar(codigo: str = codigo) -> object:
            with banco.nova_sessao() as sessao:
                uc = composicao.casos_uso_pagamentos(sessao)
                return uc.registrar_resultado.executar(codigo, pago=True)

        def expirar(compra: uuid.UUID = compra) -> object:
            with banco.nova_sessao() as sessao:
                return composicao.casos_uso_pagamentos(sessao).cancelar.executar(compra)

        pagamento, cancelamento = _em_paralelo([pagar, expirar])
        perdedores = [r for r in (pagamento, cancelamento) if isinstance(r, Exception)]
        assert len(perdedores) == 1, (pagamento, cancelamento)
        nome = type(perdedores[0]).__name__
        assert nome in {"CobrancaJaPagaError", "PagamentoAposCancelamentoError"}
    with engine.connect() as conexao:
        estados = conexao.execute(
            text("SELECT status, count(*) FROM pagamentos.cobrancas GROUP BY status")
        ).all()
    assert sum(n for _, n in estados) == 10


def test_t3_repetido_em_paralelo_gera_uma_unica_cobranca(
    banco: BancoDeDados, engine: Engine
) -> None:
    composicao = _composicao(banco)
    compra = uuid.uuid4()

    def gerar() -> object:
        with banco.nova_sessao() as sessao:
            return composicao.casos_uso_pagamentos(sessao).gerar.executar(compra, PRECO)

    resultados = _em_paralelo([gerar] * 6)
    codigos = {str(r.cobranca.codigo) for r in resultados}  # type: ignore[attr-defined]
    assert len(codigos) == 1
    assert sum(r.efeito for r in resultados) == 1  # type: ignore[attr-defined]


def test_bloqueios_simultaneos_de_compras_diferentes(banco: BancoDeDados) -> None:
    composicao = _composicao(banco)
    veiculo = _veiculo(banco, composicao)

    def bloquear() -> object:
        with banco.nova_sessao() as sessao:
            return composicao.casos_uso_catalogo(sessao).bloquear.executar(
                veiculo.id, uuid.uuid4(), 1
            )

    resultados = _em_paralelo([bloquear] * 8)
    vencedores = [r for r in resultados if not isinstance(r, Exception)]
    assert len(vencedores) == 1
    assert {type(r).__name__ for r in resultados if isinstance(r, Exception)} == {
        "VeiculoIndisponivelError"
    }


def test_desbloqueio_e_bloqueio_atrasado_da_mesma_compra(banco: BancoDeDados) -> None:
    """C2 e o T2 atrasado chegam juntos: nunca termina bloqueado por uma compra compensada."""
    composicao = _composicao(banco)
    for _ in range(10):
        veiculo = _veiculo(banco, composicao)
        compra = uuid.uuid4()

        def bloquear(v: uuid.UUID = veiculo.id, c: uuid.UUID = compra) -> object:
            with banco.nova_sessao() as sessao:
                return composicao.casos_uso_catalogo(sessao).bloquear.executar(v, c, 1)

        def desbloquear(v: uuid.UUID = veiculo.id, c: uuid.UUID = compra) -> object:
            with banco.nova_sessao() as sessao:
                return composicao.casos_uso_catalogo(sessao).desbloquear.executar(v, c)

        _em_paralelo([bloquear, desbloquear])
        with banco.nova_sessao() as sessao:
            final = composicao.casos_uso_catalogo(sessao).obter.executar(veiculo.id)
        assert final.status is StatusVeiculo.A_VENDA
