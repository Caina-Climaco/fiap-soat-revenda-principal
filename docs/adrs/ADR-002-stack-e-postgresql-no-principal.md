# ADR-002: Stack Python e PostgreSQL no serviço principal

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

A Fase 3 entregou uma API em Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 e Alembic, com Clean Architecture verificada por import-linter, e todo o ferramental de qualidade em volta (ruff, mypy strict, pytest-cov, Trivy). O prazo da Fase 4 é de duas semanas. O enunciado exige bancos diferentes nos dois serviços, e os temas da fase incluem bancos relacionais e não relacionais.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Mesma stack nos dois serviços; PostgreSQL no principal** | Reuso do código e do ferramental; o principal é transacional e relacional por natureza (cobrança com transição condicional, outbox na mesma transação) | Nenhum relevante |
| Outra linguagem no serviço de venda | Mostraria poliglotismo | Duplica ferramental, pipelines e curva de aprendizado; nenhum requisito pede |
| MongoDB nos dois | Um único tipo de banco | A outbox e a arbitragem da cobrança são mais naturais com transações relacionais; o enunciado quer bancos diferentes |

## Decisão

- Os dois serviços usam **Python 3.12, FastAPI, Pydantic v2, uv** e a mesma organização em Clean Architecture (`domain`, `application`, `infrastructure`, `interfaces`).
- O principal usa **PostgreSQL 16** com SQLAlchemy 2 (síncrono) e Alembic, com os schemas `catalogo`, `pagamentos` e `mensageria` (outbox), sem chave estrangeira entre schemas.
- O serviço de venda usa **MongoDB** ([ADR-001 do serviço de venda](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-001-mongodb.md)).
- Chamadas HTTP entre serviços com **httpx** (cliente síncrono, timeouts explícitos).

## Consequências

### Positivas
- Reuso alto de código, testes, pipelines e documentação da Fase 3.
- Uma só forma de organizar o código e de verificar a arquitetura nos dois repositórios.
- A solução usa um banco relacional e um não relacional, como pedem os temas da fase.

### Negativas
- Os dois serviços dependem do mesmo ecossistema; uma vulnerabilidade comum afeta ambos.

## Mitigações
- Dependabot e Trivy nos dois repositórios; versões fixadas no `uv.lock` de cada um.
