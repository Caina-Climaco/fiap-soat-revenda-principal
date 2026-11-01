"""Engine, sessões e Unit of Work SQLAlchemy (síncrono, psycopg 3)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from principal.shared.eventos import EventoDominio, PublicadorEventos

# Convenção de nomes das constraints, compartilhada pelos metadados de cada módulo, para
# que os nomes gerados sejam estáveis e iguais aos de docs/06-dados.md.
CONVENCAO_NOMES = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def criar_engine(url: str | URL, *, pool_size: int = 5, max_overflow: int = 5) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        # Erros do banco (ex.: DataError) não levam os valores dos parâmetros para a
        # mensagem da exceção nem, portanto, para o log de erro 500.
        hide_parameters=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        # Readiness probe precisa falhar rápido quando o banco está fora. A sessão em UTC
        # garante que timestamptz volte em UTC qualquer que seja o fuso do servidor.
        connect_args={
            "connect_timeout": 2,
            "application_name": "revenda-principal",
            "options": "-c timezone=UTC",
        },
    )


class BancoDeDados:
    """Engine + fábrica de sessões; uma sessão (e transação) por requisição."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # expire_on_commit=False: os repositórios devolvem objetos de domínio, não instâncias
        # ORM; nada precisa ser recarregado após o commit.
        self._fabrica = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def nova_sessao(self) -> Session:
        return self._fabrica()

    def sessao(self) -> Iterator[Session]:
        """Dependência FastAPI: o que não tiver sido confirmado é desfeito ao final."""
        sessao = self._fabrica()
        try:
            yield sessao
        finally:
            sessao.close()

    def disponivel(self) -> bool:
        try:
            with self.engine.connect() as conexao:
                conexao.execute(text("SET LOCAL statement_timeout = 2000"))
                conexao.execute(text("SELECT 1"))
        except Exception:  # qualquer falha de conexão/consulta = não pronto
            return False
        return True


class SqlUnidadeDeTrabalho:
    """Implementa `UnidadeDeTrabalho` sobre uma sessão SQLAlchemy.

    Os eventos só são publicados depois do commit, para não registrar em log eventos de
    transações desfeitas (docs/04-arquitetura.md, seção 7.4).
    """

    def __init__(self, sessao: Session, publicador: PublicadorEventos) -> None:
        self._sessao = sessao
        self._publicador = publicador
        self._pendentes: list[EventoDominio] = []

    def registrar_eventos(self, eventos: Iterable[EventoDominio]) -> None:
        self._pendentes.extend(eventos)

    def confirmar(self) -> None:
        self._sessao.commit()
        eventos, self._pendentes = self._pendentes, []
        self._publicador.publicar(eventos)
