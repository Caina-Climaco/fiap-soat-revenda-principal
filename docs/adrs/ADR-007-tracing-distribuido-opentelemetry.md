# ADR-007: Tracing distribuído com OpenTelemetry e Jaeger

**Status:** Aceito
**Data:** 2026-11-01
**Complementa:** ADR-016 da Fase 3 (Prometheus e Grafana)

## Contexto

Na Fase 3, métricas e logs bastavam: uma requisição vivia num só processo. Na Fase 4, uma compra atravessa Kong, serviço de venda, serviço principal, MongoDB e PostgreSQL, e o pagamento volta por outro caminho (webhook, outbox, repasse). O Tech Challenge oficial pede "monitoramento e rastreamento dos fluxos distribuídos". Na Fase 3 o tracing ficou registrado como evolução.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **OpenTelemetry (SDK + instrumentação automática) → Collector → Jaeger** | Padrão aberto e neutro de fornecedor; instrumentação automática de FastAPI, httpx, PyMongo, SQLAlchemy e Kong; Jaeger roda local | Mais dois componentes no cluster |
| APM SaaS (Datadog, New Relic) | Painéis prontos | Exige conta e envio de dados para fora; sem custo zero |
| Só `X-Request-ID` nos logs | Nenhum componente novo | Não mostra a árvore de chamadas nem o tempo de cada trecho |

## Decisão

- Os dois serviços usam o **SDK do OpenTelemetry** com instrumentação de FastAPI, httpx, PyMongo e SQLAlchemy, exportando OTLP para o **OpenTelemetry Collector** no namespace `observabilidade`.
- O **Kong** usa o plugin `opentelemetry`, para o trace começar na borda.
- O Collector envia os traces ao **Jaeger** (all-in-one, armazenamento em memória), com interface em `http://localhost:16686`.
- O contexto é propagado pelo header W3C `traceparent` nas chamadas HTTP e **guardado na linha da outbox**, para que o repasse do pagamento continue o mesmo trace do webhook.
- Os logs JSON passam a incluir `trace_id` e `span_id`. As métricas da Fase 3 continuam no Prometheus, com painéis novos de saga e de resiliência.
- Nenhum atributo de span carrega CPF ou outro dado pessoal; o processador de atributos do Collector remove chaves proibidas como segunda barreira.

## Consequências

### Positivas
- Um trace mostra a compra inteira, incluindo as compensações quando há falha; é a evidência visual mais forte para o vídeo.
- Do log de um erro se chega ao trace correspondente pelo `trace_id`.

### Negativas
- Mais memória (Collector e Jaeger).
- O Jaeger em memória perde os traces quando o pod reinicia.

## Mitigações
- *Limits* de memória definidos e somados na conta do ambiente (RNF-18).
- Amostragem de 100% no ambiente local; em produção, amostragem por cauda no Collector e armazenamento persistente ficam como evolução.
