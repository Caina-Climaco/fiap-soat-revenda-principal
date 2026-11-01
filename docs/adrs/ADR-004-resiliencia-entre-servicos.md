# ADR-004: Resiliência nas chamadas entre serviços

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

Com dois serviços, toda compra depende de chamadas de rede. Sem proteção, um principal lento prende as requisições do serviço de venda até estourar o pool de conexões e derrubar também a vitrine (falha em cascata). Os temas da fase (resiliência, padrões de resiliência, chaos engineering) pedem tratar isso de forma explícita.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Padrões na aplicação: timeout, retry com backoff e jitter, circuit breaker, idempotência** | Fica no código e é testável em unidade; não exige componente extra | Cada serviço implementa o próprio cliente |
| Service mesh (Istio ou Linkerd) com retry e circuit breaking | Política fora do código; mTLS de brinde | Consome muita memória num kind de um nó; aumenta a complexidade do ambiente e do vídeo |
| Nenhuma proteção além do timeout padrão | Simples | Cascata de falhas; compras penduradas |

## Decisão

Um cliente HTTP comum em cada serviço (`infrastructure/http`), sobre httpx, com:

| Padrão | Configuração inicial |
|---|---|
| **Timeout** | 2 s de conexão + leitura por chamada; nenhuma chamada sem timeout |
| **Retry** | Até 3 tentativas, backoff exponencial com jitter (100 ms, 200 ms, 400 ms), só em erros de rede, 502, 503 e 504; nunca em 4xx |
| **Circuit breaker** | Abre após 5 falhas consecutivas para o mesmo destino; meio-aberto após 15 s; estado exposto em métrica |
| **Idempotência** | Toda operação interna é idempotente pela `compra_id` (comandos) ou pela versão (réplica); retry nunca duplica efeito |
| **Fail fast na compra** | Com o circuito aberto, a compra compensa o que já fez e responde 503 com `Retry-After` |
| **Bulkhead** | Pool de conexões limitado por destino, para que o principal lento não esgote o serviço de venda |

O retry da outbox ([ADR-003](ADR-003-http-com-outbox.md)) e o da recuperação de sagas são de longo prazo (minutos), separados desse retry curto da requisição.

## Consequências

### Positivas
- Falhas do principal ficam contidas: a vitrine segue respondendo e a compra falha rápido e limpa.
- O comportamento é observável (métricas de tentativas e do estado do circuito) e demonstrável no vídeo com um teste de caos (derrubar o principal durante uma compra).

### Negativas
- Os parâmetros são empíricos; podem precisar de ajuste.
- Retry só é seguro porque tudo é idempotente; uma operação nova que não seja idempotente quebra a garantia.

## Mitigações
- Teste de contrato e revisão de PR conferem a chave de idempotência de cada rota interna.
- Service mesh fica documentado como evolução (aula de Service Mesh), junto com mTLS entre os serviços.
