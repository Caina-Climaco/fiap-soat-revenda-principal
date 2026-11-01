"""Tracing distribuido (ADR-007): o Jaeger recebe os spans do Kong e dos servicos.

Usa a API de consulta v3 do Jaeger (`/api/v3`, respostas em OTLP JSON) e, se ela nao
existir, a API interna antiga (`/api/services`, `/api/traces`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from apoio_e2e import Ambiente, esperar

pytestmark = pytest.mark.e2e


def _get(ambiente: Ambiente, caminho: str, params: dict[str, Any] | None = None) -> Any:
    try:
        resposta = httpx.get(f"{ambiente.jaeger}{caminho}", params=params, timeout=5.0)
    except httpx.HTTPError:
        return None
    return resposta.json() if resposta.status_code == 200 else None


def _servicos(ambiente: Ambiente) -> set[str]:
    v3 = _get(ambiente, "/api/v3/services")
    if v3 is not None:
        return set(v3.get("services") or [])
    antiga = _get(ambiente, "/api/services")
    return set((antiga or {}).get("data") or [])


def _servicos_por_trace(ambiente: Ambiente, servico: str) -> list[set[str]]:
    """Para cada trace recente que passou por `servico`, os servicos que tem spans nele."""
    agora = datetime.now(UTC)
    v3 = _get(
        ambiente,
        "/api/v3/traces",
        {
            "query.service_name": servico,
            "query.start_time_min": (agora - timedelta(minutes=5)).isoformat(),
            "query.start_time_max": (agora + timedelta(minutes=1)).isoformat(),
            "query.num_traces": 20,
        },
    )
    if v3 is not None:
        por_trace: dict[str, set[str]] = defaultdict(set)
        for recurso in (v3.get("result") or {}).get("resourceSpans") or []:
            nome = next(
                (
                    a["value"].get("stringValue", "")
                    for a in recurso.get("resource", {}).get("attributes", [])
                    if a.get("key") == "service.name"
                ),
                "",
            )
            for escopo in recurso.get("scopeSpans") or []:
                for span in escopo.get("spans") or []:
                    por_trace[span["traceId"]].add(nome)
        return list(por_trace.values())
    antiga = _get(ambiente, "/api/traces", {"service": servico, "limit": 20, "lookback": "5m"})
    return [
        {p["serviceName"] for p in trace.get("processes", {}).values()}
        for trace in (antiga or {}).get("data") or []
    ]


def test_kong_e_principal_publicam_traces(
    ambiente: Ambiente, novo_veiculo: Callable[..., dict[str, Any]]
) -> None:
    novo_veiculo()  # gera um trace que passa pelo Kong e pelo principal
    esperados = {"kong", "revenda-principal"} | ({"revenda-vendas"} if ambiente.vendas else set())
    esperar(lambda: esperados <= _servicos(ambiente), f"servicos {esperados} no Jaeger", 60)


def test_trace_do_gestor_atravessa_kong_e_principal(
    ambiente: Ambiente, http: httpx.Client, gestor: dict[str, str]
) -> None:
    assert http.get("/api/v1/veiculos", headers=gestor).status_code == 200
    esperar(
        lambda: any(
            {"kong", "revenda-principal"} <= servicos
            for servicos in _servicos_por_trace(ambiente, "kong")
        ),
        "um trace com spans do Kong e do principal",
        60,
    )
