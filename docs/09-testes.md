# 9. Testes

## 9.1 Pirâmide de testes

| Nível | Principal | Venda | O que cobre | Onde roda |
|---|---|---|---|---|
| Unidade | 149 | 71 | Domínio (Veiculo, Cobranca, Compra, CPF), casos de uso com dublês, relay, cliente resiliente, circuit breaker, cifra, configuração, arquitetura | CI (`testes`) |
| Integração | 82 | 76 | API real com o banco real (PostgreSQL 16 / MongoDB 7): rotas, erros RFC 9457, outbox, concorrência, saga com o principal simulado, recuperação, logs sem CPF | CI (`testes`) |
| BDD | | 4 | Só no serviço de venda: o fluxo da compra em Gherkin (pytest-bdd) | CI (`testes`) |
| Contrato | 6 | 4 | Rotas internas nos dois lados (seção 9.4) | CI (`contrato`) |
| Ponta a ponta | 24 | 14 | Ambiente implantado, sempre pelo Kong | CD |
| Carga | | 1 script | p95 das listagens (RNF-10) | CD do serviço de venda, depois do e2e; e sob demanda (`carga-listagens.ps1`) |

Cobertura de linhas e ramos: **100%** nos dois serviços (mínimo exigido de 80%, `--cov-fail-under=80`), enviada ao SonarCloud e exibida no resumo de cada execução do CI. As poucas linhas excluídas com `# pragma: no cover` são ramos defensivos que o próprio desenho torna inalcançáveis, cada um com o motivo no comentário: no principal, a checagem do tipo da exceção nos handlers registrados (`errors.py`), a falha depois de a resposta já ter começado (`middleware.py`) e a cobrança que a restrição de unicidade garante existir (`pagamentos/application/casos_uso.py`); no serviço de venda, os mesmos casos de `errors.py` e `middleware.py`, as verificações de exaustividade (`assert_never`) da saga e releituras de compras que nunca são apagadas. Os pontos de entrada (`__main__`) do Job e do CronJob também ficam de fora.

## 9.2 Ferramentas

| Ferramenta | Uso |
|---|---|
| pytest, pytest-cov | Execução e cobertura; marcadores `unit`, `integration`, `contrato`, `e2e` nos dois serviços e `bdd` só no serviço de venda |
| pytest-bdd | Cenários em Gherkin, em português (só no serviço de venda) |
| httpx `MockTransport` | O principal simulado no serviço de venda: a saga passa pelo cliente HTTP real, com retry e circuit breaker, sem rede |
| jsonschema | Validação das requisições e respostas contra os contratos OpenAPI |
| mongomock | MongoDB simulado para rodar os testes sem banco (o CI usa o MongoDB real) |
| k6 | Teste de carga das listagens |
| ruff, mypy (strict), import-linter | Estilo, tipos e regras de arquitetura (job `qualidade`) |
| SonarCloud | Quality Gate (job `sonarcloud`) |

## 9.3 Como executar

```bash
# Principal (PostgreSQL 16 acessível)
export TEST_DATABASE_URL=postgresql+psycopg://revenda:revenda@localhost:5432/revenda_principal_test
uv run pytest -m "unit or integration" --cov=principal --cov-branch --cov-fail-under=80
uv run pytest -m contrato

# Venda (sem TEST_MONGO_URL, o MongoDB é simulado)
uv run pytest -m "unit or integration or bdd" --cov=vendas --cov-branch --cov-fail-under=80
uv run pytest -m contrato

# Ponta a ponta contra o ambiente no kind (chaves com scripts/windows/segredos.ps1)
export E2E_GESTOR_APIKEY=... E2E_GATEWAY_PAGAMENTO_APIKEY=... E2E_WEBHOOK_SECRET=...
pytest tests/e2e -m e2e -p no:cacheprovider -o addopts=""
```

## 9.4 Testes de contrato

Cada serviço publica o recorte `/internal/v1` da sua OpenAPI em `contratos/` e guarda a cópia do contrato do outro ([ADR-008](adrs/ADR-008-qualidade-sonarcloud-contrato-bdd.md), [contratos/README.md](../contratos/README.md)).

| Lado | Verificação |
|---|---|
| Provedor | O contrato publicado é idêntico ao gerado da aplicação; as respostas reais (sucesso, repetição idempotente, 404, 409, 401) cumprem o esquema |
| Consumidor (venda) | Toda chamada da saga ao principal e toda resposta do principal simulado cumprem o contrato do principal |
| Consumidor (principal) | Toda mensagem da outbox (réplica do anúncio, resultado do pagamento) cumpre o contrato do serviço de venda |
| Sincronia | A cópia local é igual à publicada na `main` do outro repositório |

