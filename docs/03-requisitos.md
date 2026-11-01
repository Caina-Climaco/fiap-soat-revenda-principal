# 3. Requisitos

Este documento lista os requisitos funcionais (RF) e não funcionais (RNF) da solução, com a origem de cada um (enunciado da Fase 4, temas da fase ou modelagem, [02-modelagem-ddd.md](02-modelagem-ddd.md)) e o serviço que o atende. A matriz de rastreabilidade RF → endpoint → caso de uso → teste é preenchida junto com o código.

## 3.1 Convenções

| Campo | Valores |
|---|---|
| Origem | **E** = enunciado do substitutivo da Fase 4; **F** = temas da fase (aulas e Tech Challenge oficial); **M** = modelagem |
| Prioridade | **Must** (obrigatório), **Should** (importante, entra no prazo), **Could** (desejável) |
| Serviço | **P** = principal (`revenda-principal`); **V** = venda (`revenda-vendas`) |

Rotas públicas usam o prefixo `/api/v1` e passam pelo Kong. Rotas internas usam `/internal/v1`, não têm rota no Kong e só aceitam o outro serviço.

## 3.2 Requisitos funcionais

| ID | Requisito | Serviço | Origem | Prioridade | Regras |
|---|---|---|---|---|---|
| RF-01 | O gestor cadastra um veículo (marca, modelo, ano, cor, preço); ele nasce à venda e é publicado na vitrine | P (+ V) | E | Must | RN-15, RN-16, RN-18 |
| RF-02 | O gestor edita um veículo à venda; a nova versão é publicada na vitrine | P (+ V) | E | Must | RN-02, RN-15 |
| RF-03 | Qualquer pessoa lista os veículos à venda, do mais barato ao mais caro | V | E | Must | RN-17, RN-22 |
| RF-04 | Qualquer pessoa lista os veículos vendidos, do mais barato ao mais caro | V | E | Must | RN-17, RN-20, RN-22 |
| RF-05 | O comprador inicia a compra de um veículo informando o CPF; recebe o código do pagamento e o prazo | V (+ P) | E | Must | RN-01, RN-03, RN-05, RN-06, RN-12, RN-14 |
| RF-06 | O processador de pagamentos informa, pelo código do pagamento, que o pagamento foi **efetuado**; a compra é efetivada com a data da venda e o veículo fica vendido | P (+ V) | E | Must | RN-07, RN-08, RN-09, RN-13 |
| RF-07 | O processador informa que o pagamento foi **cancelado**; a compra é cancelada e o veículo volta à vitrine | P (+ V) | E | Must | RN-09, RN-11, RN-13 |
| RF-08 | A reserva expira após o prazo; a compra é cancelada e o veículo volta à vitrine, salvo se o pagamento já tiver sido efetuado | V (+ P) | M | Must | RN-04, RN-10 |
| RF-09 | Falha do serviço principal durante a compra desfaz os passos concluídos e o veículo volta à vitrine | V (+ P) | F | Must | |
| RF-10 | Uma saga interrompida (queda de pod) é retomada automaticamente do ponto em que parou | V | F | Must | |
| RF-11 | Qualquer pessoa consulta uma compra pelo identificador (status, código do pagamento, prazo, CPF mascarado) | V | M | Should | RN-06, RN-21 |
| RF-12 | Qualquer pessoa consulta os dados de um anúncio pelo identificador | V | M | Should | |
| RF-13 | O gestor consulta um veículo e lista os veículos com qualquer status | P | M | Should | |
| RF-14 | As listagens são paginadas (`limite` padrão 20, máximo 100; `deslocamento`) e informam o total | P, V | M | Should | RN-17 |
| RF-15 | Os serviços expõem verificações de vida e de prontidão | P, V | M | Must | |
| RF-16 | Os serviços publicam a documentação OpenAPI em `/docs` | P, V | E | Should | |

### 3.2.1 Observações

