"""Autorização (ADR-005): consumer do Kong nas rotas públicas e token de serviço nas internas.

- Rotas do gestor: o Kong autentica a chave (key-auth), aplica a ACL e injeta o header
  `X-Consumer-Username`. A NetworkPolicy só deixa o Kong (e o serviço de venda, nas rotas
  internas) alcançar este serviço, então o header não chega forjado de fora do cluster.
- Rotas internas: o serviço de venda apresenta `X-Service-Token`, comparado em tempo
  constante com o segredo configurado.
"""

# Sem `from __future__ import annotations` neste módulo: as dependências usam
# `Annotated[..., Depends(esquema)]` com o esquema local à fábrica, e o FastAPI só o resolve
# se as anotações forem avaliadas na definição da função.

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from principal.shared.errors import (
    AcessoNegadoError,
    NaoAutenticadoError,
    ServicoNaoAutorizadoError,
)

CABECALHO_CONSUMIDOR = "X-Consumer-Username"
CABECALHO_TOKEN_SERVICO = "X-Service-Token"  # noqa: S105 - nome do header, não segredo


def exigir_consumidor(nome: str) -> Callable[..., str]:
    """Dependência que exige o consumer `nome` do Kong (401 sem consumer, 403 se outro)."""
    esquema = APIKeyHeader(
        name=CABECALHO_CONSUMIDOR,
        scheme_name="consumidor-kong",
        description=(
            "Preenchido pelo Kong após validar a chave `apikey` do consumer. Ao chamar a API "
            "diretamente (desenvolvimento local), informe o nome do consumer."
        ),
        auto_error=False,
    )

    def dependencia(request: Request, consumidor: Annotated[str | None, Depends(esquema)]) -> str:
        if not consumidor:
            raise NaoAutenticadoError("Credencial ausente: use a chave do consumer no Kong.")
        # Registrado no log de acesso (auditoria de quem cadastrou ou editou)
        request.state.consumidor = consumidor[:60]
        if not hmac.compare_digest(consumidor.encode(), nome.encode()):
            raise AcessoNegadoError("Este consumer não tem acesso a esta operação.")
        return consumidor

    return dependencia


def exigir_token_servico(token: str) -> Callable[[str | None], None]:
    """Dependência das rotas internas: exige `X-Service-Token` igual ao segredo configurado."""
    esquema = APIKeyHeader(
        name=CABECALHO_TOKEN_SERVICO,
        scheme_name="token-servico",
        description="Segredo compartilhado entre os serviços (rotas internas).",
        auto_error=False,
    )
    esperado = token.encode()

    def dependencia(recebido: Annotated[str | None, Depends(esquema)]) -> None:
        if not recebido or not hmac.compare_digest(recebido.encode(), esperado):
            raise ServicoNaoAutorizadoError("Token de serviço ausente ou inválido.")

    return dependencia