Na primeira execução, os testes de contrato acharam duas divergências reais: o principal simulado respondia sem campos que o real devolve, e a resposta 200 da cobrança repetida não estava documentada.

## 9.5 Cenários BDD

O fluxo da compra está em `tests/bdd/features/compra.feature` (serviço de venda), executado no CI com o principal simulado. Os mesmos cenários são verificados de ponta a ponta no CD contra os dois serviços reais; a expiração, cujo prazo é de 30 minutos, é provada adiantando as datas no MongoDB e executando a recuperação de verdade (`test_e2e_recuperacao`).

```gherkin
# language: pt
Funcionalidade: Compra de veículo coordenada pela saga

  Contexto:
    Dado um veículo à venda por "119900.00" na vitrine

  Cenário: Caminho feliz, do pedido à efetivação
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    Então a compra fica aguardando pagamento com um código de pagamento
    E o veículo sai da vitrine e fica bloqueado no catálogo
    Quando o processador informa que o pagamento foi efetuado
    Então a compra é efetivada com a data da venda
    E o veículo aparece entre os vendidos por "119900.00"
    E o catálogo registra o veículo como vendido

  Cenário: Pagamento cancelado pelo processador
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    E o processador informa que o pagamento foi cancelado
    Então a compra é cancelada com o motivo "PAGAMENTO_CANCELADO"
    E o veículo volta à vitrine e é desbloqueado no catálogo

  Cenário: Reserva expirada sem pagamento
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    E passam 31 minutos sem resultado do pagamento
    E a recuperação de sagas é executada
    Então a compra é cancelada com o motivo "RESERVA_EXPIRADA"
    E a cobrança é cancelada no principal
    E o veículo volta à vitrine e é desbloqueado no catálogo

  Cenário: Serviço principal fora do ar durante a compra
    Dado que o serviço principal está fora do ar
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    Então a resposta é 503 com o problema "servico-principal-indisponivel"
    E o veículo continua na vitrine
```

## 9.6 Testes ponta a ponta (CD)

| Repositório | Módulo | Cenários |
|---|---|---|
| principal | `test_e2e_gateway` | Sem chave, chave inválida, consumer errado, header de consumer forjado, rotas internas e `/metrics` fechadas, Swagger, `X-Request-ID` |
| principal | `test_e2e_catalogo` | Cadastro, edição com nova versão, 422 em problem+json, listagem paginada, webhook exige chave e segredo |
| principal | `test_e2e_integracao` | Réplica do anúncio, compra paga nos dois serviços, compra cancelada, versão antiga recusada (só com o serviço de venda no ar) |
| principal | `test_e2e_tracing` | Kong, principal e venda no Jaeger; um trace com spans do Kong e do principal |
| venda | `test_e2e_vitrine` | Anúncio replicado, ordenação por preço, vendidos sem dados do comprador, rate limiting, rotas internas fechadas |
| venda | `test_e2e_compra` | Compra paga, pagamento cancelado, segunda compra recusada, CPF inválido, veículo inexistente |
| venda | `test_e2e_recuperacao` | **Recuperação real**: reserva vencida desfeita pelo CronJob (compra cancelada, veículo de volta à vitrine e desbloqueado, webhook tardio sem efeito) e CPF de compra cancelada apagado depois da retenção |
| venda | `test_e2e_caos` | **Falha do principal**: escala o principal para 0, a vitrine continua respondendo, a compra é desfeita com 503 e `Retry-After`, o circuito abre; com o principal de volta, a compra é aceita |

O CD falha se faltar qualquer variável do e2e (nunca pula em silêncio) e mostra o resultado no resumo da execução. Os CDs dos dois repositórios usam uma trava comum: o teste de falha derruba o principal, então um CD nunca roda junto com o outro.

## 9.7 Testes de requisitos não funcionais

| RNF | Teste |
|---|---|
| RNF-02 (uma compra ativa por veículo) | 10 compras simultâneas do mesmo veículo contra o MongoDB real: exatamente uma aceita |
| RNF-04 (resiliência) | Cliente resiliente em unidade (retry, jitter, circuit breaker); saga com o principal fora; uma tentativa de compensação por requisição com chamadas lentas |
| RNF-05 (entrega garantida) | Relay com duas réplicas disputando a outbox; ordem por agregado; backoff e `FALHOU` |
| RNF-08 (CPF) | Documento cru no MongoDB sem o CPF em claro; logs sem CPF; resposta mascarada; no CD, o CPF cifrado apagado pela recuperação depois da retenção |
| RNF-10 (desempenho) | k6 a cada deploy do serviço de venda, 8 requisições por segundo por 1 minuto, limites p95 < 300 ms e falhas < 1% |
| RNF-16 (arquitetura) | import-linter e teste de arquitetura |
