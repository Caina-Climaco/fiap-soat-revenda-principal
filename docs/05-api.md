# 5. Contratos da API

Este documento descreve os contratos HTTP dos dois serviços: as rotas públicas (pelo Kong) e as rotas internas (entre os serviços). A especificação OpenAPI gerada por cada serviço (`/openapi.json`) é a fonte executável; o recorte interno de cada uma fica versionado em `contratos/` e é verificado pelos testes de contrato ([ADR-008](adrs/ADR-008-qualidade-sonarcloud-contrato-bdd.md)).

## 5.1 Convenções

| Tema | Regra |
|---|---|
| Prefixos | Públicas: `/api/v1`. Internas: `/internal/v1` (sem rota no Kong). Saúde: `/health/live`, `/health/ready` |
| Formato | JSON em UTF-8; datas em ISO 8601 com fuso (UTC); dinheiro como string decimal com 2 casas (`"89900.00"`) |
| Identificadores | UUID v4 |
| Erros | `application/problem+json` (RFC 9457) com `type`, `title`, `status`, `detail`, `instance` e, quando houver, `erros` (lista de campos inválidos) |
| Paginação | `limite` (padrão 20, máximo 100) e `deslocamento` (padrão 0); resposta com `itens`, `total`, `limite`, `deslocamento` |
| Correlação | `X-Request-ID` (gerado pelo Kong se ausente) e `traceparent` (W3C), propagados em todas as chamadas |
| Validação | Campos desconhecidos são recusados (422); mensagens em português |

## 5.2 Autenticação e autorização

| Chamador | Credencial | Onde é verificada |
|---|---|---|
| Gestor | Header `apikey` (consumer `gestor`) | Kong (key-auth + ACL); o principal exige `X-Consumer-Username: gestor`, que só chega pelo Kong |
| Processador de pagamentos | Header `apikey` (consumer `gateway-pagamento`) + `X-Webhook-Secret` | Kong (key-auth + ACL) e principal (segredo em tempo constante) |
| Comprador e visitante | Nenhuma | *Rate limiting* no Kong |
| Outro serviço | Header `X-Service-Token` (um segredo por direção) | Serviço chamado; a NetworkPolicy só deixa o outro serviço alcançar as rotas internas |

## 5.3 Roteamento no Kong

O Kong escolhe a rota de prefixo mais longo; por isso as rotas do serviço de venda dentro de `/api/v1/veiculos` vencem a rota do gestor.

| Rota Kong | Caminho | Métodos | Serviço | Proteção |
|---|---|---|---|---|
| `vitrine` | `/api/v1/veiculos/a-venda`, `/api/v1/veiculos/vendidos`, `/api/v1/anuncios` | GET | venda | *Rate limiting* 600/min |
| `compras` | `/api/v1/compras` | POST, GET | venda | *Rate limiting* 60/min no POST |
| `veiculos-gestor` | `/api/v1/veiculos` | GET, POST, PATCH | principal | key-auth + ACL `gestor` |
| `webhook-pagamento` | `/api/v1/pagamentos/webhook` | POST | principal | key-auth + ACL `gateway-pagamento` |
| `documentacao-*` | `/principal/docs`, `/vendas/docs` e respectivos `openapi.json` | GET | ambos | |

Em todas as rotas: `X-Request-ID`, plugin `opentelemetry` e payload máximo de 1 MB. `/internal/v1` e `/metrics` não têm rota (404 do Kong).

## 5.4 Serviço principal: rotas públicas

### 5.4.1 `POST /api/v1/veiculos` (gestor)

```json
{ "marca": "Toyota", "modelo": "Corolla XEi", "ano": 2022, "cor": "Prata", "preco": "119900.00" }
```

**201** com `Location` e o veículo:

```json
{
  "id": "5f0c...", "marca": "Toyota", "modelo": "Corolla XEi", "ano": 2022, "cor": "Prata",
  "preco": "119900.00", "status": "A_VENDA", "versao": 1,
  "criado_em": "2026-11-04T13:00:00Z", "atualizado_em": "2026-11-04T13:00:00Z"
}
```

Erros: 401/403 (Kong), 422 (`dados-invalidos`, RN-15). Efeito colateral: anúncio publicado na outbox.

### 5.4.2 `PATCH /api/v1/veiculos/{id}` (gestor)

