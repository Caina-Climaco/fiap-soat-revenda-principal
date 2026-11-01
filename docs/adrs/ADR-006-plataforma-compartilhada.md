# ADR-006: Plataforma local compartilhada, mantida pelo repositório principal

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

Na Fase 3, dois repositórios (API e identidade) compartilharam um cluster kind local, cada um com namespace, Terraform, state e runner de CD próprios. Na Fase 4 há de novo dois repositórios, agora com componentes que servem aos dois serviços: o API Gateway (Kong) e a observabilidade (Prometheus, Grafana, OpenTelemetry Collector e Jaeger). Alguém precisa ser dono deles. O enunciado pede dois repositórios, então não cabe um terceiro só de plataforma.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Principal é dono da plataforma comum; cada serviço é dono do próprio namespace** | Dois repositórios; o principal já trazia Kong e observabilidade na Fase 3 | As rotas do serviço de venda no Kong ficam no repositório principal |
| Terceiro repositório de plataforma | Separação mais limpa | Fora do pedido (dois repositórios) |
| Kong Ingress Controller, com cada serviço declarando as próprias rotas | Cada repositório dono das suas rotas | Mais um controlador e CRDs; muda o modelo DB-less validado na Fase 3 |

## Decisão

- **Cluster kind** `revenda` criado pela CLI `kind` a partir de um `cluster.yaml` idêntico nos dois repositórios; o CD de cada um cria o cluster se ele faltar (modelo da Fase 3).
- **Repositório principal** (Terraform, state `revenda-principal.tfstate`): namespaces `principal` (API, PostgreSQL, relay da outbox), `gateway` (Kong) e `observabilidade` (Prometheus, Grafana, OpenTelemetry Collector, Jaeger).
- **Repositório de venda** (Terraform, state `revenda-vendas.tfstate`): namespace `vendas` (API, MongoDB, CronJob de recuperação de sagas).
- **Contrato de plataforma**: as rotas públicas do serviço de venda no Kong apontam para o Service `revenda-vendas.vendas.svc.cluster.local`, nome publicado como contrato. O CD do serviço de venda confere, antes do deploy, que o Kong e o Collector respondem; sem eles, falha cedo pedindo para implantar o principal.
- Um runner self-hosted em container por repositório, como na Fase 3.

## Consequências

### Positivas
- Cada serviço é implantado e revertido de forma independente.
- Reuso direto da infraestrutura da Fase 3 (kind, Terraform, Kong DB-less, Prometheus, Grafana, runner em container).

### Negativas
- O primeiro deploy tem ordem: principal antes do serviço de venda.
- Uma nova rota pública no serviço de venda exige PR também no repositório principal.

## Mitigações
- A ordem está no README dos dois repositórios e é verificada pelo CD.
- As rotas do serviço de venda são poucas e estáveis (listagens, compras, consulta); mudança de rota é rara.
