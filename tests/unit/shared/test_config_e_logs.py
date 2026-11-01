"""Configuração, logs JSON (com ids do trace) e relógio."""

from __future__ import annotations

import io
import json
import logging
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from principal.shared.clock import RelogioFixo, RelogioSistema
from principal.shared.config import DatabaseSettings, Settings
from principal.shared.eventos import PublicadorEventosLog
from principal.shared.logging import FormatadorJson, configurar_logs, request_id_atual

VARIAVEIS = (
    "DATABASE_URL",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "WEBHOOK_SECRET",
    "TOKEN_SERVICO_ENTRADA",
    "TOKEN_SERVICO_SAIDA",
    "VENDAS_URL",
    "OUTBOX_RELAY_ATIVO",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


def test_settings_le_variaveis_do_contrato(monkeypatch: pytest.MonkeyPatch) -> None:
    valores = {
        "DB_HOST": "revenda-principal-db.principal.svc.cluster.local",
        "DB_PORT": "5432",
        "DB_NAME": "revenda_principal",
        "DB_USER": "revenda",
        "DB_PASSWORD": "s3nh@:/x",
        "WEBHOOK_SECRET": "x" * 32,
        "TOKEN_SERVICO_ENTRADA": "e" * 32,
        "TOKEN_SERVICO_SAIDA": "s" * 32,
        "VENDAS_URL": "http://revenda-vendas.vendas.svc.cluster.local:8000/",
        "OUTBOX_RELAY_ATIVO": "false",
        "LOG_LEVEL": "debug",
    }
    for nome, valor in valores.items():
        monkeypatch.setenv(nome, valor)
    settings = Settings()
    url = settings.url_banco()
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "revenda-principal-db.principal.svc.cluster.local"
    assert url.password == "s3nh@:/x"
    assert settings.vendas_url == "http://revenda-vendas.vendas.svc.cluster.local:8000"
    assert settings.outbox_relay_ativo is False
    assert settings.log_level == "DEBUG"
    assert settings.consumidor_gestor == "gestor"


def test_database_url_tem_precedencia_e_forca_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:1/b")
    monkeypatch.setenv("DB_HOST", "ignorado")
    url = DatabaseSettings().url_banco()
    assert (url.drivername, url.host, url.port, url.database) == ("postgresql+psycopg", "h", 1, "b")


@pytest.mark.parametrize(
    "campo", ["webhook_secret", "token_servico_entrada", "token_servico_saida"]
)
def test_segredos_sao_obrigatorios_e_fortes(campo: str) -> None:
    segredos = {
        "webhook_secret": "w" * 16,
        "token_servico_entrada": "e" * 16,
        "token_servico_saida": "s" * 16,
    }
    Settings(**segredos)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(**{**segredos, campo: "curto"})  # type: ignore[arg-type]
    del segredos[campo]
    with pytest.raises(ValidationError):
        Settings(**segredos)  # type: ignore[arg-type]


def test_formatador_json_inclui_request_id_e_campos() -> None:
    registro = logging.LogRecord(
        "principal.teste", logging.INFO, __file__, 1, "olá %s", ("x",), None
    )
    registro.campos = {"evento": "CobrancaPaga"}
    registro.extra_livre = 7
    registro.color_message = "\x1b[36mcolorido\x1b[0m"  # anexado pelo uvicorn
    token = request_id_atual.set("req-1")
    try:
        saida = json.loads(FormatadorJson().format(registro))
    finally:
        request_id_atual.reset(token)
    assert saida["mensagem"] == "olá x"
    assert saida["request_id"] == "req-1"
    assert saida["evento"] == "CobrancaPaga"
    assert saida["extra_livre"] == 7
    assert saida["nivel"] == "INFO"
    assert "color_message" not in saida


def test_formatador_json_registra_excecao() -> None:
    try:
        raise RuntimeError("falhou")
    except RuntimeError:
        registro = logging.LogRecord("x", logging.ERROR, __file__, 1, "erro", (), sys.exc_info())
    saida = json.loads(FormatadorJson().format(registro))
    assert "RuntimeError: falhou" in saida["excecao"]


def test_configurar_logs_e_publicador_de_eventos() -> None:
    configurar_logs("INFO")
    raiz = logging.getLogger()
    buffer = io.StringIO()
    raiz.handlers[0].setStream(buffer)  # type: ignore[attr-defined]

    class Evento:
        nome = "VeiculoCadastrado"

        def como_dict(self) -> dict[str, str]:
            return {"veiculo_id": "v1"}

    PublicadorEventosLog().publicar([Evento()])
    linha = json.loads(buffer.getvalue().strip())
    assert linha["evento"] == "VeiculoCadastrado"
    assert linha["veiculo_id"] == "v1"
    assert logging.getLogger("uvicorn.access").disabled


def test_relogios() -> None:
    assert RelogioSistema().agora().tzinfo is UTC
    relogio = RelogioFixo(datetime(2026, 1, 1, tzinfo=UTC))
    relogio.avancar(timedelta(minutes=5))
    assert relogio.agora() == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    relogio.definir(datetime(2027, 1, 1, tzinfo=UTC))
    assert relogio.agora().year == 2027
    with pytest.raises(ValueError, match="timezone"):
        RelogioFixo(datetime(2026, 1, 1))
