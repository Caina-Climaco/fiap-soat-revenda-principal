"""Esquemas HTTP do Catálogo (docs/05-api.md, seções 5.4 e 5.6)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from principal.catalogo.domain.veiculo import (
    ANO_MINIMO,
    CARACTERES_DE_CONTROLE,
    MENSAGEM_CARACTERE_CONTROLE,
    TAMANHO_MAXIMO_COR,
    TAMANHO_MAXIMO_MARCA,
    TAMANHO_MAXIMO_MODELO,
    StatusVeiculo,
)
from principal.shared.http import DataHoraUtc, Dinheiro, ModeloRequisicao, ModeloResposta


def _sem_caracteres_de_controle(valor: object) -> object:
    # Roda antes do strip do modelo, sobre o valor bruto (mesma regra do domínio).
    if isinstance(valor, str) and CARACTERES_DE_CONTROLE.search(valor):
        raise ValueError(MENSAGEM_CARACTERE_CONTROLE)
    return valor


# Depois do Field no Annotated: o BeforeValidator envolve o str já com as restrições de
# tamanho, que continuam gerando string_too_short/string_too_long.
SemControle = BeforeValidator(_sem_caracteres_de_controle)

Marca = Annotated[
    str,
    Field(min_length=1, max_length=TAMANHO_MAXIMO_MARCA, examples=["Fiat"]),
    SemControle,
]
Modelo = Annotated[
    str,
    Field(min_length=1, max_length=TAMANHO_MAXIMO_MODELO, examples=["Argo Drive 1.3"]),
    SemControle,
]
Ano = Annotated[
    int,
    Field(
        strict=True,  # inteiro JSON de verdade: "2020" e 2020.0 são recusados
        ge=ANO_MINIMO,
        description=(
            "Número inteiro JSON (não string), de 1950 até o ano corrente + 1 (o limite "
            "superior é validado no domínio)."
        ),
        examples=[2023],
    ),
]
Cor = Annotated[
    str,
    Field(min_length=1, max_length=TAMANHO_MAXIMO_COR, examples=["Vermelho"]),
    SemControle,
]
Preco = Annotated[
    Decimal,
    Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description=(
            "Valor em BRL com até 2 casas decimais. **Envie como string decimal** (ex.: "
            '"79900.00"), que preserva o valor exato; números JSON também são aceitos, mas '
            "passam por ponto flutuante no cliente e podem perder precisão. As respostas "
            "sempre trazem string."
        ),
        examples=["79900.00"],
    ),
]


class VeiculoCriacao(ModeloRequisicao):
    marca: Marca
    modelo: Modelo
    ano: Ano
    cor: Cor
    preco: Preco


class VeiculoEdicao(ModeloRequisicao):
    """Edição parcial (merge): só os campos enviados mudam; ao menos um é obrigatório."""

    marca: Marca | None = None
    modelo: Modelo | None = None
    ano: Ano | None = None
    cor: Cor | None = None
    preco: Preco | None = None

    @model_validator(mode="after")
    def _sem_nulos_explicitos(self) -> Self:
        nulos = sorted(c for c in self.model_fields_set if getattr(self, c) is None)
        if nulos:
            raise ValueError("campos não podem ser nulos: " + ", ".join(nulos))
        if not self.model_fields_set:
            raise ValueError("informe ao menos um campo para editar")
        return self


class VeiculoResposta(ModeloResposta):
    id: UUID
    marca: str
    modelo: str
    ano: int
    cor: str
    preco: Dinheiro
    status: StatusVeiculo
    versao: int = Field(description="Cresce a cada edição; viaja na réplica do anúncio.")
    compra_id: UUID | None = Field(
        default=None, description="Compra que mantém o bloqueio ou que vendeu o veículo."
    )
    criado_em: DataHoraUtc
    atualizado_em: DataHoraUtc


class PaginaVeiculos(ModeloResposta):
    itens: list[VeiculoResposta]
    total: int
    limite: int
    deslocamento: int


# ---------------------------------------------------------------- rotas internas (saga)


class BloqueioRequisicao(ModeloRequisicao):
    compra_id: UUID = Field(description="Compra que pede o bloqueio (chave de idempotência).")
    versao: int = Field(
        ge=1, strict=True, description="Versão do anúncio que o comprador viu (RN-12)."
    )


class VendaRequisicao(ModeloRequisicao):
    compra_id: UUID = Field(description="Compra que detém o bloqueio.")


class PassoSagaResposta(ModeloResposta):
    veiculo_id: UUID
    status: StatusVeiculo
    versao: int
    compra_id: UUID | None
    efeito: bool = Field(description="False quando o pedido repetiu um passo já aplicado.")
