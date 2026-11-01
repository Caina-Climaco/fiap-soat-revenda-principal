# ADR-009: Pagamentos no serviço principal, com a cobrança como árbitro do pivô

**Status:** Aceito
**Data:** 2026-11-01
**Atualiza:** ADR-007 da Fase 3 (pagamento simulado por webhook)

## Contexto

O enunciado pede "um endpoint (webhook) para que a entidade que processa o pagamento consiga, a partir do código do pagamento, informar se o pagamento foi efetuado ou cancelado". O webhook não está entre os endpoints que precisam ficar isolados no serviço de venda (listagem e compras). Além disso, existe uma corrida inevitável: o pagamento pode ser efetuado no mesmo instante em que a reserva expira. Algum componente precisa decidir quem ganhou, uma única vez.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Contexto Pagamentos no principal: gera a cobrança, recebe o webhook e decide a corrida pela transição condicional da cobrança** | Um único dono do código do pagamento e do resultado; decisão atômica em PostgreSQL; serviço de venda sem exposição a parceiro externo | Mais uma ida e volta HTTP no fluxo de pagamento |
| Webhook no serviço de venda | Menos uma chamada | A decisão ficaria no MongoDB junto com a saga; parceiro externo acessando o serviço isolado |
| Código do pagamento gerado pelo serviço de venda | Menos uma chamada na compra | Pagamentos deixaria de ser dono da própria cobrança |

## Decisão

- Pagamentos gera a cobrança (`PAG-` + 12 hexadecimais aleatórios), uma por `compra_id` (idempotente).
- O webhook `POST /api/v1/pagamentos/webhook` recebe `{"codigo_pagamento": "...", "status": "PAGO" | "CANCELADO"}`.
  - Uma camada anticorrupção traduz esse payload em comandos do domínio.
  - O segredo `X-Webhook-Secret` é validado em tempo constante.
- A saída de `PENDENTE` é um `UPDATE` condicional (`WHERE status = 'PENDENTE'`). Pagamento (webhook) e cancelamento (expiração, pela saga) disputam essa linha, e só um vence.
- O resultado é repassado ao serviço de venda pela outbox ([ADR-003](ADR-003-http-com-outbox.md)).
- Respostas do webhook:

| Situação | Resposta |
|---|---|
| Primeira notificação | 200 |
| Repetição do mesmo resultado | 200 sem efeito |
| `PAGO` para cobrança já cancelada | 409, registrado para estorno |
| Código inexistente | 404 |

## Consequências

### Positivas
- O pivô da saga é uma transação local e atômica, fácil de testar com concorrência real.
- O serviço de venda nunca fala com o mundo externo, só com o principal e com o comprador.

### Negativas
- O resultado do pagamento chega ao serviço de venda com atraso (normalmente menor que 1 s, maior se o serviço de venda estiver fora).

## Mitigações
- A consulta da compra mostra `AGUARDANDO_PAGAMENTO` até o repasse chegar; o atraso fica visível no trace.
- Teste de integração com webhook e cancelamento simultâneos confirma exatamente um vencedor.
