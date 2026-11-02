"""Agregado Veiculo (contexto Catálogo, serviço principal): docs/02-modelagem-ddd.md, 2.6.1.

Na Fase 4 o veículo deixa de ser reservado aqui (a reserva é do serviço de venda): o Catálogo
guarda o **bloqueio** pedido pela saga da compra, que impede a edição (RN-02) enquanto a
compra não termina, e o estado final de vendido. A `versao` muda só quando os dados
descritivos mudam (edição): é a versão que viaja na réplica do anúncio e que o bloqueio
confere (RN-12). Bloquear, desbloquear e vender são idempotentes pela `compra_id`.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from principal.catalogo.domain.erros import (
    BloqueioDeOutraCompraError,
    DadosVeiculoInvalidosError,
    VeiculoIndisponivelError,
    VeiculoJaVendidoError,
    VeiculoNaoEditavelError,
    VersaoDivergenteError,
)
from principal.catalogo.domain.eventos import (
    EventoCatalogo,
    VeiculoBloqueado,
    VeiculoCadastrado,
    VeiculoDesbloqueado,
    VeiculoEditado,
    VeiculoVendido,
)

ANO_MINIMO = 1950
TAMANHO_MAXIMO_MARCA = 60
TAMANHO_MAXIMO_MODELO = 60
TAMANHO_MAXIMO_COR = 30
PRECO_MAXIMO = Decimal("9999999999.99")  # limite de NUMERIC(12,2)
_CENTAVOS = Decimal("0.01")
# Caracteres de controle C0 (\x00 a \x1f) e DEL: o PostgreSQL recusa NUL em colunas de
# texto e os demais não têm uso legítimo em marca, modelo ou cor.
CARACTERES_DE_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
MENSAGEM_CARACTERE_CONTROLE = "não pode conter caracteres de controle"


class StatusVeiculo(StrEnum):
    A_VENDA = "A_VENDA"
    BLOQUEADO = "BLOQUEADO"
    VENDIDO = "VENDIDO"


@dataclass(frozen=True, slots=True)
class DadosVeiculo:
    """Campos editáveis já validados e normalizados (None = não informado)."""

    marca: str | None = None
    modelo: str | None = None
    ano: int | None = None
    cor: str | None = None
    preco: Decimal | None = None

    def informados(self) -> dict[str, object]:
        return {
            f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None
        }


@dataclass(eq=False)
class Veiculo:
    id: UUID
    marca: str
    modelo: str
    ano: int
    cor: str
    preco: Decimal
    status: StatusVeiculo
    versao: int
    criado_em: datetime
    atualizado_em: datetime
    compra_id: UUID | None = None
    _eventos: list[EventoCatalogo] = field(default_factory=list, repr=False)

    @classmethod
    def cadastrar(
        cls,
        *,
        marca: str,
        modelo: str,
        ano: int,
        cor: str,
        preco: Decimal | str | int,
        agora: datetime,
        id_: UUID | None = None,
    ) -> Veiculo:
        dados = validar_dados(
            marca=marca, modelo=modelo, ano=ano, cor=cor, preco=preco, ano_corrente=agora.year
        )
        if (
            dados.marca is None
            or dados.modelo is None
            or dados.ano is None
            or dados.cor is None
            or dados.preco is None
        ):
            ausentes = sorted({"marca", "modelo", "ano", "cor", "preco"} - set(dados.informados()))
            raise DadosVeiculoInvalidosError([(c, "campo obrigatório") for c in ausentes])
        veiculo = cls(
            id=id_ or uuid.uuid4(),
            marca=dados.marca,
            modelo=dados.modelo,
            ano=dados.ano,
            cor=dados.cor,
            preco=dados.preco,
            status=StatusVeiculo.A_VENDA,
            versao=1,
            criado_em=agora,
            atualizado_em=agora,
        )
        veiculo._eventos.append(
            VeiculoCadastrado(veiculo_id=veiculo.id, preco=veiculo.preco, ocorrido_em=agora)
        )
        return veiculo

    def editar(
        self,
        *,
        agora: datetime,
        marca: str | None = None,
        modelo: str | None = None,
        ano: int | None = None,
        cor: str | None = None,
        preco: Decimal | str | int | None = None,
    ) -> bool:
        """Edição parcial (merge). Só com o veículo à venda (RN-02).

        Os dados são validados antes do estado: um pedido inválido recebe 422 (corrigível
        pelo cliente) mesmo com o veículo reservado, em vez de um 409 que esconde o erro.
        Devolve False, sem incrementar a versão nem gerar evento, quando todos os valores
        informados já são os atuais (edição sem mudança real).
        """
        dados = validar_dados(
            marca=marca, modelo=modelo, ano=ano, cor=cor, preco=preco, ano_corrente=agora.year
        )
        informados = dados.informados()
        if not informados:
            raise DadosVeiculoInvalidosError([("corpo", "informe ao menos um campo para editar")])
        if self.status is not StatusVeiculo.A_VENDA:
            raise VeiculoNaoEditavelError(self.id, self.status)
        alterados = {n: v for n, v in informados.items() if getattr(self, n) != v}
        if not alterados:
            return False
        for nome, valor in alterados.items():
            setattr(self, nome, valor)
        self.versao += 1
        self.atualizado_em = agora
        self._eventos.append(
            VeiculoEditado(
                veiculo_id=self.id,
                versao=self.versao,
                campos=",".join(sorted(alterados)),
                ocorrido_em=agora,
            )
        )
        return True

    # ------------------------------------------------------------ passos da saga

    def bloquear(self, compra_id: UUID, versao_vista: int, agora: datetime) -> bool:
        """T2: bloqueia a edição para a compra. Devolve False se já estava bloqueado por ela.

        A versão vista pelo comprador precisa ser a atual (RN-12); se o gestor editou o
        veículo depois da réplica que o comprador viu, a compra é recusada.
        """
        if self.compra_id == compra_id and self.status in (
            StatusVeiculo.BLOQUEADO,
            StatusVeiculo.VENDIDO,
        ):
            return False  # repetição do mesmo passo: idempotente
        if self.status is not StatusVeiculo.A_VENDA:
            raise VeiculoIndisponivelError(self.id, self.status)
        if versao_vista != self.versao:
            raise VersaoDivergenteError(self.id, versao_vista, self.versao)
        self._mudar(StatusVeiculo.BLOQUEADO, compra_id, agora)
        self._eventos.append(
            VeiculoBloqueado(veiculo_id=self.id, compra_id=compra_id, ocorrido_em=agora)
        )
        return True

    def desbloquear(self, compra_id: UUID, agora: datetime) -> bool:
        """C2: desfaz o bloqueio da compra. Sem bloqueio dessa compra, não tem efeito."""
        if self.compra_id != compra_id:
            return False
        if self.status is StatusVeiculo.VENDIDO:
            raise VeiculoJaVendidoError(self.id)
        self._mudar(StatusVeiculo.A_VENDA, None, agora)
        self._eventos.append(
            VeiculoDesbloqueado(veiculo_id=self.id, compra_id=compra_id, ocorrido_em=agora)
        )
        return True

    def marcar_vendido(self, compra_id: UUID, agora: datetime) -> bool:
        """T6: registra a venda da compra que detém o bloqueio. Repetição não tem efeito."""
        if self.compra_id != compra_id:
            raise BloqueioDeOutraCompraError(self.id, compra_id)
        if self.status is StatusVeiculo.VENDIDO:
            return False
        self._mudar(StatusVeiculo.VENDIDO, compra_id, agora)
        self._eventos.append(
            VeiculoVendido(veiculo_id=self.id, compra_id=compra_id, ocorrido_em=agora)
        )
        return True

    def _mudar(self, status: StatusVeiculo, compra_id: UUID | None, agora: datetime) -> None:
        # Mudança de disponibilidade não altera a versão (que é a dos dados descritivos).
        self.status = status
        self.compra_id = compra_id
        self.atualizado_em = agora

    def coletar_eventos(self) -> list[EventoCatalogo]:
        eventos, self._eventos = self._eventos, []
        return eventos


# ---------------------------------------------------------------- validação (RN-15)


def validar_dados(
    *,
    ano_corrente: int,
    marca: object = None,
    modelo: object = None,
    ano: object = None,
    cor: object = None,
    preco: object = None,
) -> DadosVeiculo:
    """Valida e normaliza os campos informados (None = ausente), acumulando todos os erros."""
    erros: list[tuple[str, str]] = []

    def texto(nome: str, valor: object, maximo: int) -> str | None:
        if valor is None:
            return None
        if not isinstance(valor, str):
            erros.append((nome, "deve ser texto"))
            return None
        limpo = valor.strip()
        # Verificado no valor bruto: strip() removeria \t, \n e \x1c-\x1f das pontas.
        if CARACTERES_DE_CONTROLE.search(valor):
            erros.append((nome, MENSAGEM_CARACTERE_CONTROLE))
        elif not limpo:
            erros.append((nome, "não pode ser vazio"))
        elif len(limpo) > maximo:
            erros.append((nome, f"deve ter no máximo {maximo} caracteres"))
        else:
            return limpo
        return None

    dados = DadosVeiculo(
        marca=texto("marca", marca, TAMANHO_MAXIMO_MARCA),
        modelo=texto("modelo", modelo, TAMANHO_MAXIMO_MODELO),
        ano=_validar_ano(ano, ano_corrente, erros),
        cor=texto("cor", cor, TAMANHO_MAXIMO_COR),
        preco=_validar_preco(preco, erros),
    )
    if erros:
        raise DadosVeiculoInvalidosError(erros)
    return dados


def _validar_ano(valor: object, ano_corrente: int, erros: list[tuple[str, str]]) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int):
        erros.append(("ano", "deve ser um número inteiro"))
        return None
    if not ANO_MINIMO <= valor <= ano_corrente + 1:
        erros.append(("ano", f"deve estar entre {ANO_MINIMO} e {ano_corrente + 1}"))
        return None
    return valor


def _validar_preco(valor: object, erros: list[tuple[str, str]]) -> Decimal | None:
    if valor is None:
        return None
    try:
        if isinstance(valor, bool) or not isinstance(valor, Decimal | str | int):
            raise InvalidOperation
        preco = Decimal(valor)
        if not preco.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        erros.append(("preco", "deve ser um valor decimal"))
        return None
    if preco <= 0:
        erros.append(("preco", "deve ser maior que zero"))
        return None
    if preco != preco.quantize(_CENTAVOS):
        erros.append(("preco", "deve ter no máximo 2 casas decimais"))
        return None
    preco = preco.quantize(_CENTAVOS)
    if preco > PRECO_MAXIMO:
        erros.append(("preco", f"deve ser no máximo {PRECO_MAXIMO}"))
        return None
    return preco
