variable "cluster_nome" {
  description = "Nome do cluster kind criado pela CLI (infra/kind/cluster.yaml); os providers usam o contexto kind-<nome>."
  type        = string
  default     = "revenda"
}

variable "kubeconfig_path" {
  description = "Kubeconfig com o contexto kind-<cluster_nome> (gravado pela CLI kind). Vazio = ~/.kube/config (no Windows, %USERPROFILE%\\.kube\\config)."
  type        = string
  default     = ""
}

variable "pod_subnet" {
  description = "Faixa de IPs dos pods no kind: deve ser igual a networking.podSubnet de infra/kind/cluster.yaml (usada nas NetworkPolicies)."
  type        = string
  default     = "10.244.0.0/16"
}

variable "expor_banco" {
  description = <<-EOT
    Publica o principal-db no host (localhost:15432 -> NodePort 30432) para a demonstracao
    do banco no video. O mapeamento de porta do kind (infra/kind/cluster.yaml) existe
    sempre; esta variavel so muda o tipo do Service e a regra extra da NetworkPolicy.
  EOT
  type        = bool
  default     = true
}

variable "namespace_vendas" {
  description = "Namespace do servico de venda (outro repositorio, ADR-006): origem permitida nas rotas internas e destino das rotas publicas de venda no Kong."
  type        = string
  default     = "vendas"
}

variable "postgres_imagem" {
  description = "Imagem oficial do PostgreSQL 16 (fixada por versao menor)."
  type        = string
  default     = "postgres:16.15-alpine"
}

variable "metrics_server_chart_versao" {
  description = "Versao do chart metrics-server (https://kubernetes-sigs.github.io/metrics-server/)."
  type        = string
  default     = "3.14.0"
}

variable "kong_imagem" {
  description = "Imagem oficial do Kong Gateway (OSS), modo DB-less."
  type        = string
  default     = "kong:3.9.3"
}

variable "kong_limite_geral_minuto" {
  description = "Rate limiting do Kong nas rotas publicas: requisicoes por minuto por IP."
  type        = number
  default     = 600
}

variable "kong_limite_compra_minuto" {
  description = "Rate limiting do Kong em POST /api/v1/compras: requisicoes por minuto por IP."
  type        = number
  default     = 60
}

variable "prometheus_imagem" {
  description = "Imagem oficial do Prometheus."
  type        = string
  default     = "prom/prometheus:v3.14.0"
}

variable "grafana_imagem" {
  description = "Imagem oficial do Grafana OSS."
  type        = string
  default     = "grafana/grafana:13.2.3"
}

variable "otel_collector_imagem" {
  description = "Imagem do OpenTelemetry Collector (distribuicao contrib, que traz o processador de atributos)."
  type        = string
  default     = "otel/opentelemetry-collector-contrib:0.162.0"
}

variable "jaeger_imagem" {
  description = "Imagem do Jaeger v2 (all-in-one, armazenamento em memoria)."
  type        = string
  default     = "jaegertracing/jaeger:2.21.0"
}
