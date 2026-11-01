# ADR-003: Comunicação HTTP entre serviços, com outbox transacional

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

O enunciado determina que "a comunicação entre o serviço de venda de veículos e o software principal deve ser feita via requisições http". Há dois tipos de interação:

1. **Comandos da saga** (bloquear veículo, gerar cobrança, cancelar cobrança, marcar vendido): o serviço de venda precisa da resposta para decidir o próximo passo.
2. **Notificações do principal** (novo anúncio ou nova versão; resultado do pagamento): o principal precisa garantir que a informação chegue, mesmo que o serviço de venda esteja fora do ar naquele momento.

No segundo caso, gravar no banco e depois chamar HTTP abre a janela do *dual write*: o banco confirma, o processo cai antes da chamada, e a notificação se perde. Um broker (RabbitMQ, Kafka) resolveria isso, mas contraria o enunciado.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **HTTP síncrono para comandos + outbox transacional com entrega HTTP para notificações** | Atende o enunciado; garante entrega ao menos uma vez sem broker; padrão visto na fase | Exige um entregador (*relay*) e idempotência no receptor |
| HTTP direto após o commit | Simples | Perde mensagens em falha; sem garantia de ordem |
| Broker (RabbitMQ) | Desacoplamento temporal nativo | Contraria o enunciado; mais um componente no cluster |
| Serviço de venda consultando o principal periodicamente (*polling*) | Sem relay no principal | Atraso na vitrine; carga desnecessária; o isolamento piora |

## Decisão

- **Comandos da saga**: HTTP síncrono do serviço de venda para o principal, em rotas `/internal/v1`, com a `compra_id` como chave de idempotência em todas as operações.
- **Notificações**: o principal grava a mudança de negócio e uma linha na tabela `mensageria.outbox` **na mesma transação**. Um *relay* em segundo plano no próprio principal lê as linhas pendentes em ordem e as entrega por HTTP ao serviço de venda, com retry e backoff, marcando como entregues só após resposta 2xx.
- **Ordem por agregado**: o relay entrega as mensagens de um mesmo `veiculo_id` (ou da mesma cobrança) em ordem; mensagens de agregados diferentes podem seguir em paralelo.
- **Idempotência no receptor**: a réplica de anúncio só aplica versão maior que a atual; o resultado do pagamento é ignorado se a compra já estiver no estado final correspondente.
- Mensagens que esgotam as tentativas ficam com status `FALHOU`, contam numa métrica e disparam alerta; podem ser reprocessadas.

## Consequências

### Positivas
- Nenhuma notificação se perde por queda de processo entre o commit e a chamada.
- A solução continua usando só HTTP, como o enunciado manda.
- O comportamento é demonstrável: com o serviço de venda parado, a outbox acumula; ao voltar, a vitrine se atualiza sozinha.

### Negativas
- A entrega é ao menos uma vez: o receptor precisa ser idempotente.
- O relay adiciona um laço em segundo plano ao principal, que precisa de cuidado com várias réplicas.

## Mitigações
- Com 2 réplicas do principal, o relay usa `SELECT ... FOR UPDATE SKIP LOCKED` para que duas réplicas nunca entreguem a mesma linha ao mesmo tempo.
- Testes de integração do relay com o receptor fora do ar, com respostas 5xx e com mensagens repetidas.