- RF-01 e RF-02 terminam no principal (201/200) assim que o veículo é gravado. A publicação na vitrine é assíncrona, pela outbox, e normalmente leva menos de 1 segundo.
- RF-06 e RF-07 são o webhook do enunciado. O processador recebe 200 assim que o principal grava o resultado; o repasse ao serviço de venda é garantido pela outbox.
- RF-09 e RF-10 vêm dos temas da fase (saga e resiliência) e são o que o vídeo mostra como "tratamento de falhas".

## 3.3 Requisitos não funcionais

| ID | Atributo | Requisito | Verificação |
|---|---|---|---|
| RNF-01 | Isolamento | Listagens e compra num serviço próprio, com banco próprio (MongoDB) que nenhum outro serviço acessa | NetworkPolicy do banco; e2e de listagens com o principal escalado para zero réplicas |
| RNF-02 | Consistência | Um veículo nunca tem duas compras ativas, nem com requisições simultâneas | Teste de integração com N compras simultâneas: exatamente 1 resposta 201 |
| RNF-03 | Consistência eventual | Ao fim de toda saga, anúncio, compra, veículo e cobrança estão em estados coerentes | Testes de integração e BDD de cada final (efetivada, pagamento cancelado, expirada, falha) |
| RNF-04 | Resiliência | Chamadas entre serviços com timeout, retry com backoff e jitter, circuit breaker e idempotência | Testes unitários do cliente HTTP; métricas do circuit breaker no Grafana; teste de caos no vídeo |
| RNF-05 | Entrega garantida | Réplica dos anúncios e repasse do resultado do pagamento entregues ao menos uma vez, na ordem por agregado | Outbox na mesma transação; teste de integração com o serviço de venda indisponível |
| RNF-06 | Segurança: borda | Kong como única entrada HTTP; key-auth + ACL para gestor e processador; *rate limiting* mais restrito na compra | e2e: sem credencial → 401 do Kong; excedente → 429 |
| RNF-07 | Segurança: interna | Rotas `/internal/v1` sem rota no Kong, protegidas por NetworkPolicy e token de serviço | e2e: rota interna pelo Kong → 404 |
| RNF-08 | Privacidade / LGPD | CPF validado, criptografado em repouso, mascarado nas respostas e ausente de logs, métricas e traces | Teste que lê o documento cru no MongoDB; inspeção dos logs e dos traces |
| RNF-09 | Disponibilidade | Cada serviço com 2 réplicas, probes, HPA e rollout sem indisponibilidade | `maxUnavailable: 0`; HPA ativo |
| RNF-10 | Desempenho | Listagens com p95 < 300 ms sob carga | Teste de carga k6 nas listagens do serviço de venda |
| RNF-11 | Testabilidade | Cobertura de linhas ≥ 80% por serviço (unit + integração) | `--cov-fail-under=80` no CI e Quality Gate do SonarCloud |
| RNF-12 | Qualidade de código | Análise estática e Quality Gate obrigatórios no PR | SonarCloud + ruff + mypy + import-linter no CI |
| RNF-13 | Contratos | As integrações HTTP entre os serviços são verificadas por testes de contrato nos dois lados | Job de contrato no CI de cada repositório |
| RNF-14 | Observabilidade | Métricas Prometheus, logs JSON correlacionados por `trace_id` e tracing distribuído (OpenTelemetry → Jaeger) | Um trace da compra mostra Kong, venda, principal e bancos; painel de saga no Grafana |
| RNF-15 | Implantabilidade | Cada repositório com CI e CD independentes; toda mudança por PR; deploy no merge na `main`, com e2e ao final | `main` protegida; e2e verde no job summary do CD |
| RNF-16 | Manutenibilidade | Clean Architecture nos dois serviços; domínio sem dependência de framework | import-linter e teste de arquitetura no CI |
| RNF-17 | Interoperabilidade | Contratos em OpenAPI; erros em `application/problem+json` (RFC 9457) | Testes de contrato e de integração |
| RNF-18 | Eficiência de recursos | O ambiente completo cabe num PC de desenvolvimento | Soma dos *limits* de memória dos componentes permanentes ≤ 7 GiB (conta em [08, seção 8.6](08-ci-cd.md#86-recursos)) |
