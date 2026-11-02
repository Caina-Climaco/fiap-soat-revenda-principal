# 12. Observabilidade

Três sinais, com uma chave em comum para ir de um ao outro ([ADR-007](adrs/ADR-007-tracing-distribuido-opentelemetry.md)):

| Sinal | Onde | Chave de correlação |
|---|---|---|
| Logs JSON | `kubectl logs` (stdout dos pods) | `request_id` (header `X-Request-ID`, gerado no Kong) e `trace_id` |
| Métricas | Prometheus (`localhost:9090`) e Grafana (`localhost:3000`) | rótulos `job`, `rota`, `passo`, `motivo` |
| Traces | Jaeger (`localhost:16686`) | `trace_id` (header W3C `traceparent`) |

## 12.1 Tracing distribuído

O trace nasce no Kong (plugin `opentelemetry`) e segue pelo `traceparent` para o serviço de venda, dele para o principal (bloqueio e cobrança) e para os bancos. O contexto do trace é **gravado na linha da outbox**: quando o relay entrega o resultado do pagamento ao serviço de venda, o span continua o trace do webhook. Assim uma compra aparece inteira no Jaeger, inclusive as compensações quando o principal falha.

O Collector remove atributos com dado pessoal ou credencial (CPF, corpo da requisição, `apikey`, `X-Webhook-Secret`, `X-Service-Token`) antes de enviar ao Jaeger, como segunda barreira (os serviços já não os registram).

Os traces também são consultáveis no Grafana (Explore, fonte **Jaeger**). Essa fonte nativa do Grafana usa a API HTTP v1 de busca do Jaeger (`/api/services`, `/api/traces`), que o Jaeger 2.21 removeu ([jaegertracing/jaeger#9260](https://github.com/jaegertracing/jaeger/pull/9260)); por isso o Jaeger está fixado em 2.20.0. O CD do principal verifica o health check da fonte pelo Grafana a cada deploy, então uma atualização futura que a quebre falha o pipeline em vez de passar despercebida.

## 12.2 Métricas

| Métrica | Serviço | Uso |
|---|---|---|
| `revenda_http_requisicoes_total`, `revenda_http_requisicao_duracao_segundos` | ambos | taxa, erros e latência por rota |
| `revenda_veiculos_cadastrados_total`, `revenda_veiculos_editados_total` | principal | negócio |
| `revenda_principal_passos_saga_total{passo}` | principal | bloqueio, desbloqueio, venda |
| `revenda_cobrancas_total{evento,origem}` | principal | gerada, paga, cancelada, pagamento após cancelamento |
| `revenda_outbox_pendentes`, `revenda_outbox_entregas_total{tipo,resultado}`, `revenda_outbox_atraso_segundos` | principal | saúde da outbox |
| `revenda_compras_*_total` | venda | iniciadas, aguardando pagamento, efetivadas, canceladas por motivo |
| `revenda_saga_passos_total{passo,resultado}` | venda | T1 a T6 e C1 a C3 |
| `revenda_chamadas_participante_total{destino,resultado}` | venda | sucesso, recusa, nova tentativa, falha, circuito aberto |
| `revenda_circuito_estado{destino}` | venda | 0 fechado, 1 meio-aberto, 2 aberto |
| `revenda_sagas_paradas` | venda | compras com trabalho pendente e sem progresso há mais de 5 minutos (calculado a cada coleta) |

## 12.3 Painéis

- **Revenda de Veículos: visão geral**: contadores de negócio, tráfego e erros por serviço, latência p95, réplicas e o Kong.
- **Revenda de Veículos: saga e resiliência**: funil da compra, passos e compensações, cancelamentos por motivo, estado do circuit breaker, chamadas ao principal por resultado e a outbox.

## 12.4 Alertas

Avaliados pelo Prometheus (`infra/observabilidade/alertas.yml`, com testes em `alertas.test.yml` rodados no CI):

| Alerta | Condição |
|---|---|
| `PrincipalFora`, `VendasFora`, `KongFora` | sem coleta por 2 minutos |
| `Erros5xxAltos` | mais de 1% de 5xx em um serviço por 5 minutos |
| `ListagensLentas` | p95 das listagens acima de 300 ms por 10 minutos |
| `WebhookRecusado` | 401 frequente no webhook (tentativa de forjar pagamento) |
| `CircuitoAbertoParaPrincipal` | circuito aberto por mais de 1 minuto |
| `ComprasCanceladasPorFalha` | mais de 3 compras desfeitas por falha em 15 minutos |
| `OutboxAcumulando` | mais de 50 mensagens pendentes por 5 minutos |
| `OutboxComFalhaDefinitiva` | mensagem marcada como `FALHOU` (recusa definitiva ou prazo de entrega vencido); reprocessar com `python -m principal.outbox reprocessar` |
| `SagasParadas` | compra com passo ou compensação pendente sem progresso há mais de 5 minutos, por 5 minutos (`revenda_sagas_paradas`, calculado pela recuperação de sagas) |
| `PagamentoAposCancelamento` | pagamento confirmado para cobrança cancelada (estorno manual) |
| `KongRejeicoesNaBorda` | muitas respostas 401, 403 ou 429 no gateway |
