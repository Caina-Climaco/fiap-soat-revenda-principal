# Revenda de Veículos: serviço principal

[![CI](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/actions/workflows/ci.yml/badge.svg)](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/actions/workflows/ci.yml)

Trabalho Substitutivo do Tech Challenge, FIAP PósTech Software Architecture (SOAT), Fase 4.
Autor: Cainã Clímaco, RM366473 (trabalho individual).

> **Esta entrega tem dois repositórios.** Este é o do **serviço principal** (Catálogo e Pagamentos, PostgreSQL), que também mantém a plataforma comum (Kong e observabilidade) e a documentação da solução. O **serviço de venda** (listagens e compras, MongoDB, orquestração da saga) está em **[fiap-soat-revenda-vendas](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas)**.

**Em construção.** Este README cresce a cada PR; a infraestrutura (kind, Terraform, Kong, observabilidade) e o CD entram nos próximos.

## 1. O que é

O "software principal" do enunciado: tudo o que não é listagem nem compra.

| Funcionalidade | Rota | Quem chama |
|---|---|---|
| Cadastrar veículo (marca, modelo, ano, cor, preço) | `POST /api/v1/veiculos` | Gestor (consumer `gestor` no Kong) |
| Editar veículo (só à venda) | `PATCH /api/v1/veiculos/{id}` | Gestor |
| Consultar e listar veículos (visão da loja) | `GET /api/v1/veiculos[/{id}]` | Gestor |
| Resultado do pagamento (efetuado ou cancelado), pelo código do pagamento | `POST /api/v1/pagamentos/webhook` | Processador de pagamentos |
| Passos da saga da compra: bloquear, desbloquear e vender o veículo; gerar e cancelar a cobrança | `/internal/v1/...` | Serviço de venda |

Cada cadastro ou edição publica o anúncio na vitrine do serviço de venda, e cada resultado de pagamento é repassado a ele. As duas coisas usam uma **outbox transacional**, entregue por HTTP. O desenho completo está em [docs/02, seção 2.8](docs/02-modelagem-ddd.md#28-saga-da-compra) e os contratos em [docs/05](docs/05-api.md).

## 2. Como foi implementado

| Tema | Decisão |
|---|---|
| Stack | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, httpx, uv ([ADR-002](docs/adrs/ADR-002-stack-e-postgresql-no-principal.md)) |
| Organização | Clean Architecture por contexto (`catalogo`, `pagamentos`), verificada pelo import-linter e por teste de arquitetura |
| Banco | PostgreSQL com schemas `catalogo`, `pagamentos` e `mensageria`, sem chave estrangeira entre eles |
| Bloqueio do veículo (T2) | Trava de linha + conferência da versão vista pelo comprador (RN-12); idempotente pela `compra_id` |
| Regra contra ressurreição | Toda compensação marca a compra; um passo atrasado dela é recusado com 409 `compra-compensada` |
| Pivô da saga | A cobrança decide a corrida entre pagamento e expiração sob trava de linha ([ADR-009](docs/adrs/ADR-009-pagamentos-no-principal.md)) |
| Outbox | Mensagem gravada na mesma transação; relay em segundo plano com `FOR UPDATE SKIP LOCKED`, ordem por agregado e backoff exponencial ([ADR-003](docs/adrs/ADR-003-http-com-outbox.md)) |
| Autorização | Consumer do Kong nas rotas do gestor, segredo no webhook, `X-Service-Token` nas rotas internas ([ADR-005](docs/adrs/ADR-005-sem-keycloak-autorizacao-na-borda.md)) |
| Observabilidade | Logs JSON com `request_id` e `trace_id`, métricas Prometheus em `/metrics` (negócio e outbox), OpenTelemetry quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido ([ADR-007](docs/adrs/ADR-007-tracing-distribuido-opentelemetry.md)) |

```
src/principal/
  catalogo/      domain (Veiculo), application (casos de uso), infrastructure (SQL), interfaces (HTTP)
  pagamentos/    domain (Cobranca), application, infrastructure, interfaces (webhook e rotas internas)
  mensageria/    tabela da outbox, gravação e relay
  shared/        config, banco, autorização, erros RFC 9457, logs, métricas, tracing, cliente HTTP
  composicao.py  liga os contextos à sessão de cada requisição
  main.py        app factory (uvicorn principal.main:app)
  migracao.py    migração tolerante a rollback (Job do Kubernetes)
```

## 3. Como testar localmente

Pré-requisitos: [uv](https://docs.astral.sh/uv/) e um PostgreSQL 16 acessível.

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports

# só unidade (sem banco)
uv run pytest -m unit

# unidade + integração com cobertura (o banco é recriado pelas migrações a cada execução)
export TEST_DATABASE_URL=postgresql+psycopg://revenda:revenda@localhost:5432/revenda_principal_test
uv run pytest -m "unit or integration" --cov=principal --cov-branch --cov-fail-under=80
```

Os testes de integração rodam contra PostgreSQL real e cobrem, entre outros, corridas de verdade no banco (pagamento e expiração simultâneos, bloqueios concorrentes, T2 atrasado contra C2) e o relay com duas "réplicas" disputando a outbox.

## 4. Fluxo de contribuição

A `main` é protegida: toda mudança entra por Pull Request, com título em Conventional Commits e os checks do CI verdes (`qualidade`, `testes`, `titulo-pr`). O script `scripts/windows/abrir-pr.ps1` cria a branch, faz o commit, abre o PR e acompanha os checks.

## 5. Documentação

| Documento | Conteúdo |
|---|---|
| [01. Visão geral](docs/01-visao-geral.md) | Problema, escopo, atores, premissas e restrições |
| [02. Modelagem DDD](docs/02-modelagem-ddd.md) | Domain Storytelling, Event Storming, contextos, agregados e a saga da compra |
| [03. Requisitos](docs/03-requisitos.md) | Requisitos funcionais e não funcionais |
| [05. Contratos da API](docs/05-api.md) | Rotas públicas e internas dos dois serviços |
| [ADRs](docs/adrs/README.md) | Decisões de arquitetura da solução |
