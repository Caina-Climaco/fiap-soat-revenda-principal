"""Repositório SQLAlchemy do agregado Veiculo."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as insert_pg
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from principal.catalogo.domain.veiculo import StatusVeiculo, Veiculo
from principal.catalogo.infrastructure.tabelas import compensacoes, veiculos


class SqlVeiculoRepository:
    def __init__(self, sessao: Session) -> None:
        self._sessao = sessao

    def adicionar(self, veiculo: Veiculo) -> None:
        self._sessao.execute(insert(veiculos).values(**_para_linha(veiculo)))

    def obter(self, veiculo_id: UUID, *, travar: bool = False) -> Veiculo | None:
        consulta = select(veiculos).where(veiculos.c.id == veiculo_id)
        if travar:
            consulta = consulta.with_for_update()
        registro = self._sessao.execute(consulta).mappings().one_or_none()
        return None if registro is None else _para_dominio(registro)

    def salvar(self, veiculo: Veiculo) -> None:
        valores = _para_linha(veiculo)
        for chave in ("id", "criado_em"):
            valores.pop(chave)
        self._sessao.execute(update(veiculos).where(veiculos.c.id == veiculo.id).values(**valores))

    def listar(
        self, status: StatusVeiculo | None, *, limite: int, deslocamento: int
    ) -> tuple[Sequence[Veiculo], int]:
        filtros = [] if status is None else [veiculos.c.status == status.value]
        total = self._sessao.execute(
            select(func.count()).select_from(veiculos).where(*filtros)
        ).scalar_one()
        linhas = self._sessao.execute(
            select(veiculos)
            .where(*filtros)
            .order_by(veiculos.c.criado_em.desc(), veiculos.c.id.asc())
            .limit(limite)
            .offset(deslocamento)
        )
        return [_para_dominio(r) for r in linhas.mappings()], int(total)

    def compensacao_registrada(self, compra_id: UUID) -> bool:
        consulta = select(compensacoes.c.compra_id).where(compensacoes.c.compra_id == compra_id)
        return self._sessao.execute(consulta).first() is not None

    def apagar_compensacoes_anteriores(self, limite: datetime) -> int:
        """Retenção das marcas da regra contra ressurreição (limpeza periódica)."""
        resultado = cast(
            CursorResult[Any],
            self._sessao.execute(delete(compensacoes).where(compensacoes.c.registrada_em < limite)),
        )
        return int(resultado.rowcount)

    def registrar_compensacao(self, compra_id: UUID, veiculo_id: UUID, agora: datetime) -> None:
        self._sessao.execute(
            insert_pg(compensacoes)
            .values(compra_id=compra_id, veiculo_id=veiculo_id, registrada_em=agora)
            .on_conflict_do_nothing(index_elements=[compensacoes.c.compra_id])
        )


def _para_linha(veiculo: Veiculo) -> dict[str, Any]:
    return {
        "id": veiculo.id,
        "marca": veiculo.marca,
        "modelo": veiculo.modelo,
        "ano": veiculo.ano,
        "cor": veiculo.cor,
        "preco": veiculo.preco,
        "status": veiculo.status.value,
        "versao": veiculo.versao,
        "compra_id": veiculo.compra_id,
        "criado_em": veiculo.criado_em,
        "atualizado_em": veiculo.atualizado_em,
    }


def _para_dominio(registro: RowMapping) -> Veiculo:
    return Veiculo(
        id=registro["id"],
        marca=registro["marca"],
        modelo=registro["modelo"],
        ano=registro["ano"],
        cor=registro["cor"],
        preco=registro["preco"],
        status=StatusVeiculo(registro["status"]),
        versao=registro["versao"],
        compra_id=registro["compra_id"],
        criado_em=registro["criado_em"],
        atualizado_em=registro["atualizado_em"],
    )
