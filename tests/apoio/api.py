"""Atalhos HTTP para os testes de API (TestClient), na linguagem da saga."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from apoio.cenario import VEICULO

SEGREDO_WEBHOOK = "segredo-do-webhook-de-teste-0123456789"
TOKEN_ENTRADA = "token-de-entrada-0123456789"
PROBLEM_JSON = "application/problem+json"
GESTOR = {"X-Consumer-Username": "gestor"}
SERVICO = {"X-Service-Token": TOKEN_ENTRADA}


class Api:
    def __init__(self, cliente: TestClient) -> None:
        self.http = cliente

    # ------------------------------------------------------------ gestor
    def cadastrar(self, **campos: Any) -> dict[str, Any]:
        resposta = self.http.post("/api/v1/veiculos", json={**VEICULO, **campos}, headers=GESTOR)
        assert resposta.status_code == 201, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def editar(self, veiculo_id: str, **campos: Any) -> Response:
        return self.http.patch(f"/api/v1/veiculos/{veiculo_id}", json=campos, headers=GESTOR)

    def veiculo(self, veiculo_id: str) -> dict[str, Any]:
        resposta = self.http.get(f"/api/v1/veiculos/{veiculo_id}", headers=GESTOR)
        assert resposta.status_code == 200, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    # ------------------------------------------------------------ saga (rotas internas)
    def bloquear(self, veiculo_id: str, compra_id: str, versao: int = 1) -> Response:
        return self.http.post(
            f"/internal/v1/veiculos/{veiculo_id}/bloqueio",
            json={"compra_id": compra_id, "versao": versao},
            headers=SERVICO,
        )

    def desbloquear(self, veiculo_id: str, compra_id: str) -> Response:
        return self.http.delete(
            f"/internal/v1/veiculos/{veiculo_id}/bloqueio/{compra_id}", headers=SERVICO
        )

    def vender(self, veiculo_id: str, compra_id: str) -> Response:
        return self.http.post(
            f"/internal/v1/veiculos/{veiculo_id}/venda",
            json={"compra_id": compra_id},
            headers=SERVICO,
        )

    def gerar_cobranca(self, compra_id: str, valor: str = "119900.00") -> Response:
        return self.http.post(
            "/internal/v1/cobrancas", json={"compra_id": compra_id, "valor": valor}, headers=SERVICO
        )

    def cancelar_cobranca(self, compra_id: str) -> Response:
        return self.http.post(f"/internal/v1/cobrancas/{compra_id}/cancelamento", headers=SERVICO)

    def compra_aguardando_pagamento(self) -> tuple[str, str, str]:
        """Executa T2 e T3 como a saga faria; devolve (veiculo_id, compra_id, código)."""
        veiculo = self.cadastrar()
        compra = str(uuid.uuid4())
        assert self.bloquear(veiculo["id"], compra).status_code == 200
        resposta = self.gerar_cobranca(compra, veiculo["preco"])
        assert resposta.status_code == 201, resposta.text
        return veiculo["id"], compra, resposta.json()["codigo_pagamento"]

    # ------------------------------------------------------------ processador
    def webhook(
        self, codigo: str, status: str, *, segredo: str | None = SEGREDO_WEBHOOK
    ) -> Response:
        cabecalhos = {} if segredo is None else {"X-Webhook-Secret": segredo}
        return self.http.post(
            "/api/v1/pagamentos/webhook",
            json={"codigo_pagamento": codigo, "status": status},
            headers=cabecalhos,
        )


def tipo_problema(resposta: Response) -> str:
    assert resposta.headers["content-type"].startswith(PROBLEM_JSON), resposta.text
    tipo: str = resposta.json()["type"]
    return tipo.removeprefix("urn:revenda:problema:")
