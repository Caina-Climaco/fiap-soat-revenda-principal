"""Gera o contrato publicado por este servico: `uv run python -m contrato.gerar`
(com `tests` no PYTHONPATH, como o pytest faz). Rode ao mudar uma rota interna."""

from __future__ import annotations

from typing import Any

from contrato.openapi_contrato import DIRETORIO, recortar, serializar
from principal.main import criar_app
from principal.shared.config import Settings

ARQUIVO = "principal-interno.openapi.json"
TITULO = "Revenda de Veiculos: rotas internas do servico principal"


def contrato_atual() -> dict[str, Any]:
    settings = Settings(
        database_url="postgresql+psycopg://contrato:contrato@127.0.0.1:1/contrato",
        webhook_secret="segredo-so-para-gerar-o-contrato",
        token_servico_entrada="token-so-para-gerar-o-contrato",
        token_servico_saida="token-so-para-gerar-o-contrato",
        outbox_relay_ativo=False,
    )
    return recortar(criar_app(settings).openapi(), TITULO)


def main() -> None:
    (DIRETORIO / ARQUIVO).write_text(serializar(contrato_atual()), encoding="utf-8")
    print(f"contrato gravado em contratos/{ARQUIVO}")


if __name__ == "__main__":
    main()
