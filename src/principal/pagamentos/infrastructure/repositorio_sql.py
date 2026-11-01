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
from principal.pagamentos.infrastructure.tabelas import cobrancas


class SqlCobrancaRepository:
    def __init__(self, sessao: Session) -> None:
        self._sessao = sessao

    def adicionar_se_ausente(self, cobranca: Cobranca) -> bool:
        resultado = self._sessao.execute(
            insert_pg(cobrancas)
            .values(**_para_linha(cobranca))
            .on_conflict_do_nothing(index_elements=[cobrancas.c.compra_id])
            .returning(cobrancas.c.id)
        )
        return resultado.first() is not None

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
