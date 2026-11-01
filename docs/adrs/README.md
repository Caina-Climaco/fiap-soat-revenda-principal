# Architecture Decision Records

Decisões de arquitetura da solução (serviço principal e plataforma comum). As decisões próprias do serviço de venda estão no [repositório dele](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/tree/main/docs/adrs). Cada ADR descreve o contexto, as alternativas avaliadas, a decisão e as consequências.

A Fase 4 recomeça a numeração. As decisões da Fase 3 que continuam valendo (kind via CLI, imagem sem registry com tag igual ao SHA, segredos gerados pelo Terraform, CI hospedado e CD self-hosted, Kong DB-less, Prometheus e Grafana) são reaproveitadas e citadas onde mudam.

| Nº | Decisão | Status |
|---|---|---|
| [ADR-001](ADR-001-decomposicao-em-dois-servicos.md) | Decomposição em serviço principal e serviço de venda | Aceito (substitui o ADR-002 da Fase 3) |
| [ADR-002](ADR-002-stack-e-postgresql-no-principal.md) | Stack Python e PostgreSQL no serviço principal | Aceito |
| [ADR-003](ADR-003-http-com-outbox.md) | Comunicação HTTP entre serviços, com outbox transacional | Aceito |
| [ADR-004](ADR-004-resiliencia-entre-servicos.md) | Resiliência nas chamadas entre serviços | Aceito |
| [ADR-005](ADR-005-sem-keycloak-autorizacao-na-borda.md) | Sem Keycloak; autorização na borda e token de serviço nas rotas internas | Aceito (substitui os ADRs 001 e 014 da Fase 3) |
| [ADR-006](ADR-006-plataforma-compartilhada.md) | Plataforma local compartilhada, mantida pelo repositório principal | Aceito |
| [ADR-007](ADR-007-tracing-distribuido-opentelemetry.md) | Tracing distribuído com OpenTelemetry e Jaeger | Aceito |
| [ADR-008](ADR-008-qualidade-sonarcloud-contrato-bdd.md) | Estratégia de qualidade: cobertura, SonarCloud, testes de contrato e BDD | Aceito |
| [ADR-009](ADR-009-pagamentos-no-principal.md) | Pagamentos no serviço principal, com a cobrança como árbitro do pivô | Aceito (atualiza o ADR-007 da Fase 3) |
