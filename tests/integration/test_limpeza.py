"""Limpeza periódica (ADR-003 e docs/06, retenção) contra PostgreSQL real."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import Engine, insert, select

from apoio.cenario import AS_10H
from principal.catalogo.infrastructure.tabelas import compensacoes
from principal.composicao import nova_limpeza
from principal.limpeza import ResultadoLimpeza
from principal.mensageria.tabelas import outbox
from principal.shared.clock import RelogioFixo
from principal.shared.db import BancoDeDados

DIA = timedelta(days=1)


def _mensagem(chave: str, status: str, entregue_em: object) -> dict[str, object]:
    return {
        "tipo": "ANUNCIO",
        "chave": chave,
        "metodo": "PUT",
        "caminho": "/internal/v1/anuncios/x",
        "corpo": {},
        "status": status,
        "proxima_tentativa": AS_10H - 30 * DIA,
        "criado_em": AS_10H - 30 * DIA,
        "entregue_em": entregue_em,
    }


def test_apaga_so_entregues_antigas_e_marcas_antigas(banco: BancoDeDados, engine: Engine) -> None:
    antiga, recente = uuid4(), uuid4()
    with engine.begin() as conexao:
        conexao.execute(
            insert(outbox),
            [
                _mensagem("entregue-antiga", "ENTREGUE", AS_10H - 8 * DIA),
                _mensagem("entregue-recente", "ENTREGUE", AS_10H - 6 * DIA),
                _mensagem("pendente-antiga", "PENDENTE", None),
                _mensagem("falhou-antiga", "FALHOU", None),
            ],
        )
        conexao.execute(
            insert(compensacoes),
            [
                {"compra_id": antiga, "veiculo_id": uuid4(), "registrada_em": AS_10H - 31 * DIA},
                {"compra_id": recente, "veiculo_id": uuid4(), "registrada_em": AS_10H - DIA},
            ],
        )
    limpeza = nova_limpeza(banco, RelogioFixo(AS_10H))
    resultado = limpeza.executar()
    assert (resultado.mensagens_entregues, resultado.compensacoes) == (1, 1)
    with engine.connect() as conexao:
        chaves = set(conexao.execute(select(outbox.c.chave)).scalars())
        marcas = set(conexao.execute(select(compensacoes.c.compra_id)).scalars())
    assert chaves == {"entregue-recente", "pendente-antiga", "falhou-antiga"}
    assert marcas == {recente}
    assert limpeza.executar() == ResultadoLimpeza(0, 0)  # repetir não tem efeito
