"""Repositório SQLAlchemy do agregado Cobranca."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as insert_pg
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from principal.pagamentos.domain.cobranca import (
    Cobranca,
    CodigoPagamento,
    OrigemCancelamento,
    StatusCobranca,
)
from principal.pagamentos.domain.erros import CodigoPagamentoEmUsoError
from principal.pagamentos.infrastructure.tabelas import cobrancas

# O código tem 48 bits aleatórios: uma colisão já é improvável; duas seguidas indicam defeito.
_TENTATIVAS_CODIGO = 2


class SqlCobrancaRepository:
    def __init__(self, sessao: Session) -> None:
        self._sessao = sessao

    def adicionar_se_ausente(self, cobranca: Cobranca) -> bool:
        """Grava se a compra ainda não tem cobrança. False: já tinha (idempotência do T3).

        O conflito pode vir de `uq_cobrancas_compra_id` (a compra já tem cobrança) ou de
        `uq_cobrancas_codigo` (o código sorteado já existe). No segundo caso o código é
        sorteado de novo e a gravação repetida, em vez de virar um erro 500.
        """
        for _ in range(_TENTATIVAS_CODIGO):
            gravada = self._sessao.execute(
                insert_pg(cobrancas)
                .values(**_para_linha(cobranca))
                .on_conflict_do_nothing()
                .returning(cobrancas.c.id)
            ).first()
            if gravada is not None:
                return True
            if self.por_compra(cobranca.compra_id) is not None:
                return False
            cobranca.sortear_novo_codigo()
        raise CodigoPagamentoEmUsoError(cobranca.compra_id)

    def por_compra(self, compra_id: UUID, *, travar: bool = False) -> Cobranca | None:
        return self._um(cobrancas.c.compra_id == compra_id, travar)

    def por_codigo(self, codigo: str, *, travar: bool = False) -> Cobranca | None:
        return self._um(cobrancas.c.codigo == codigo, travar)

    def salvar(self, cobranca: Cobranca) -> None:
        valores = _para_linha(cobranca)
        for chave in ("id", "codigo", "compra_id", "criada_em"):
            valores.pop(chave)
        self._sessao.execute(
            update(cobrancas).where(cobrancas.c.id == cobranca.id).values(**valores)
        )

    def _um(self, condicao: Any, travar: bool) -> Cobranca | None:
        consulta = select(cobrancas).where(condicao)
        if travar:
            consulta = consulta.with_for_update()
        registro = self._sessao.execute(consulta).mappings().one_or_none()
        return None if registro is None else _para_dominio(registro)


def _para_linha(cobranca: Cobranca) -> dict[str, Any]:
    return {
        "id": cobranca.id,
        "codigo": str(cobranca.codigo),
        "compra_id": cobranca.compra_id,
        "valor": cobranca.valor,
        "status": cobranca.status.value,
        "origem_cancelamento": (
            cobranca.origem_cancelamento.value if cobranca.origem_cancelamento else None
        ),
        "pagamento_apos_cancelamento": cobranca.pagamento_apos_cancelamento,
        "criada_em": cobranca.criada_em,
        "atualizada_em": cobranca.atualizada_em,
    }


def _para_dominio(registro: RowMapping) -> Cobranca:
    origem = registro["origem_cancelamento"]
    return Cobranca(
        id=registro["id"],
        codigo=CodigoPagamento(registro["codigo"]),
        compra_id=registro["compra_id"],
        valor=registro["valor"],
        status=StatusCobranca(registro["status"]),
        origem_cancelamento=OrigemCancelamento(origem) if origem else None,
        pagamento_apos_cancelamento=registro["pagamento_apos_cancelamento"],
        criada_em=registro["criada_em"],
        atualizada_em=registro["atualizada_em"],
    )
