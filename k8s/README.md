# k8s: manifestos do serviço principal (kustomize)

```text
k8s/
├── base/      ConfigMap revenda-principal-config, Deployment revenda-principal, Service ClusterIP 8000, HPA 2..5
└── migracao/  Job principal-migracao (python -m principal.migracao)
```

Tudo no namespace `principal`. A plataforma (namespaces, Secrets, `principal-db`, metrics-server, NetworkPolicies, Kong, Prometheus, Grafana, OpenTelemetry Collector e Jaeger) vem do Terraform em `infra/terraform`; estes manifestos só **referenciam** os Secrets:

| Secret | Chaves |
|---|---|
| `principal-db-credentials` | `DB_USER`, `DB_PASSWORD`, `DB_NAME` |
| `principal-webhook` | `WEBHOOK_SECRET` |
| `principal-tokens-servico` | `TOKEN_SERVICO_ENTRADA` (venda para principal), `TOKEN_SERVICO_SAIDA` (principal para venda) |

O `base/configmap.yaml` guarda os endereços publicados como contrato: `VENDAS_URL` (`http://revenda-vendas.vendas.svc.cluster.local:8000`) e `OTEL_EXPORTER_OTLP_ENDPOINT` (Collector).

## Contrato com o CD (`.github/workflows/cd.yml`)

| Item | Valor |
|---|---|
| Imagem no repositório | `revenda-principal:dev` (tag neutra), `imagePullPolicy: IfNotPresent` |
| Troca da tag | O CD renderiza com `kubectl kustomize` e troca `revenda-principal:dev` por `revenda-principal:<sha>` |
| Ordem | 1) apaga o Job anterior; 2) aplica `k8s/migracao` e espera `Complete`; 3) aplica `k8s/base`; 4) `rollout status deployment/revenda-principal` |
| Container | `api` (usado por `kubectl set image`) |
