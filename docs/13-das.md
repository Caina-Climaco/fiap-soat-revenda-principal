# 13. Design Approval Sheet (DAS)

Folha de aprovação do desenho da solução: resume o que é entregue, as decisões, os riscos e as evoluções. O detalhe está nos documentos e ADRs referenciados.

## 13.1 Identificação

| Campo | Valor |
|---|---|
| Projeto | Revenda de Veículos: serviço principal ([fiap-soat-revenda-principal](https://github.com/Caina-Climaco/fiap-soat-revenda-principal)) e serviço de venda ([fiap-soat-revenda-vendas](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas)) |
| Contexto | Trabalho Substitutivo do Tech Challenge, FIAP PósTech Software Architecture (SOAT), Fase 4 |
| Versão do desenho | 1.0 |
| Autor (proponente) | Cainã Clímaco, RM366473 |
| Data | 01/11/2026 |
| Entrega prevista | 15/11/2026 |

## 13.2 Resumo da solução e escopo

Duas APIs REST em Python 3.12 e FastAPI. O **serviço principal** cadastra e edita veículos (PostgreSQL), gera as cobranças e recebe o webhook do processador de pagamentos. O **serviço de venda** mantém a vitrine (réplica de leitura dos anúncios), recebe as compras com o CPF do comprador (MongoDB, CPF cifrado) e orquestra a saga da compra, chamando o principal por HTTP para bloquear o veículo, gerar a cobrança e registrar a venda. No sentido inverso, o principal entrega a réplica dos anúncios e o resultado do pagamento por uma outbox transacional. O Kong é a única entrada; Prometheus, Grafana, OpenTelemetry e Jaeger dão métricas, alertas e traces de ponta a ponta. Tudo roda num cluster kind local, com infraestrutura em Terraform e deploy automático a cada merge na `main` de cada repositório.

Fora do escopo: front-end, pagamento real, cadastro de compradores, nota fiscal, nuvem pública ([01, seção 1.3](01-visao-geral.md#13-escopo)).

## 13.3 Requisitos atendidos

| Grupo | Itens | Referência |
|---|---|---|
| Funcionais do enunciado | Cadastrar e editar veículo; venda com CPF e data; webhook por código do pagamento (efetuado ou cancelado); listagens à venda e vendidos por preço | RF-01 a RF-07, [03](03-requisitos.md#32-requisitos-funcionais) |
| Funcionais descobertos na modelagem | Expiração da reserva, compensação em falha, retomada da saga, consultas, paginação, saúde, OpenAPI | RF-08 a RF-16 |
| Isolamento do serviço de venda | Serviço e banco próprios, comunicação só por HTTP | RNF-01, ADR-001 |
| Não funcionais | Consistência, resiliência, segurança, LGPD, observabilidade, contratos, CI/CD | RNF-02 a RNF-18, [03](03-requisitos.md#33-requisitos-não-funcionais) |

## 13.4 Decisões de arquitetura

| ADR | Decisão |
|---|---|
| [001](adrs/ADR-001-decomposicao-em-dois-servicos.md) | Dois serviços: principal (catálogo e pagamentos) e venda (vitrine, compra e saga) |
| [002](adrs/ADR-002-stack-e-postgresql-no-principal.md) | Python 3.12, FastAPI, SQLAlchemy 2, Alembic; PostgreSQL no principal |
| [003](adrs/ADR-003-http-com-outbox.md) | HTTP entre os serviços; outbox transacional para o que o principal entrega |
| [004](adrs/ADR-004-resiliencia-entre-servicos.md) | Timeout, retry com jitter, circuit breaker, bulkhead e idempotência |
| [005](adrs/ADR-005-sem-keycloak-autorizacao-na-borda.md) | Sem Keycloak: consumers do Kong para gestor e processador; token de serviço nas rotas internas |
| [006](adrs/ADR-006-plataforma-compartilhada.md) | Plataforma (kind, Kong, observabilidade) mantida pelo repositório principal |
| [007](adrs/ADR-007-tracing-distribuido-opentelemetry.md) | Tracing distribuído com OpenTelemetry e Jaeger |
| [008](adrs/ADR-008-qualidade-sonarcloud-contrato-bdd.md) | Cobertura mínima, SonarCloud, testes de contrato e BDD |
| [009](adrs/ADR-009-pagamentos-no-principal.md) | Cobrança e webhook no principal; a cobrança decide a corrida entre pagamento e expiração |
| [Venda 001](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-001-mongodb.md) | MongoDB 7 no serviço de venda, sem transações entre documentos |
| [Venda 002](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-002-saga-orquestrada.md) | Saga orquestrada pelo serviço de venda |
| [Venda 003](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-003-replica-de-leitura-dos-anuncios.md) | Réplica de leitura dos anúncios |
| [Venda 004](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-004-contramedidas-de-isolamento.md) | Contramedidas de isolamento da saga (trava semântica, releitura da versão) |
| [Venda 005](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-005-cpf-lgpd.md) | CPF validado, cifrado, mascarado e com retenção |
| [Venda 006](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-006-expiracao-e-recuperacao-de-sagas.md) | Expiração e recuperação de sagas por CronJob |

## 13.5 Atributos de qualidade

| Atributo | Como é atendido | Evidência |
|---|---|---|
| Consistência | Índice único parcial (uma compra ativa por veículo), passos idempotentes, regra contra ressurreição, a cobrança como árbitra | Testes de concorrência nos dois serviços; BDD |
| Resiliência | Falha antes do pivô desfaz a compra na hora; recuperação a cada minuto; circuit breaker | e2e `test_e2e_caos` no CD do serviço de venda |
| Segurança e privacidade | Borda única, rotas internas fechadas, NetworkPolicies, CPF cifrado e mascarado, nada sensível em logs e traces | [07](07-seguranca-lgpd.md); e2e da borda |
| Implantabilidade | PR obrigatório com 6 checks, CD automático com e2e, trava comum entre os CDs | [08](08-ci-cd.md) |
| Testabilidade | Clean Architecture, relógio injetável, dublês validados por contrato; 100% de cobertura | [09](09-testes.md) |
| Observabilidade | Métricas, painéis de saga e resiliência, alertas testados, traces de ponta a ponta | [12](12-observabilidade.md) |

## 13.6 Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Divergência entre os serviços nas rotas internas | Contratos versionados e verificados nos dois lados, com comparação com a `main` do outro repositório |
| Principal fora do ar durante compras | Vitrine local, desfazimento imediato, circuit breaker, recuperação; demonstrado no CD |
| Pagamento confirmado depois do cancelamento | A cobrança decide sob trava de linha; o caso residual é marcado para estorno e gera alerta |
| Runner self-hosted em repositório público | CD só na `main`, CI de PR no runner hospedado, `main` protegida, segredos lidos do cluster e mascarados |
| Memória do PC | Limits somados em cerca de 5,2 GiB |

Lista completa em [10, seção 10.5](10-plano-execucao.md#105-riscos).

## 13.7 Custos

| Item | Ambiente local | Estimativa qualitativa em nuvem |
|---|---|---|
| Computação | Zero (kind no PC) | Kubernetes gerenciado com 2 a 3 nós pequenos |
| Bancos | Zero (StatefulSets) | PostgreSQL e MongoDB gerenciados: o maior custo depois dos nós |
| CI/CD | Zero (runner hospedado gratuito para repositório público; runner no PC) | Minutos de runner e registry de imagens: baixo |
| Qualidade | Zero (SonarCloud gratuito para repositório público) | Igual |
| Gateway e observabilidade | Zero (Kong, Prometheus, Grafana, Jaeger no cluster) | No cluster (custo de nó) ou serviços gerenciados por volume |

## 13.8 Evoluções

- Mensageria (fila ou log de eventos) no lugar do HTTP da outbox quando houver mais consumidores.
- TLS no Kong, mTLS entre os serviços e identidade de workload no lugar do token de serviço.
- Identidade do comprador (Keycloak, como na Fase 3) e consulta das próprias compras.
- Assinatura HMAC do corpo no webhook, com proteção contra replay.
- Jaeger com armazenamento persistente e amostragem por cauda; Alertmanager com notificação.
- Rotação da chave do CPF com recifragem gradual (o `kid` já é gravado com a cifra).
- Backend remoto do Terraform e cofre de segredos.

## 13.9 Aprovação

| Papel | Nome | Data | Parecer |
|---|---|---|---|
| Proponente (autor) | Cainã Clímaco | 01/11/2026 | Submetido para aprovação |
| Revisões de arquitetura, segurança e operação | | | Trabalho individual: exercidas pelo autor com apoio das verificações automáticas (CI, contratos, SonarCloud, e2e) |
| Avaliador (FIAP) | | | Reservado para o parecer da avaliação |
