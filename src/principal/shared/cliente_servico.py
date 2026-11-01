"""Cliente HTTP para as rotas internas do outro serviço (ADR-004).

Timeout explícito em toda chamada e o `X-Service-Token` da direção principal → venda. Este
cliente faz uma única tentativa: quem o usa (o relay da outbox) tem o próprio retry, de
longo prazo, com backoff gravado no banco.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from principal.shared.auth import CABECALHO_TOKEN_SERVICO


@dataclass(frozen=True, slots=True)
class RespostaServico:
    status: int
    corpo: str


class FalhaDeRedeError(Exception):
    """Falha de transporte (conexão recusada, timeout, DNS): sempre transitória."""


class ClienteServico:
    def __init__(
        self,
        url_base: str,
        token: str,
        *,
        timeout_segundos: float = 2.0,
        transporte: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=url_base,
            timeout=httpx.Timeout(timeout_segundos),
            headers={CABECALHO_TOKEN_SERVICO: token},
            # Pool limitado por destino (bulkhead): um destino lento não esgota o processo.
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            transport=transporte,
        )

    def enviar(
        self,
        metodo: str,
        caminho: str,
        corpo: Mapping[str, object],
        cabecalhos: Mapping[str, str] | None = None,
    ) -> RespostaServico:
        try:
            resposta = self._http.request(
                metodo, caminho, json=dict(corpo), headers=dict(cabecalhos or {})
            )
        except httpx.HTTPError as exc:
            raise FalhaDeRedeError(type(exc).__name__) from exc
        return RespostaServico(resposta.status_code, resposta.text[:300])

    def fechar(self) -> None:
        self._http.close()