Corpo parcial com qualquer subconjunto dos cinco campos. **200** com o veículo e `versao` incrementada. Erros: 404 (`veiculo-nao-encontrado`), 409 (`veiculo-nao-editavel`: `BLOQUEADO` ou `VENDIDO`, RN-02), 422.

### 5.4.3 `GET /api/v1/veiculos/{id}` e `GET /api/v1/veiculos?status=` (gestor)

Consulta e listagem com qualquer status, para a loja. Listagem paginada, ordenada por `criado_em` decrescente.

### 5.4.4 `POST /api/v1/pagamentos/webhook` (processador de pagamentos)

```json
{ "codigo_pagamento": "PAG-3f9a1c2b7d4e", "status": "PAGO" }
```

`status`: `PAGO` (pagamento efetuado) ou `CANCELADO`.

| Situação | Resposta |
|---|---|
| Primeira notificação para cobrança `PENDENTE` | **200** `{"codigo_pagamento": "...", "status_cobranca": "PAGA", "efeito": "APLICADO"}` |
| Repetição do mesmo resultado | **200** com `"efeito": "SEM_EFEITO"` |
| `PAGO` para cobrança `CANCELADA` | **409** `pagamento-apos-cancelamento` (registrado para estorno) |
| `CANCELADO` para cobrança `PAGA` | **409** `cobranca-ja-paga` |
| Código inexistente | **404** `cobranca-nao-encontrada` |
| Segredo ausente ou inválido | **401** sem alteração de estado |

Efeito colateral: resultado repassado ao serviço de venda pela outbox.

## 5.5 Serviço de venda: rotas públicas

### 5.5.1 `GET /api/v1/veiculos/a-venda`

Anúncios `A_VENDA`, ordenados por `preco` ascendente, depois `criado_em` e `veiculo_id` (RN-17).

```json
{
  "itens": [
    { "veiculo_id": "5f0c...", "marca": "Toyota", "modelo": "Corolla XEi", "ano": 2022,
      "cor": "Prata", "preco": "119900.00", "versao": 1 }
  ],
  "total": 1, "limite": 20, "deslocamento": 0
}
```

A `versao` de cada item é a que o comprador envia na compra.

### 5.5.2 `GET /api/v1/veiculos/vendidos`

Anúncios `VENDIDO`, ordenados pelo preço da venda ascendente (RN-20). Cada item traz `preco_venda` e `data_venda`; nunca o CPF.

### 5.5.3 `GET /api/v1/anuncios/{veiculo_id}`

Um anúncio com `status` (`A_VENDA`, `RESERVADO`, `VENDIDO`). **404** `anuncio-nao-encontrado`.

### 5.5.4 `POST /api/v1/compras`

```json
{ "veiculo_id": "5f0c...", "versao": 1, "cpf": "529.982.247-25" }
```

O CPF é aceito com ou sem pontuação.

**201** com `Location`:

```json
{
  "id": "a71e...", "veiculo_id": "5f0c...",
  "veiculo": { "marca": "Toyota", "modelo": "Corolla XEi", "ano": 2022, "cor": "Prata" },
  "preco": "119900.00", "cpf": "***.982.247-**",
  "status": "AGUARDANDO_PAGAMENTO", "codigo_pagamento": "PAG-3f9a1c2b7d4e",
  "criada_em": "2026-11-04T13:05:00Z", "expira_em": "2026-11-04T13:35:00Z",
  "data_venda": null, "motivo_cancelamento": null
}
```

| Erro | Quando |
|---|---|
| **404** `anuncio-nao-encontrado` | Veículo não está na vitrine |
| **409** `veiculo-indisponivel` | Anúncio `RESERVADO` ou `VENDIDO`, ou outra compra venceu a disputa (RN-03) |
| **409** `anuncio-alterado` | A versão enviada não é a atual; nada fica reservado (RN-12) |
| **422** `cpf-invalido` | Formato ou dígitos verificadores inválidos (RN-05) |
| **503** `servico-principal-indisponivel` | Falha em T2 ou T3; compensado; com `Retry-After` |

### 5.5.5 `GET /api/v1/compras/{id}`

A compra no formato acima, com o estado atual (`PROCESSANDO`, `AGUARDANDO_PAGAMENTO`, `EFETIVADA`, `CANCELADA`), `data_venda` quando efetivada e `motivo_cancelamento` quando cancelada. **404** `compra-nao-encontrada`. Não existe busca por CPF (RN-21).

