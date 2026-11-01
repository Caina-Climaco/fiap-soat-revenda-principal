"""Tracing distribuido (ADR-007): o Jaeger recebe os spans do Kong e dos servicos."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from apoio_e2e import Ambiente, esperar

pytestmark = pytest.mark.e2e


def _servicos(ambiente: Ambiente) -> set[str]:
    try:
        resposta = httpx.get(f"{ambiente.jaeger}/api/services", timeout=5.0)
    except httpx.HTTPError:
        return set()
    if resposta.status_code != 200:
        return set()
    return set(resposta.json().get("data") or [])


def test_kong_e_principal_publicam_traces(
    ambiente: Ambiente, novo_veiculo: Callable[..., dict[str, Any]]
) -> None:
    novo_veiculo()  # gera um trace que passa pelo Kong e pelo principal
    esperados = {"kong", "revenda-principal"} | ({"revenda-vendas"} if ambiente.vendas else set())
    esperar(lambda: esperados <= _servicos(ambiente), f"servicos {esperados} no Jaeger", 60)


def test_trace_do_cadastro_atravessa_kong_e_principal(
    ambiente: Ambiente, http: httpx.Client, gestor: dict[str, str]
) -> None:
    resposta = http.get("/api/v1/veiculos", headers=gestor)
    assert resposta.status_code == 200

    def trace_com_os_dois() -> bool:
        busca = httpx.get(
            f"{ambiente.jaeger}/api/traces",
            params={"service": "kong", "limit": 20, "lookback": "5m"},
            timeout=5.0,
        )
        if busca.status_code != 200:
            return False
        for trace in busca.json().get("data") or []:
            servicos = {p["serviceName"] for p in trace.get("processes", {}).values()}
            if {"kong", "revenda-principal"} <= servicos:
                return True
        return False

    esperar(trace_com_os_dois, "um trace com spans do Kong e do principal", 60)
