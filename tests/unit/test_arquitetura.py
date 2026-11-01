"""Regras de dependência verificadas pelo código-fonte, sem importar módulos.

Complementa os contratos do import-linter (`uv run lint-imports`) com uma checagem que roda
junto da suíte de testes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "principal"
CONTEXTOS = ("catalogo", "pagamentos")
FRAMEWORKS = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "pydantic",
    "pydantic_settings",
    "psycopg",
    "httpx",
    "opentelemetry",
)


def _importados(arquivo: Path) -> set[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module and no.level == 0:
            nomes.add(no.module)
    return nomes


def _arquivos(*partes: str) -> list[Path]:
    arquivos = sorted((SRC.joinpath(*partes)).rglob("*.py"))
    assert arquivos, f"nenhum arquivo em {partes}"
    return arquivos


def _viola(importados: set[str], proibidos: tuple[str, ...]) -> list[str]:
    return sorted(i for i in importados for p in proibidos if i == p or i.startswith(p + "."))


def _outro(contexto: str) -> str:
    return CONTEXTOS[1] if contexto == CONTEXTOS[0] else CONTEXTOS[0]


@pytest.mark.parametrize("contexto", CONTEXTOS)
def test_dominio_nao_depende_de_frameworks_nem_de_outros_pacotes(contexto: str) -> None:
    for arquivo in _arquivos(contexto, "domain"):
        proibidos = (*FRAMEWORKS, "principal.shared", f"principal.{_outro(contexto)}")
        assert _viola(_importados(arquivo), proibidos) == [], arquivo


@pytest.mark.parametrize("contexto", CONTEXTOS)
def test_aplicacao_nao_depende_de_frameworks(contexto: str) -> None:
    for arquivo in _arquivos(contexto, "application"):
        proibidos = (
            *FRAMEWORKS,
            "principal.mensageria",
            f"principal.{contexto}.infrastructure",
            f"principal.{contexto}.interfaces",
        )
        assert _viola(_importados(arquivo), proibidos) == [], arquivo


@pytest.mark.parametrize("contexto", CONTEXTOS)
def test_contextos_nao_se_importam(contexto: str) -> None:
    for arquivo in _arquivos(contexto):
        assert _viola(_importados(arquivo), (f"principal.{_outro(contexto)}",)) == [], arquivo


@pytest.mark.parametrize("pacote", ["shared", "mensageria"])
def test_infraestrutura_comum_nao_conhece_os_contextos(pacote: str) -> None:
    proibidos = (*(f"principal.{c}" for c in CONTEXTOS), "principal.main", "principal.composicao")
    for arquivo in _arquivos(pacote):
        assert _viola(_importados(arquivo), proibidos) == [], arquivo
