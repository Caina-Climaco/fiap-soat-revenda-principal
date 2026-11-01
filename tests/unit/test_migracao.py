"""Decisão da migração tolerante a rollback (src/principal/migracao.py), sem banco."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory

from apoio.banco import RAIZ_PROJETO
from principal.migracao import revisoes_desconhecidas


def _scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(RAIZ_PROJETO / "alembic.ini")))


def test_revisoes_conhecidas_nao_bloqueiam_o_upgrade() -> None:
    scripts = _scripts()
    assert revisoes_desconhecidas(scripts, scripts.get_heads()) == []
    assert revisoes_desconhecidas(scripts, []) == []  # banco novo, sem alembic_version


def test_revisao_de_versao_mais_nova_e_desconhecida() -> None:
    scripts = _scripts()
    assert revisoes_desconhecidas(scripts, ["0002_futura", *scripts.get_heads()]) == ["0002_futura"]