## 5.6 Rotas internas do serviço principal (chamadas pela saga)

Todas exigem `X-Service-Token` e são **idempotentes pela `compra_id`**: repetir a mesma chamada devolve o mesmo resultado, sem novo efeito.

| Rota | Passo | Corpo | Sucesso | Erros |
|---|---|---|---|---|
| `POST /internal/v1/veiculos/{id}/bloqueio` | T2 | `{compra_id, versao}` | 200 `{status: "BLOQUEADO"}` | 404; 409 `versao-divergente`; 409 `veiculo-indisponivel` (bloqueado por outra compra ou vendido); 409 `compra-compensada` |
| `DELETE /internal/v1/veiculos/{id}/bloqueio/{compra_id}` | C2 | | 200 (também sem efeito, se não havia bloqueio dessa compra) | 409 `veiculo-ja-vendido` |
| `POST /internal/v1/veiculos/{id}/venda` | T6 | `{compra_id}` | 200 `{status: "VENDIDO"}` | 409 `bloqueio-de-outra-compra` |
| `POST /internal/v1/cobrancas` | T3 | `{compra_id, valor}` | 201 `{codigo_pagamento, status: "PENDENTE"}`; 200 com a mesma cobrança se repetido | 409 `compra-compensada` |
| `POST /internal/v1/cobrancas/{compra_id}/cancelamento` | C3 | | 200 `{status: "CANCELADA"}` (também sem efeito, se já cancelada) | 409 `cobranca-ja-paga` (o pivô aconteceu) |

**Regra contra ressurreição.** Uma chamada de T2 ou T3 pode chegar depois da compensação correspondente: por exemplo, a primeira tentativa estourou o timeout no cliente, a saga compensou, e só então a requisição original foi processada. Para isso não "ressuscitar" um bloqueio ou uma cobrança, toda compensação grava a `compra_id` como compensada. A partir daí, T2 e T3 para essa compra respondem 409 `compra-compensada`. O cancelamento de uma cobrança que ainda não existe também grava essa marca.

## 5.7 Rotas internas do serviço de venda (chamadas pela outbox do principal)

| Rota | Corpo | Sucesso | Observação |
|---|---|---|---|
| `PUT /internal/v1/anuncios/{veiculo_id}` | `{versao, marca, modelo, ano, cor, preco, criado_em}` | 200 `{"aplicado": true}` ou `{"aplicado": false}` (versão antiga ou repetida) | Nunca altera a disponibilidade ([ADR-003 do serviço de venda](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-003-replica-de-leitura-dos-anuncios.md)) |
| `POST /internal/v1/compras/{compra_id}/resultado-pagamento` | `{codigo_pagamento, status: "PAGO" \| "CANCELADO"}` | 200 assim que o resultado é registrado na saga; o avanço ou a compensação seguem na mesma requisição, e o que faltar fica para a recuperação | Repetição: 200 sem efeito. Compra desconhecida: 404, e a outbox tenta de novo |

O resultado é endereçado pela `compra_id` (que o principal conhece pela cobrança), não pelo código do pagamento. Assim ele é aceito mesmo que, numa corrida extrema, o webhook chegue antes de o serviço de venda gravar o código recebido em T3.

## 5.8 Catálogo de erros

| `type` | Status | Serviço |
|---|---|---|
| `dados-invalidos` | 422 | ambos |
| `cpf-invalido` | 422 | venda |
| `veiculo-nao-encontrado` | 404 | principal |
| `veiculo-nao-editavel` | 409 | principal |
| `versao-divergente` | 409 | principal (interno) |
| `veiculo-indisponivel` | 409 | ambos |
| `veiculo-ja-vendido` | 409 | principal (interno) |
| `bloqueio-de-outra-compra` | 409 | principal (interno) |
| `compra-compensada` | 409 | principal (interno) |
| `cobranca-nao-encontrada` | 404 | principal |
| `cobranca-ja-paga` | 409 | principal |
| `pagamento-apos-cancelamento` | 409 | principal |
| `anuncio-nao-encontrado` | 404 | venda |
| `anuncio-alterado` | 409 | venda |
| `compra-nao-encontrada` | 404 | venda |
| `servico-principal-indisponivel` | 503 | venda |
| `nao-autorizado` | 401 | ambos |

Os `type` são URIs relativas (`/problemas/<nome>`), documentadas na OpenAPI de cada serviço.
