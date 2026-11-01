"""Apoio dos testes ponta a ponta: tipos e funcoes usados pelos modulos de teste."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

PROBLEMA = "urn:revenda:problema:"
# CPF valido de teste (digitos verificadores corretos), o exemplo classico de documentacao
CPF_DE_TESTE = "52998224725"


@dataclass(frozen=True)
class Ambiente:
    gateway: str
    jaeger: str
    chave_gestor: str
    chave_pagamento: str
    segredo_webhook: str
    vendas: bool


def tipo(resposta: httpx.Response) -> str:
    """Nome do problema RFC 9457 (sem o prefixo urn:revenda:problema:)."""
    corpo = resposta.json()
    return str(corpo.get("type", "")).removeprefix(PROBLEMA)


def esperar(condicao: Callable[[], Any], descricao: str, limite: float = 30.0) -> Any:
    """Repete `condicao` ate devolver algo verdadeiro (replicacao e relay sao assincronos)."""
    fim = time.monotonic() + limite
    while True:
        valor = condicao()
        if valor:
            return valor
        if time.monotonic() > fim:
            pytest.fail(f"Tempo esgotado esperando: {descricao}")
        time.sleep(0.5)
