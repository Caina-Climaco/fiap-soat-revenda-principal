"""Migração do banco tolerante a rollback: `python -m principal.migracao` (Job do Kubernetes).

Regra:
- Banco sem `alembic_version` ou em revisão conhecida pelos scripts desta imagem →
  `alembic upgrade head` (sem efeito se já estiver no head).
- Banco em revisão DESCONHECIDA pelos scripts desta imagem ("banco à frente") → registra o
  fato e termina com sucesso (código 0) sem tocar no banco.

O segundo caso é o rollback: o CD reimplanta um SHA anterior cuja imagem não traz a
migração mais nova já aplicada. `alembic upgrade head` falharia ("Can't locate revision")
e o Job travaria o deploy. As migrações do projeto são aditivas (expandir/contrair), então o
código anterior roda sobre o schema mais novo; desfazer a migração (downgrade) é uma
decisão manual, nunca automática.

Executado em /app (onde ficam alembic.ini e migrations/), com as mesmas variáveis de banco
da API (DATABASE_URL ou DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from logging.config import fileConfig
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import Connection, create_engine, pool

from principal.shared.config import DatabaseSettings

_logger = logging.getLogger("principal.migracao")


@dataclass(frozen=True, slots=True)
class Resultado:
    aplicada: bool  # True: rodou `upgrade head`; False: banco à frente, nada feito
    revisoes_banco: tuple[str, ...]
    desconhecidas: tuple[str, ...]


def revisoes_desconhecidas(scripts: ScriptDirectory, revisoes: Iterable[str]) -> list[str]:
    """Revisões gravadas no banco que os scripts desta imagem não conhecem."""
    desconhecidas: list[str] = []
    for revisao in revisoes:
        try:
            conhecida = scripts.get_revision(revisao)
        except CommandError:
            conhecida = None
        if conhecida is None:
            desconhecidas.append(revisao)
    return desconhecidas


def migrar(config: Config, conexao: Connection) -> Resultado:
    """Decide e executa; `config` deve trazer `attributes["connection"] = conexao`."""
    atuais = tuple(MigrationContext.configure(conexao).get_current_heads())
    desconhecidas = tuple(revisoes_desconhecidas(ScriptDirectory.from_config(config), atuais))
    if desconhecidas:
        _logger.warning(
            "Banco em revisão desconhecida por esta imagem (%s); heads da imagem: %s. "
            "Provável rollback para uma versão anterior: migração ignorada, banco intacto.",
            ", ".join(desconhecidas),
            ", ".join(ScriptDirectory.from_config(config).get_heads()),
        )
        return Resultado(aplicada=False, revisoes_banco=atuais, desconhecidas=desconhecidas)
    _logger.info(
        "Revisão atual do banco: %s. Aplicando upgrade head.", ", ".join(atuais) or "nenhuma"
    )
    command.upgrade(config, "head")
    return Resultado(aplicada=True, revisoes_banco=atuais, desconhecidas=())


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ini = Path(args[0] if args else "alembic.ini")
    config = Config(str(ini))
    fileConfig(str(ini), disable_existing_loggers=False)
    logging.getLogger("principal").setLevel(logging.INFO)

    engine = create_engine(DatabaseSettings().url_banco(), poolclass=pool.NullPool)
    try:
        with engine.connect() as conexao:
            # A mesma conexão serve à verificação e ao upgrade (migrations/env.py a reutiliza);
            # o commit é nosso porque a transação foi aberta aqui.
            config.attributes["connection"] = conexao
            migrar(config, conexao)
            conexao.commit()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover - ponto de entrada do Job
    raise SystemExit(main())
