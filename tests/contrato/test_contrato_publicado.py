"""Provedor: o contrato publicado em contratos/ e exatamente o da aplicacao atual."""

from __future__ import annotations

import uuid

import pytest

from contrato.gerar import ARQUIVO, contrato_atual
from contrato.openapi_contrato import (
    DIRETORIO,
    Contrato,
    ViolacaoDeContratoError,
    carregar,
    serializar,
)

pytestmark = pytest.mark.contrato


def test_contrato_publicado_e_o_da_aplicacao() -> None:
    publicado = (DIRETORIO / ARQUIVO).read_text(encoding="utf-8")
    assert serializar(contrato_atual()) == publicado, (
        "As rotas internas mudaram: rode `uv run python -m contrato.gerar` (PYTHONPATH=tests), "
        "versione o contrato e atualize a copia no repositorio de venda."
    )


def test_consumidor_detecta_quebra_de_contrato() -> None:
    """O validador falha quando o corpo foge do contrato (garantia do proprio teste)."""
    with pytest.raises(ViolacaoDeContratoError, match="fora do contrato"):
        Contrato(carregar("vendas-interno.openapi.json")).validar_requisicao(
            "POST",
            f"/internal/v1/compras/{uuid.uuid4()}/resultado-pagamento",
            {"codigo_pagamento": "PAG-000000000000", "status": "APROVADO"},
        )
    with pytest.raises(ViolacaoDeContratoError, match="rota fora do contrato"):
        Contrato(carregar("vendas-interno.openapi.json")).validar_requisicao(
            "DELETE", "/internal/v1/anuncios", None
        )
