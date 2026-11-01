# Nenhuma saida contem senha (nem como sensitive): os valores ficam so no state e nos
# Secrets do cluster. Para ler um segredo, use os comandos em `comandos_segredos`.

output "cluster" {
  description = "Nome do cluster kind e contexto do kubectl."
  value = {
    nome            = var.cluster_nome
    contexto        = local.kube_contexto
    kubeconfig_path = local.kubeconfig_path
    config_kind     = "infra/kind/cluster.yaml"
  }
}

output "urls" {
  description = "Enderecos locais (apenas 127.0.0.1)."
  value = {
    gateway        = "http://localhost:8080 (Kong; os dois servicos sao ClusterIP)"
    swagger        = "http://localhost:8080/principal/docs e http://localhost:8080/vendas/docs"
    grafana        = "http://localhost:3000"
    prometheus     = "http://localhost:9090"
    jaeger         = "http://localhost:16686"
    principal_db   = var.expor_banco ? "localhost:15432 (banco revenda_principal)" : "nao exposto (use kubectl exec)"
    servico_vendas = "repositorio fiap-soat-revenda-vendas (namespace ${var.namespace_vendas})"
  }
}

output "secrets" {
  description = "Secrets criados (namespace/nome => chaves). Contrato com a aplicacao, o CD e o repositorio de venda."
  # Lista estatica: referenciar `data` dos Secrets tornaria a saida sensivel.
  value = {
    "principal/principal-db-credentials" = ["DB_USER", "DB_PASSWORD", "DB_NAME"]
    "principal/principal-webhook"        = ["WEBHOOK_SECRET"]
    "principal/principal-tokens-servico" = ["TOKEN_SERVICO_ENTRADA", "TOKEN_SERVICO_SAIDA"]
    "principal/contrato-tokens-servico"  = ["VENDAS_PARA_PRINCIPAL", "PRINCIPAL_PARA_VENDAS"]
    "gateway/consumidores-kong"          = ["GESTOR_APIKEY", "GATEWAY_PAGAMENTO_APIKEY"]
    "gateway/kong-config"                = ["kong.yml"]
    "observabilidade/grafana-admin"      = ["GF_SECURITY_ADMIN_USER", "GF_SECURITY_ADMIN_PASSWORD"]
  }
  depends_on = [
    kubernetes_secret_v1.principal_db_credentials,
    kubernetes_secret_v1.principal_webhook,
    kubernetes_secret_v1.principal_tokens_servico,
    kubernetes_secret_v1.contrato_tokens_servico,
    kubernetes_secret_v1.consumidores_kong,
    kubernetes_secret_v1.kong_config,
    kubernetes_secret_v1.grafana_admin,
  ]
}

output "comandos_segredos" {
  description = "Como ler os segredos com kubectl (Git Bash/Linux; no PowerShell use scripts/windows/segredos.ps1)."
  value = {
    chave_gestor            = "kubectl -n gateway get secret consumidores-kong -o jsonpath='{.data.GESTOR_APIKEY}' | base64 -d"
    chave_gateway_pagamento = "kubectl -n gateway get secret consumidores-kong -o jsonpath='{.data.GATEWAY_PAGAMENTO_APIKEY}' | base64 -d"
    webhook_secret          = "kubectl -n principal get secret principal-webhook -o jsonpath='{.data.WEBHOOK_SECRET}' | base64 -d"
    senha_grafana           = "kubectl -n observabilidade get secret grafana-admin -o jsonpath='{.data.GF_SECURITY_ADMIN_PASSWORD}' | base64 -d"
    senha_principal_db      = "kubectl -n principal get secret principal-db-credentials -o jsonpath='{.data.DB_PASSWORD}' | base64 -d"
  }
}

output "comandos_uteis" {
  description = "Atalhos para a demonstracao."
  value = {
    pods           = "kubectl get pods -A -l app.kubernetes.io/part-of=revenda-veiculos -o wide"
    psql_principal = "kubectl -n principal exec -it statefulset/principal-db -- psql -U revenda -d revenda_principal"
    hpa            = "kubectl -n principal get hpa revenda-principal"
    kong           = "kubectl -n gateway logs deployment/kong --tail=50"
    alvos          = "http://localhost:9090/targets"
    traces         = "http://localhost:16686/search?service=kong"
  }
}
