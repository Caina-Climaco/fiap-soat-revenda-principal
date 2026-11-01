# infra: plataforma local e infraestrutura do serviço principal

Este repositório é dono da **plataforma comum** ([ADR-006](../docs/adrs/ADR-006-plataforma-compartilhada.md)): o cluster kind, o API Gateway e a observabilidade. O serviço de venda (outro repositório) só cria o namespace dele e consome os contratos abaixo.

```text
infra/
├── kind/cluster.yaml         cluster "revenda" (idêntico nos dois repositórios)
├── terraform/                namespaces, segredos, PostgreSQL, Kong, observabilidade, NetworkPolicies
├── kong/kong.yml.tftpl       rotas, plugins e consumers do Kong (DB-less)
├── observabilidade/          Prometheus (coleta e alertas), Grafana (painéis), OTel Collector
└── runner/                   imagem do runner self-hosted do CD (container no Docker Desktop)
```

## Namespaces e componentes

| Namespace | Componente | Acesso no PC |
|---|---|---|
| `principal` | `revenda-principal` (2 a 5 réplicas), `principal-db` (PostgreSQL 16), Job de migração | banco em `localhost:15432` |
| `gateway` | Kong 3.9 (DB-less) | `http://localhost:8080` |
| `observabilidade` | Prometheus, Grafana, OpenTelemetry Collector, Jaeger | `:9090`, `:3000`, `:16686` |
| `vendas` | serviço de venda e MongoDB (**outro repositório**) | banco em `localhost:27017` |

## Contratos com o repositório de venda

| Contrato | Valor |
|---|---|
| Service do serviço de venda (upstream do Kong, destino da outbox) | `revenda-vendas.vendas.svc.cluster.local:8000` |
| Service do principal (destino da saga) | `revenda-principal.principal.svc.cluster.local:8000` |
| Tokens de serviço | Secret `principal/contrato-tokens-servico`: `VENDAS_PARA_PRINCIPAL`, `PRINCIPAL_PARA_VENDAS` (o Terraform de venda lê por data source) |
| Tracing | `http://otel-collector.observabilidade.svc.cluster.local:4318` (OTLP/HTTP) |
| Métricas | pods com `prometheus.io/scrape: "true"` e rótulo `app=revenda-vendas` no namespace `vendas` |
| Entrada no principal | NetworkPolicy aceita pods `app in (revenda-vendas, revenda-vendas-recuperacao)` do namespace `vendas` |

## Segredos

Gerados pelo Terraform (`random_password`) e guardados só no state local (`%USERPROFILE%\.revenda\revenda-principal.tfstate`, fora do repositório) e nos Secrets do cluster. Para a demonstração: `scripts/windows/segredos.ps1`.

## Verificação manual

```bash
kubectl get pods -A -l app.kubernetes.io/part-of=revenda-veiculos
# NetworkPolicy: um pod qualquer não alcança o principal nem o banco
kubectl run teste --rm -it --image=curlimages/curl:8.17.0 --restart=Never -- \
  curl -sS -m 5 http://revenda-principal.principal.svc.cluster.local:8000/health/live   # timeout
```
