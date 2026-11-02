# Revenda de Veículos: serviço principal

[![CI](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/actions/workflows/ci.yml/badge.svg)](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/actions/workflows/ci.yml)
[![CD](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/actions/workflows/cd.yml/badge.svg)](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/actions/workflows/cd.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=Caina-Climaco_fiap-soat-revenda-principal&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Caina-Climaco_fiap-soat-revenda-principal)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=Caina-Climaco_fiap-soat-revenda-principal&metric=coverage)](https://sonarcloud.io/summary/new_code?id=Caina-Climaco_fiap-soat-revenda-principal)

Trabalho Substitutivo do Tech Challenge, FIAP PósTech Software Architecture (SOAT), Fase 4.
Autor: Cainã Clímaco, RM366473 (trabalho individual).

> **Esta entrega tem dois repositórios.** Este é o do **serviço principal** (Catálogo e Pagamentos, PostgreSQL), que também mantém a plataforma comum (Kong e observabilidade) e a documentação da solução. O **serviço de venda** (listagens e compras, MongoDB, orquestração da saga) está em **[fiap-soat-revenda-vendas](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas)**.

## 1. O que é

O "software principal" do enunciado: tudo o que não é listagem nem compra.

| Funcionalidade | Rota | Quem chama |
|---|---|---|
| Cadastrar veículo (marca, modelo, ano, cor, preço) | `POST /api/v1/veiculos` | Gestor (consumer `gestor` no Kong) |
| Editar veículo (só à venda) | `PATCH /api/v1/veiculos/{id}` | Gestor |
| Consultar e listar veículos (visão da loja) | `GET /api/v1/veiculos[/{id}]` | Gestor |
| Resultado do pagamento (efetuado ou cancelado), pelo código do pagamento | `POST /api/v1/pagamentos/webhook` | Processador de pagamentos, com dois headers: `apikey` (consumer `gateway-pagamento` no Kong) e `X-Webhook-Secret` |
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
  composicao.py  liga os contextos à sessão de cada requisição (e monta a limpeza periódica)
  limpeza.py     retenção: apaga a outbox entregue e as marcas de compensação antigas
  outbox.py      CLI de operação: python -m principal.outbox reprocessar [ID...]
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

# unidade + integração com cobertura (100%; o banco é recriado pelas migrações a cada execução)
export TEST_DATABASE_URL=postgresql+psycopg://revenda:revenda@localhost:5432/revenda_principal_test
uv run pytest -m "unit or integration" --cov=principal --cov-branch --cov-fail-under=80

# contratos das rotas internas com o serviço de venda (contratos/README.md)
uv run pytest -m contrato
```

Os testes de integração rodam contra PostgreSQL real e cobrem, entre outros, corridas de verdade no banco (pagamento e expiração simultâneos, bloqueios concorrentes, T2 atrasado contra C2) e o relay com duas "réplicas" disputando a outbox.

## 4. Como executar localmente (cluster kind)

Tudo roda num cluster [kind](https://kind.sigs.k8s.io/) no PC, criado e configurado por Terraform. Pré-requisitos: Windows 11 com Docker Desktop, `kind`, `kubectl` e `terraform` (`scripts/windows/01-instalar-ferramentas.ps1` instala o que faltar).

> O ambiente completo (kind, Terraform e o runner do CD) foi montado para Windows 11. Em outros sistemas, os testes de unidade, integração e contrato rodam com `uv` e um PostgreSQL (seção 3); a evidência do ambiente implantado está no vídeo da entrega.

```powershell
# 1. Plataforma e infraestrutura do principal: cluster, PostgreSQL, Kong, Prometheus,
#    Grafana, OpenTelemetry Collector e Jaeger (-RecriarCluster se houver um cluster da Fase 3)
powershell -ExecutionPolicy Bypass -File .\scripts\windows\04-subir-ambiente.ps1

# 2. Runner do CD deste repositório (container no Docker Desktop) e o primeiro deploy
powershell -ExecutionPolicy Bypass -File .\scripts\windows\03-instalar-runner.ps1
gh workflow run cd.yml -R Caina-Climaco/fiap-soat-revenda-principal

# 3. Depois, o serviço de venda pelo repositório dele (fiap-soat-revenda-vendas)

# Chaves e segredos para a demonstração
powershell -ExecutionPolicy Bypass -File .\scripts\windows\segredos.ps1

# Apaga os veículos E2E que os testes ponta a ponta deixam nos dois bancos (-Simular só conta; -Tudo zera)
powershell -ExecutionPolicy Bypass -File .\scripts\windows\limpar-dados.ps1
```

Exemplos pelo Kong (chaves e segredo mostrados pelo `segredos.ps1`):

```bash
# Cadastro de veículo (gestor)
curl -X POST http://localhost:8080/api/v1/veiculos \
  -H "apikey: <chave do gestor>" -H "Content-Type: application/json" \
  -d '{"marca":"Fiat","modelo":"Argo","ano":2022,"cor":"Branco","preco":"72900.00"}'

# Resultado do pagamento (processador): exige os dois headers
curl -X POST http://localhost:8080/api/v1/pagamentos/webhook \
  -H "apikey: <chave do gateway de pagamento>" -H "X-Webhook-Secret: <segredo do webhook>" \
  -H "Content-Type: application/json" \
  -d '{"codigo_pagamento":"<código devolvido na compra>","status":"PAGO"}'
```

| Endereço | O quê |
|---|---|
| `http://localhost:8080` | Kong, a única entrada pública dos dois serviços |
| `http://localhost:8080/principal/docs` | Swagger do principal (rotas do gestor exigem o header `apikey`) |
| `http://localhost:3000` | Grafana: painéis "visão geral" e "saga e resiliência" |
| `http://localhost:9090` | Prometheus: alvos e alertas |
| `http://localhost:16686` | Jaeger: traces da compra de ponta a ponta |
| `localhost:15432` | PostgreSQL do principal (demonstração) |

Exemplo com a chave do gestor:

```bash
curl -X POST http://localhost:8080/api/v1/veiculos -H "apikey: <chave do gestor>" \
  -H "Content-Type: application/json" \
  -d '{"marca":"Fiat","modelo":"Argo","ano":2022,"cor":"Branco","preco":"72900.00"}'
```

Detalhes da infraestrutura em [infra/README.md](infra/README.md) e do CI/CD em [docs/08](docs/08-ci-cd.md).

## 5. Fluxo de contribuição

A `main` é protegida: toda mudança entra por Pull Request, com título em Conventional Commits e os checks do CI verdes (`qualidade`, `testes`, `contrato`, `sonarcloud`, `imagem`, `infra`, `titulo-pr`). O merge dispara o CD, que implanta no kind e roda os testes ponta a ponta pelo Kong (`tests/e2e`). O script `scripts/windows/abrir-pr.ps1` cria a branch, faz o commit, abre o PR e acompanha os checks. O vídeo de entrega é gravado e montado pelo kit em [`scripts/video/`](scripts/video/README.md) (roteiro em [docs/11](docs/11-roteiro-video.md)).

## 6. Documentação

| Documento | Conteúdo |
|---|---|
| [01. Visão geral](docs/01-visao-geral.md) | Problema, escopo, atores, premissas e restrições |
| [02. Modelagem DDD](docs/02-modelagem-ddd.md) | Domain Storytelling, Event Storming, contextos, agregados e a saga da compra |
| [03. Requisitos](docs/03-requisitos.md) | Requisitos funcionais e não funcionais |
| [04. Arquitetura](docs/04-arquitetura.md) | C4, implantação e diagramas de sequência |
| [05. Contratos da API](docs/05-api.md) | Rotas públicas e internas dos dois serviços |
| [06. Dados](docs/06-dados.md) | Bancos, tabelas, coleções, índices e retenção |
| [07. Segurança e LGPD](docs/07-seguranca-lgpd.md) | Ameaças, controles e o tratamento do CPF |
| [08. CI/CD e infraestrutura](docs/08-ci-cd.md) | Checks do CI, passos do CD, scripts do ambiente local e recursos |
| [09. Testes](docs/09-testes.md) | Pirâmide, contratos, BDD, e2e e testes de requisitos não funcionais |
| [10. Plano de execução](docs/10-plano-execucao.md) | Backlog, critérios de pronto, cronograma e riscos |
| [11. Roteiro do vídeo](docs/11-roteiro-video.md) | Demonstração da entrega |
| [12. Observabilidade](docs/12-observabilidade.md) | Logs, métricas, traces, painéis e alertas |
| [13. DAS](docs/13-das.md) | Folha de aprovação do desenho |
| [ADRs](docs/adrs/README.md) | Decisões de arquitetura da solução |
| [Contratos internos](contratos/README.md) | Contratos OpenAPI das rotas internas e como são verificados |
