"""Configuração por variáveis de ambiente."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

_DRIVER = "postgresql+psycopg"


class DatabaseSettings(BaseSettings):
    """Somente o necessário para conectar ao banco.

    Separada de `Settings` para que o Job de migração (Alembic) não precise dos segredos
    do webhook nem dos tokens de serviço.
    """

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    database_url: str | None = Field(default=None, description="Tem precedência sobre DB_*")
    db_host: str = "localhost"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "revenda_principal"
    db_user: str = "revenda"
    db_password: SecretStr = SecretStr("")

    def url_banco(self) -> URL:
        if self.database_url:
            url = make_url(self.database_url)
            # Aceita "postgresql://" (formato comum em ferramentas) e força o driver psycopg 3.
            if url.drivername in ("postgresql", "postgres"):
                url = url.set(drivername=_DRIVER)
            return url
        return URL.create(
            drivername=_DRIVER,
            username=self.db_user,
            password=self.db_password.get_secret_value() or None,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


class Settings(DatabaseSettings):
    # Segredo do processador de pagamentos (ADR-009), validado em tempo constante.
    webhook_secret: SecretStr = Field(min_length=16)
    # Token que o serviço de venda apresenta ao chamar as rotas internas deste serviço.
    token_servico_entrada: SecretStr = Field(min_length=16)
    # Token que este serviço apresenta ao chamar as rotas internas do serviço de venda.
    token_servico_saida: SecretStr = Field(min_length=16)
    vendas_url: str = "http://revenda-vendas.vendas.svc.cluster.local:8000"
    # Consumer do Kong autorizado nas rotas do gestor (ADR-005).
    consumidor_gestor: str = "gestor"

    outbox_relay_ativo: bool = True
    outbox_intervalo_segundos: float = Field(default=0.5, gt=0, le=60)
    outbox_lote: int = Field(default=50, ge=1, le=1000)
    # Rede e 5xx são repetidos até este prazo (ADR-003); depois, FALHOU e reprocessamento.
    outbox_prazo_entrega_horas: float = Field(default=24, gt=0, le=720)
    # Limpeza periódica no laço do relay (principal/limpeza.py)
    outbox_retencao_dias: int = Field(default=7, ge=1, le=365)
    compensacoes_retencao_dias: int = Field(default=30, ge=7, le=365)
    outbox_timeout_segundos: float = Field(default=2.0, gt=0, le=30)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # Prefixo do Swagger e do openapi.json. No cluster é "/principal": o Kong publica a
    # documentação dos dois serviços na mesma porta (docs/05-api.md, seção 5.3).
    docs_prefixo: str = Field(default="", pattern=r"^(/[a-z0-9-]+)*$")

    @field_validator("vendas_url")
    @classmethod
    def _sem_barra_final(cls, valor: str) -> str:
        return valor.rstrip("/")

    @field_validator("log_level", mode="before")
    @classmethod
    def _maiusculas(cls, valor: object) -> object:
        return valor.upper() if isinstance(valor, str) else valor
